"""
EWY IBS + 跌幅反弹策略 — qbot Strategy 实现。

两个子策略:
  - IBS 均值回归: Buy IBS<0.2 & Close>MA200, Sell IBS>0.8 or max_hold 10天
  - 跌幅反弹: Buy 盘中跌>3%, Sell 反弹+2.5% or max_hold 3天

数据源: ewy_minute_data.csv → 日线构建
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from qbot.strategy_base import Strategy
from qbot.models import Signal, OrderSuggestion, CheckResult
from qbot import config, db
from qbot.log_util import get_logger

log = get_logger("ewy_strategy", "EWY")

# 默认参数（可被 ~/.trading_config.json 覆盖）
DEFAULTS = {
    "quantity": 100,
    "ma_period": 200,
    "ibs_buy": 0.2,
    "ibs_sell": 0.8,
    "max_hold": 10,
    "drop_entry": -0.03,
    "drop_exit": 0.025,
    "drop_max_hold": 3,
    "circuit_breaker_losses": 3,
}

DATA_CSV = Path(__file__).parent / "ewy_minute_data.csv"
STATE_FILE = Path(__file__).parent / "ewy_signal_state.json"


class EWYStrategy(Strategy):
    """EWY IBS + 跌幅反弹策略。"""

    name = "EWY"
    symbols = ["EWY"]

    def __init__(self, do_update: bool = True):
        self.do_update = do_update

        # 合并配置
        cfg = config.strategy_params("EWY_IBS") or {}
        self.params = {**DEFAULTS, **cfg}

        log.info(f"EWY params: qty={self.params['quantity']}, "
                 f"IBS buy<={self.params['ibs_buy']}, sell>={self.params['ibs_sell']}, "
                 f"max_hold={self.params['max_hold']}, "
                 f"drop_entry={self.params['drop_entry']}, drop_exit={self.params['drop_exit']}")

        self._state = None
        self._daily = None

    # ── 数据 ──────────────────────────────────────────

    def _load_state(self):
        import json
        if STATE_FILE.exists():
            self._state = json.loads(STATE_FILE.read_text())
        else:
            self._state = {
                "ibs_position": None,
                "drop_position": None,
                "trade_log": [],
                "consecutive_exp_losses": 0,
                "circuit_breaker": False,
                "last_processed_date": None,
            }

    def _save_state(self):
        import json
        STATE_FILE.write_text(json.dumps(self._state, indent=2, ensure_ascii=False))

    def _update_data(self):
        """从 Yahoo Finance 更新今日分钟数据。"""
        import yfinance as yf
        log.info("Updating EWY minute data from Yahoo...")
        data = yf.download("EWY", period="5d", interval="1m", progress=False)
        if data.empty:
            log.warning("No data from Yahoo")
            return False

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data.index = data.index.tz_convert('US/Eastern').tz_localize(None)
        data.index.name = 'timestamp'
        data = data[['Open', 'High', 'Low', 'Close', 'Volume']]

        hist = pd.read_csv(DATA_CSV, index_col='timestamp', parse_dates=True)
        combined = pd.concat([hist, data])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined.sort_index(inplace=True)
        combined.to_csv(DATA_CSV)
        log.info(f"Data updated: {len(combined)} rows")
        return True

    def _build_daily(self):
        """从分钟数据构建日线。"""
        df = pd.read_csv(DATA_CSV, parse_dates=['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        df['date'] = df['timestamp'].dt.date

        daily = df.groupby('date').agg(
            Open=('Open', 'first'), High=('High', 'max'),
            Low=('Low', 'min'), Close=('Close', 'last'), Vol=('Volume', 'sum')
        ).reset_index()
        daily = daily.sort_values('date').reset_index(drop=True)
        daily['date'] = pd.to_datetime(daily['date'])

        ma = self.params['ma_period']
        daily['ma200'] = daily['Close'].rolling(ma).mean()
        daily['IBS'] = (daily['Close'] - daily['Low']) / (daily['High'] - daily['Low'])
        daily['prev_close'] = daily['Close'].shift(1)

        self._daily = daily

    def _load_data(self):
        if self._daily is not None:
            return

        if self.do_update:
            self._update_data()

        self._load_state()
        self._build_daily()

    # ── 信号检查 ──────────────────────────────────────────

    def check_signals(self, market_data: dict) -> list[Signal]:
        self._load_data()

        today = self._daily.iloc[-1]
        yesterday = self._daily.iloc[-2] if len(self._daily) > 1 else None
        date_str = str(today['date'].date())
        close = float(today['Close'])
        ibs = float(today['IBS']) if pd.notna(today['IBS']) else float('nan')
        ma200 = float(today['ma200']) if pd.notna(today['ma200']) else None

        # 去重
        if self._state.get("last_processed_date") == date_str:
            log.info(f"Date {date_str} already processed, checking positions only")
            return self._check_holding_status(today, date_str)

        log.info(f"Date: {date_str}, Close: ${close:.2f}, IBS: {ibs:.3f}, "
                 f"MA200: ${ma200:.2f}" if ma200 else f"Date: {date_str}, Close: ${close:.2f}, IBS: {ibs:.3f}")

        signals = []

        # 熔断检查
        self._check_circuit_breaker(today)

        # IBS 策略
        signals.extend(self._check_ibs(today, date_str, close, ibs, ma200))

        # 跌幅策略
        if yesterday is not None:
            signals.extend(self._check_drop(today, yesterday, date_str, close))

        self._state["last_processed_date"] = date_str
        self._save_state()

        return signals

    def _check_circuit_breaker(self, today):
        """检查/更新熔断状态。"""
        p = self.params
        close = float(today['Close'])
        ma200 = float(today['ma200']) if pd.notna(today['ma200']) else None

        if self._state["consecutive_exp_losses"] >= p["circuit_breaker_losses"]:
            self._state["circuit_breaker"] = True
            log.warning(f"Circuit breaker: {self._state['consecutive_exp_losses']} consecutive losses")

        if ma200 and close < ma200:
            self._state["circuit_breaker"] = True
            log.warning(f"Circuit breaker: EWY ${close:.2f} < MA200 ${ma200:.2f}")

        # 恢复条件
        if (self._state["circuit_breaker"]
                and self._state["consecutive_exp_losses"] < p["circuit_breaker_losses"]
                and ma200 and close > ma200):
            self._state["circuit_breaker"] = False
            log.info("Circuit breaker cleared")

    def _check_ibs(self, today, date_str, close, ibs, ma200) -> list[Signal]:
        """IBS 子策略信号检查。"""
        p = self.params
        signals = []

        if self._state["ibs_position"]:
            pos = self._state["ibs_position"]
            pos["days_held"] += 1
            hold_ret = close / pos["buy_price"] - 1

            if pd.notna(ibs) and ibs > p["ibs_sell"]:
                # IBS 达标卖出
                signals.append(Signal(
                    strategy="EWY_IBS", symbol="EWY", direction="SELL",
                    data={
                        "sub_strategy": "IBS",
                        "ibs": round(ibs, 4), "close": close,
                        "entry_price": pos["buy_price"], "entry_date": pos["buy_date"],
                        "days_held": pos["days_held"],
                        "hold_return": round(hold_ret * 100, 2),
                        "reason": f"IBS={ibs:.3f} > {p['ibs_sell']}",
                    }))
                self._record_trade(pos, date_str, close, "IBS>0.8", hold_ret)
                self._state["ibs_position"] = None
                self._close_db_position("EWY_IBS", "EWY")
                if hold_ret > 0:
                    self._state["consecutive_exp_losses"] = 0

            elif pos["days_held"] >= p["max_hold"]:
                # 到期卖出
                signals.append(Signal(
                    strategy="EWY_IBS", symbol="EWY", direction="SELL",
                    data={
                        "sub_strategy": "IBS",
                        "ibs": round(ibs, 4) if pd.notna(ibs) else None,
                        "close": close,
                        "entry_price": pos["buy_price"], "entry_date": pos["buy_date"],
                        "days_held": pos["days_held"],
                        "hold_return": round(hold_ret * 100, 2),
                        "reason": f"Max hold {p['max_hold']} days",
                    }))
                self._record_trade(pos, date_str, close, "EXP", hold_ret)
                self._state["ibs_position"] = None
                self._close_db_position("EWY_IBS", "EWY")
                if hold_ret < 0:
                    self._state["consecutive_exp_losses"] += 1
                else:
                    self._state["consecutive_exp_losses"] = 0
            else:
                log.info(f"IBS holding: day {pos['days_held']}/{p['max_hold']}, "
                         f"return={hold_ret*100:+.2f}%")
        else:
            # 入场检查
            above_ma = ma200 is not None and close > ma200
            ibs_low = pd.notna(ibs) and ibs < p["ibs_buy"]

            if ibs_low and above_ma and not self._state["circuit_breaker"]:
                signals.append(Signal(
                    strategy="EWY_IBS", symbol="EWY", direction="BUY",
                    data={
                        "sub_strategy": "IBS",
                        "ibs": round(ibs, 4), "close": close,
                        "ma200": round(ma200, 2),
                        "max_hold": p["max_hold"],
                        "exit_ibs": p["ibs_sell"],
                        "reason": f"IBS={ibs:.3f} < {p['ibs_buy']}, Close > MA200",
                    }))
                self._state["ibs_position"] = {
                    "buy_date": date_str,
                    "buy_price": round(close, 2),
                    "days_held": 0,
                }
                from qbot import db
                db.open_position("EWY_IBS", "EWY", p.get("quantity", 100),
                                 round(close, 2), date_str)
            else:
                reasons = []
                if not ibs_low:
                    reasons.append(f"IBS={ibs:.3f}")
                if not above_ma:
                    reasons.append("Close<MA200" if ma200 else "MA200 N/A")
                if self._state["circuit_breaker"]:
                    reasons.append("circuit breaker")
                log.info(f"IBS no entry ({', '.join(reasons)})")

        return signals

    def _check_drop(self, today, yesterday, date_str, close) -> list[Signal]:
        """跌幅反弹子策略信号检查。"""
        p = self.params
        signals = []

        if self._state["drop_position"]:
            pos = self._state["drop_position"]
            pos["days_held"] += 1
            hold_ret = close / pos["buy_price"] - 1

            if hold_ret >= p["drop_exit"]:
                # 反弹达标
                sell_price = pos["buy_price"] * (1 + p["drop_exit"])
                signals.append(Signal(
                    strategy="EWY_DROP", symbol="EWY", direction="SELL",
                    data={
                        "sub_strategy": "DROP",
                        "close": close, "sell_price": round(sell_price, 2),
                        "entry_price": pos["buy_price"], "entry_date": pos["buy_date"],
                        "days_held": pos["days_held"],
                        "hold_return": round(p["drop_exit"] * 100, 2),
                        "reason": f"Rebound +{p['drop_exit']*100:.1f}%",
                    }))
                self._record_trade(pos, date_str, sell_price, "TP", p["drop_exit"])
                self._state["drop_position"] = None
                self._close_db_position("EWY_DROP", "EWY")
                self._state["consecutive_exp_losses"] = 0

            elif pos["days_held"] >= p["drop_max_hold"]:
                # 到期卖出
                signals.append(Signal(
                    strategy="EWY_DROP", symbol="EWY", direction="SELL",
                    data={
                        "sub_strategy": "DROP",
                        "close": close,
                        "entry_price": pos["buy_price"], "entry_date": pos["buy_date"],
                        "days_held": pos["days_held"],
                        "hold_return": round(hold_ret * 100, 2),
                        "reason": f"Max hold {p['drop_max_hold']} days",
                    }))
                self._record_trade(pos, date_str, close, "EXP", hold_ret)
                self._state["drop_position"] = None
                self._close_db_position("EWY_DROP", "EWY")
                if hold_ret < 0:
                    self._state["consecutive_exp_losses"] += 1
                else:
                    self._state["consecutive_exp_losses"] = 0
            else:
                log.info(f"DROP holding: day {pos['days_held']}/{p['drop_max_hold']}, "
                         f"return={hold_ret*100:+.2f}%")
        else:
            # 跌幅入场检查
            prev_close = float(yesterday['Close'])
            intraday_drop = float(today['Low']) / prev_close - 1

            if intraday_drop <= p["drop_entry"] and not self._state["circuit_breaker"]:
                buy_price = prev_close * (1 + p["drop_entry"])
                signals.append(Signal(
                    strategy="EWY_DROP", symbol="EWY", direction="BUY",
                    data={
                        "sub_strategy": "DROP",
                        "close": close,
                        "buy_price": round(buy_price, 2),
                        "intraday_drop": round(intraday_drop * 100, 2),
                        "drop_threshold": p["drop_entry"] * 100,
                        "exit_target": p["drop_exit"] * 100,
                        "max_hold": p["drop_max_hold"],
                        "reason": f"Intraday drop {intraday_drop*100:.1f}% > {p['drop_entry']*100:.0f}%",
                    }))
                self._state["drop_position"] = {
                    "buy_date": date_str,
                    "buy_price": round(buy_price, 2),
                    "days_held": 0,
                }
                from qbot import db
                db.open_position("EWY_DROP", "EWY", p.get("quantity", 100),
                                 round(buy_price, 2), date_str)
            else:
                if self._state["circuit_breaker"]:
                    log.info("DROP no entry (circuit breaker)")
                else:
                    log.info(f"DROP no entry (drop={intraday_drop*100:+.1f}%, "
                             f"threshold={p['drop_entry']*100:.0f}%)")

        return signals

    def _check_holding_status(self, today, date_str) -> list[Signal]:
        """日期已处理，仅报告持仓状态。"""
        close = float(today['Close'])
        for name, pos in [("IBS", self._state.get("ibs_position")),
                          ("DROP", self._state.get("drop_position"))]:
            if pos:
                ret = (close / pos["buy_price"] - 1) * 100
                log.info(f"{name}: holding since {pos['buy_date']} "
                         f"(day {pos['days_held']}, return={ret:+.2f}%)")
        return []

    def _record_trade(self, pos, sell_date, sell_price, reason, ret):
        """记录交易到 state。"""
        self._state["trade_log"].append({
            "buy_date": pos["buy_date"],
            "sell_date": sell_date,
            "buy_price": pos["buy_price"],
            "sell_price": round(sell_price, 2),
            "days": pos["days_held"],
            "ret": round(ret * 100, 2),
            "reason": reason,
        })

    @staticmethod
    def _close_db_position(strategy: str, symbol: str):
        """关闭 qbot DB 中的持仓。"""
        from qbot import db
        for p in db.get_open_positions(strategy=strategy, symbol=symbol):
            db.close_position(p["id"])

    # ── 订单设计 ──────────────────────────────────────────

    def design_orders(self, signal: Signal) -> list[OrderSuggestion]:
        qty = self.params["quantity"]
        data = signal.data

        if signal.direction == "BUY":
            sub = data.get("sub_strategy", "IBS")
            if sub == "DROP":
                price = data.get("buy_price", data.get("close", 0))
                notes = (f"跌幅反弹策略\n"
                         f"盘中跌幅: {data.get('intraday_drop', '?')}%\n"
                         f"反弹目标: +{data.get('exit_target', '?')}%\n"
                         f"最大持有: {data.get('max_hold', '?')} 天")
            else:
                price = round(data.get("close", 0) + 0.05, 2)
                notes = (f"IBS 均值回归\n"
                         f"IBS: {data.get('ibs', '?')} (阈值: <{self.params['ibs_buy']})\n"
                         f"MA200: ${data.get('ma200', '?')}\n"
                         f"最大持有: {data.get('max_hold', '?')} 天\n"
                         f"退出: IBS >= {data.get('exit_ibs', '?')}")

            return [OrderSuggestion(
                symbol="EWY", side="BUY", quantity=qty,
                order_type="LIMIT", suggested_price=price, notes=notes)]
        else:
            price = data.get("sell_price", data.get("close", 0))
            entry_price = data.get("entry_price", 0)
            return [OrderSuggestion(
                symbol="EWY", side="SELL", quantity=qty,
                order_type="LIMIT", suggested_price=round(price, 2),
                notes=(f"持仓: {data.get('entry_date', '?')} @ ${entry_price:.2f}\n"
                       f"持有: {data.get('days_held', '?')} 天\n"
                       f"预估收益: {data.get('hold_return', 0):+.2f}%\n"
                       f"退出原因: {data.get('reason', 'N/A')}"))]

    # ── 安全检查 ──────────────────────────────────────────

    def safety_checks(self, signal: Signal) -> list[CheckResult]:
        checks = []

        if signal.direction == "BUY":
            # 熔断器
            if self._state.get("circuit_breaker"):
                checks.append(CheckResult("circuit_breaker", False,
                                          f"Circuit breaker active (losses={self._state['consecutive_exp_losses']})"))
            else:
                checks.append(CheckResult("circuit_breaker", True, "No circuit breaker"))

            # 重复持仓检查
            sub = signal.data.get("sub_strategy", "IBS")
            key = "ibs_position" if sub == "IBS" else "drop_position"
            if self._state.get(key):
                checks.append(CheckResult("no_existing_position", False,
                                          f"Already in {sub} position"))
            else:
                checks.append(CheckResult("no_existing_position", True,
                                          f"No existing {sub} position"))
        else:
            checks.append(CheckResult("exit_valid", True,
                                      signal.data.get("reason", "Exit condition met")))

        return checks
