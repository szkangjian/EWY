#!/usr/bin/env python3
"""
EWY 盘中监控 — 常驻 launchd 进程，每 60s 跑一次 run()。

职责:
  1. 跌幅入场 (-3% intraday)        → dispatch_signal BUY  → Alpaca paper
  2. 反弹 TP (+2.5% vs buy_price)   → dispatch_signal SELL → Alpaca paper
  3. 收盘前 IBS 入场 (IBS<0.2)      → dispatch_signal BUY  → Alpaca paper
  4. 收盘前 IBS 出场 (IBS>0.8/到期) → dispatch_signal SELL → Alpaca paper

所有下单决策统一走 signal_bus.dispatch_signal()，由它负责:
  - DB 信号/订单落库
  - 持仓 open/close
  - Telegram 通知
  - broker 路由（paper / live 一键切换）
  - 同日去重（防止与 ewy_orchestrator 重复下单）

本模块只保留"监控侧"状态:
  - ewy_signal_state.json: 本模块内部状态（日内 tick 去重 / 反弹目标计算用）
  - .ewy_intraday_alerts.json: 日内 Telegram 去重（一天内同档位/同类信号只发一次）
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from qbot import config, signal_bus
from qbot.data_feed import get_quote
from qbot.log_util import get_logger
from qbot.models import OrderSuggestion, Signal
from qbot.time_util import today_market_str

log = get_logger("intraday_monitor", "EWY")

DATA_CSV = Path(__file__).parent / "ewy_minute_data.csv"
STATE_FILE = Path(__file__).parent / "ewy_signal_state.json"
ALERT_FILE = Path(__file__).parent / ".ewy_intraday_alerts.json"
MARKET_TZ = ZoneInfo("US/Eastern")

cfg = config.strategy_params("EWY_IBS") or {}
DROP_ENTRY = cfg.get("drop_entry", -0.045)
DROP_EXIT = cfg.get("drop_exit", 0.025)
DROP_MAX_HOLD = cfg.get("drop_max_hold", 5)


# ── Prev close / state / alert 文件 ───────────────────────────

def load_prev_close() -> float | None:
    """从 Yahoo Finance 获取前一交易日收盘价，回退到本地 CSV。"""
    try:
        import yfinance as yf
        t = yf.Ticker("EWY")
        prev = t.fast_info.get("previousClose") or t.fast_info.get("regularMarketPreviousClose")
        if prev:
            return float(prev)
    except Exception as e:
        log.error(f"Yahoo prev close failed: {e}")
    try:
        daily = load_daily_bars(str(DATA_CSV))
        if len(daily) >= 1:
            return float(daily.iloc[-1]['Close'])
    except Exception as e:
        log.error(f"CSV fallback also failed: {e}")
    return None


def load_alerts() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if ALERT_FILE.exists():
            data = json.loads(ALERT_FILE.read_text())
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "drop_levels": [], "rebound_alerted": False}


def save_alerts(alerts: dict):
    ALERT_FILE.write_text(json.dumps(alerts, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "ibs_position": None,
        "drop_position": None,
        "trade_log": [],
        "consecutive_exp_losses": 0,
        "circuit_breaker": False,
    }


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ── 核心 run() ────────────────────────────────────────────────

def run():
    today = datetime.now().strftime("%Y-%m-%d")
    market_date = today_market_str()
    log.info(f"=== EWY Intraday Check {datetime.now().strftime('%H:%M')} ===")

    prev_close = load_prev_close()
    if prev_close is None:
        log.error("Cannot load previous close")
        return

    quote = get_quote("EWY")
    if quote is None:
        log.error("Cannot get EWY quote")
        return

    price = quote.price
    drop_pct = (price / prev_close - 1)
    log.info(f"EWY: ${price:.2f} vs prev close ${prev_close:.2f} = {drop_pct*100:+.2f}%")

    alerts = load_alerts()
    state = load_state()

    # ---- 跌幅买入（DROP BUY）----
    if drop_pct <= DROP_ENTRY and not state.get("circuit_breaker"):
        _handle_drop_entry(price, prev_close, drop_pct, market_date, today,
                           state, alerts)

    # ---- 持仓反弹 TP（DROP SELL）----
    if state.get("drop_position") and not alerts.get("rebound_alerted"):
        _handle_drop_exit(price, market_date, today, state, alerts)

    # ---- IBS 持仓心跳日志 ----
    if state.get("ibs_position"):
        pos = state["ibs_position"]
        ret = (price / pos["buy_price"] - 1)
        log.info(f"IBS position: {pos['buy_date']} @ ${pos['buy_price']:.2f}, "
                 f"current return={ret*100:+.2f}%")

    # ---- IBS 收盘前信号（15:45 ET 之后）----
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.time() >= dtime(15, 45):
        _check_ibs_pre_close(price, prev_close, state, alerts, market_date, today)

    save_alerts(alerts)


# ── 四个决策点 ────────────────────────────────────────────────

def _handle_drop_entry(price: float, prev_close: float, drop_pct: float,
                       market_date: str, today: str, state: dict, alerts: dict):
    """跌幅入场：第一次触发 → dispatch_signal BUY；后续档位只发加深告警。"""
    qty = cfg.get("quantity", 100)
    target_price = price * (1 + DROP_EXIT)
    level = math.floor(drop_pct * 100)  # -3, -5, -7 ...

    if not state.get("drop_position"):
        # 首次触发 → 下单
        msg = (
            f"🔴 <b>EWY 跌幅买入信号</b>\n\n"
            f"👉 买入 {qty} 股 EWY @ <b>${price:.2f}</b>（市价）\n"
            f"已跌 {abs(drop_pct)*100:.1f}%（前日收盘 ${prev_close:.2f}）\n\n"
            f"反弹目标: ${target_price:.2f} (+{DROP_EXIT*100:.1f}%)\n"
            f"最大持有: {cfg.get('drop_max_hold', 3)} 天\n\n"
            f"⏰ {datetime.now().strftime('%H:%M ET')}"
        )
        signal = Signal(
            strategy="EWY_DROP", symbol="EWY", direction="BUY",
            data={
                "sub_strategy": "DROP",
                "entry_price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "drop_pct": round(drop_pct * 100, 2),
                "target_price": round(target_price, 2),
                "reason": f"intraday drop {drop_pct*100:.2f}% <= {DROP_ENTRY*100:.0f}%",
            },
        )
        order = OrderSuggestion(
            symbol="EWY", side="BUY", quantity=qty,
            order_type="MARKET", suggested_price=round(price, 2),
            notes=f"intraday drop entry, target ${target_price:.2f}",
        )
        r = signal_bus.dispatch_signal(signal, [order], notification=msg,
                                       entry_date=market_date)

        if r["skipped"] == "already_opened_today":
            # orchestrator 或更早一次 tick 已开仓 → 把本地 state 同步上
            log.info(f"DROP: position opened elsewhere today, syncing local state")
            state["drop_position"] = {
                "buy_date": today, "buy_price": round(price, 2), "days_held": 0,
            }
        elif r["position_ids"]:
            state["drop_position"] = {
                "buy_date": today, "buy_price": round(price, 2), "days_held": 0,
            }
            log.info(f"DROP entry dispatched: pos_id={r['position_ids'][0]}")
        save_state(state)
        alerts["drop_levels"].append(level)
        return

    # 已持仓：只发"跌幅加深"告警，不再下单
    if level not in alerts["drop_levels"]:
        alerts["drop_levels"].append(level)
        from qbot import notifier
        buy_price = state["drop_position"]["buy_price"]
        msg = (
            f"⚠️ <b>EWY 跌幅加深到 {level}%</b>\n\n"
            f"当前: ${price:.2f}（已持仓 ${buy_price:.2f}）\n"
            f"⏰ {datetime.now().strftime('%H:%M ET')}"
        )
        notifier.send_text(msg)
        log.info(f"DROP deepening alert: level={level}%")


def _handle_drop_exit(price: float, market_date: str, today: str,
                      state: dict, alerts: dict):
    """反弹 TP 出场。"""
    pos = state["drop_position"]
    buy_price = pos["buy_price"]
    rebound = (price / buy_price - 1)
    if rebound < DROP_EXIT:
        return

    qty = cfg.get("quantity", 100)
    sell_price = buy_price * (1 + DROP_EXIT)
    msg = (
        f"🟢 <b>EWY 反弹达标!</b>\n\n"
        f"👉 卖出 {qty} 股 EWY（{pos['buy_date']} 买入 @ ${buy_price:.2f}）\n\n"
        f"当前: <b>${price:.2f}</b>\n"
        f"收益: {rebound*100:+.1f}%（目标 +{DROP_EXIT*100:.1f}%）\n\n"
        f"⏰ {datetime.now().strftime('%H:%M ET')}"
    )
    signal = Signal(
        strategy="EWY_DROP", symbol="EWY", direction="SELL",
        data={
            "sub_strategy": "DROP",
            "exit_price": round(price, 2),
            "buy_price": buy_price,
            "hold_return": round(rebound * 100, 2),
            "reason": f"Rebound +{rebound*100:.2f}% >= {DROP_EXIT*100:.1f}%",
        },
    )
    order = OrderSuggestion(
        symbol="EWY", side="SELL", quantity=qty,
        order_type="MARKET", suggested_price=round(price, 2),
        notes="intraday DROP rebound TP",
    )
    r = signal_bus.dispatch_signal(signal, [order], notification=msg,
                                   entry_date=market_date)

    alerts["rebound_alerted"] = True
    if r["skipped"] == "no_position_to_close":
        log.info("DROP exit: no DB position to close (state drifted)")
    state["trade_log"].append({
        "buy_date": pos["buy_date"], "sell_date": today,
        "buy_price": buy_price, "sell_price": round(sell_price, 2),
        "days": pos["days_held"], "ret": round(DROP_EXIT * 100, 2),
        "reason": "TP",
    })
    state["drop_position"] = None
    state["consecutive_exp_losses"] = 0
    save_state(state)
    log.info(f"DROP exit dispatched: rebound={rebound*100:+.1f}%, "
             f"closed={r['closed_position_ids']}")


def _check_ibs_pre_close(price: float, prev_close: float, state: dict,
                         alerts: dict, market_date: str, today: str):
    """收盘前 15 分钟 IBS 入/出场。"""
    if alerts.get("ibs_alerted"):
        return

    ibs_sell = cfg.get("ibs_sell", 0.8)
    ibs_buy = cfg.get("ibs_buy", 0.2)
    max_hold = cfg.get("max_hold", 10)

    try:
        import yfinance as yf
        info = yf.Ticker("EWY").fast_info
        day_high = float(info.get("dayHigh", 0))
        day_low = float(info.get("dayLow", 0))
    except Exception as e:
        log.error(f"Cannot get intraday H/L: {e}")
        return

    if day_high <= day_low or day_high == 0:
        return

    ibs = (price - day_low) / (day_high - day_low)
    log.info(f"Pre-close IBS: {ibs:.4f} (H={day_high:.2f} L={day_low:.2f} C~{price:.2f})")

    if state.get("ibs_position"):
        _handle_ibs_exit(price, ibs, ibs_sell, max_hold, market_date, today,
                         state, alerts)
    else:
        _handle_ibs_entry(price, ibs, ibs_buy, ibs_sell, max_hold,
                          market_date, today, state, alerts)


def _handle_ibs_exit(price: float, ibs: float, ibs_sell: float, max_hold: int,
                     market_date: str, today: str, state: dict, alerts: dict):
    pos = state["ibs_position"]
    ret = (price / pos["buy_price"] - 1)
    days = pos["days_held"] + 1

    if not (ibs >= ibs_sell or days >= max_hold):
        return

    reason = f"IBS={ibs:.3f} >= {ibs_sell}" if ibs >= ibs_sell else f"持有 {days} 天到期"
    qty = cfg.get("quantity", 100)
    msg = (
        f"🟢 <b>EWY IBS 卖出信号</b>\n\n"
        f"👉 卖出 {qty} 股 EWY @ <b>${price:.2f}</b>\n"
        f"买入: {pos['buy_date']} @ ${pos['buy_price']:.2f}\n"
        f"收益: {ret*100:+.1f}%，持有 {days} 天\n"
        f"原因: {reason}\n\n"
        f"⏰ 收盘前 15 分钟"
    )
    signal = Signal(
        strategy="EWY_IBS", symbol="EWY", direction="SELL",
        data={
            "sub_strategy": "IBS",
            "exit_price": round(price, 2),
            "buy_price": pos["buy_price"],
            "ibs": round(ibs, 4),
            "days": days,
            "hold_return": round(ret * 100, 2),
            "reason": "IBS" if ibs >= ibs_sell else "EXP",
        },
    )
    order = OrderSuggestion(
        symbol="EWY", side="SELL", quantity=qty,
        order_type="MARKET", suggested_price=round(price, 2),
        notes=f"IBS exit: {reason}",
    )
    r = signal_bus.dispatch_signal(signal, [order], notification=msg,
                                   entry_date=market_date)

    alerts["ibs_alerted"] = True
    state["trade_log"].append({
        "buy_date": pos["buy_date"], "sell_date": today,
        "buy_price": pos["buy_price"], "sell_price": round(price, 2),
        "days": days, "ret": round(ret * 100, 2),
        "reason": "IBS" if ibs >= ibs_sell else "EXP",
    })
    state["ibs_position"] = None
    if ret < 0 and days >= max_hold:
        state["consecutive_exp_losses"] = state.get("consecutive_exp_losses", 0) + 1
    else:
        state["consecutive_exp_losses"] = 0
    save_state(state)
    log.info(f"IBS exit dispatched: {reason}, closed={r['closed_position_ids']}")


def _handle_ibs_entry(price: float, ibs: float, ibs_buy: float, ibs_sell: float,
                      max_hold: int, market_date: str, today: str,
                      state: dict, alerts: dict):
    # MA200 过滤
    try:
        import yfinance as yf
        data = yf.download("EWY", period="1y", interval="1d", progress=False)
        ma200 = float(data["Close"].rolling(200).mean().iloc[-1])
    except Exception:
        ma200 = 0

    above_ma = ma200 > 0 and price > ma200
    if not (ibs < ibs_buy and above_ma and not state.get("circuit_breaker")):
        reasons = []
        if ibs >= ibs_buy:
            reasons.append(f"IBS={ibs:.3f}")
        if not above_ma:
            reasons.append(f"Close<MA200" if ma200 > 0 else "MA200 N/A")
        if state.get("circuit_breaker"):
            reasons.append("circuit breaker")
        if reasons:
            log.info(f"IBS no entry ({', '.join(reasons)})")
        return

    qty = cfg.get("quantity", 100)
    msg = (
        f"🔵 <b>EWY IBS 买入信号</b>\n\n"
        f"👉 买入 {qty} 股 EWY @ <b>${price:.2f}</b>\n"
        f"IBS = {ibs:.4f}（阈值 &lt; {ibs_buy}）\n"
        f"MA200 = ${ma200:.2f} ✅\n\n"
        f"卖出条件: IBS &gt; {ibs_sell} 或持有 {max_hold} 天\n\n"
        f"⏰ 收盘前 15 分钟"
    )
    signal = Signal(
        strategy="EWY_IBS", symbol="EWY", direction="BUY",
        data={
            "sub_strategy": "IBS",
            "entry_price": round(price, 2),
            "ibs": round(ibs, 4),
            "ma200": round(ma200, 2),
            "exit_ibs": ibs_sell,
            "max_hold": max_hold,
            "reason": f"IBS={ibs:.3f} < {ibs_buy}, Close > MA200",
        },
    )
    order = OrderSuggestion(
        symbol="EWY", side="BUY", quantity=qty,
        order_type="MARKET", suggested_price=round(price, 2),
        notes=f"IBS entry, exit>{ibs_sell} or {max_hold}d",
    )
    r = signal_bus.dispatch_signal(signal, [order], notification=msg,
                                   entry_date=market_date)

    alerts["ibs_alerted"] = True
    if r["skipped"] == "already_opened_today":
        log.info("IBS: position opened elsewhere today, syncing local state")
        state["ibs_position"] = {
            "buy_date": today, "buy_price": round(price, 2), "days_held": 0,
        }
    elif r["position_ids"]:
        state["ibs_position"] = {
            "buy_date": today, "buy_price": round(price, 2), "days_held": 0,
        }
        log.info(f"IBS entry dispatched: pos_id={r['position_ids'][0]}")
    save_state(state)


def is_market_hours() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 30) <= t <= dtime(16, 0)


if __name__ == "__main__":
    import time

    if "--once" in sys.argv:
        run()
    else:
        log.info("Starting continuous monitor (60s interval)")
        while True:
            if is_market_hours():
                try:
                    run()
                except Exception as e:
                    log.error(f"Run error: {e}")
            time.sleep(60)
