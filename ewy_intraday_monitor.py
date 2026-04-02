#!/usr/bin/env python3
"""
EWY 盘中跌幅监控 — launchd 每 15 分钟运行。

检查 EWY 相对前日收盘的跌幅，触发阈值后发 Telegram 通知。
避免重复通知：同一天同一档位只通知一次（通过 state 文件记录）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from qbot import config, notifier
from qbot.data_feed import get_quote
from qbot.log_util import get_logger
from ewy_market_data import load_daily_bars

log = get_logger("intraday_monitor", "EWY")

DATA_CSV = Path(__file__).parent / "ewy_minute_data.csv"
STATE_FILE = Path(__file__).parent / "ewy_signal_state.json"
ALERT_FILE = Path(__file__).parent / ".ewy_intraday_alerts.json"
MARKET_TZ = ZoneInfo("US/Eastern")

# 参数
cfg = config.strategy_params("EWY_IBS") or {}
DROP_ENTRY = cfg.get("drop_entry", -0.045)
DROP_EXIT = cfg.get("drop_exit", 0.025)
DROP_MAX_HOLD = cfg.get("drop_max_hold", 5)


def load_prev_close() -> float | None:
    """从 Yahoo Finance 获取前一交易日收盘价。"""
    try:
        import yfinance as yf
        t = yf.Ticker("EWY")
        prev = t.fast_info.get("previousClose") or t.fast_info.get("regularMarketPreviousClose")
        if prev:
            return float(prev)
    except Exception as e:
        log.error(f"Yahoo prev close failed: {e}")
    # 回退到本地 CSV
    try:
        daily = load_daily_bars(str(DATA_CSV))
        if len(daily) >= 1:
            return float(daily.iloc[-1]['Close'])
    except Exception as e:
        log.error(f"CSV fallback also failed: {e}")
    return None


def load_alerts() -> dict:
    """加载今日已发送的告警记录。"""
    today = datetime.now(MARKET_TZ).strftime("%Y-%m-%d")
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


def run():
    now_et = datetime.now(MARKET_TZ)
    today = now_et.strftime("%Y-%m-%d")
    log.info(f"=== EWY Intraday Check {now_et.strftime('%H:%M ET')} ===")

    # 获取前日收盘
    prev_close = load_prev_close()
    if prev_close is None:
        log.error("Cannot load previous close")
        return

    # 获取当前价格
    quote = get_quote("EWY")
    if quote is None:
        log.error("Cannot get EWY quote")
        return

    price = quote.price
    drop_pct = (price / prev_close - 1)
    log.info(f"EWY: ${price:.2f} vs prev close ${prev_close:.2f} = {drop_pct*100:+.2f}%")

    alerts = load_alerts()
    state = load_state()

    # ---- 跌幅买入信号 ----
    if drop_pct <= DROP_ENTRY and not state.get("circuit_breaker"):
        target_price = price * (1 + DROP_EXIT)

        # 首次触发：记录持仓到 state（支持日内反弹监控）
        if not state.get("drop_position"):
            state["drop_position"] = {
                "buy_date": today,
                "buy_price": round(price, 2),
                "days_held": 0,
            }
            save_state(state)
            # 同步写入 qbot DB
            from qbot import db
            db.open_position("EWY_DROP", "EWY", cfg.get("quantity", 100),
                             round(price, 2), today)
            log.info(f"DROP position recorded: ${price:.2f}")

        # 按整数百分点分档通知，避免重复
        import math
        level = math.floor(drop_pct * 100)  # e.g., -3, -5, -7 (向下取整，避免边界重复)
        if level not in alerts["drop_levels"]:
            alerts["drop_levels"].append(level)

            qty = cfg.get("quantity", 100)
            msg = (
                f"🔴 <b>EWY 跌幅买入信号</b>\n\n"
                f"👉 买入 {qty} 股 EWY @ <b>${price:.2f}</b>（市价）\n"
                f"已跌 {abs(drop_pct)*100:.1f}%（前日收盘 ${prev_close:.2f}）\n\n"
                f"反弹目标: ${target_price:.2f} (+{DROP_EXIT*100:.1f}%)\n"
                f"最大持有: {DROP_MAX_HOLD} 天\n\n"
                f"⏰ {datetime.now(MARKET_TZ).strftime('%H:%M ET')}"
            )
            notifier.send_text(msg)
            log.info(f"DROP alert sent: level={level}%")

    # ---- 持仓反弹监控 ----
    if state.get("drop_position") and not alerts.get("rebound_alerted"):
        pos = state["drop_position"]
        buy_price = pos["buy_price"]
        rebound = (price / buy_price - 1)

        if rebound >= DROP_EXIT:
            alerts["rebound_alerted"] = True

            # 记录交易并清持仓
            sell_price = buy_price * (1 + DROP_EXIT)
            state["trade_log"].append({
                "buy_date": pos["buy_date"],
                "sell_date": today,
                "buy_price": buy_price,
                "sell_price": round(sell_price, 2),
                "days": pos["days_held"],
                "ret": round(DROP_EXIT * 100, 2),
                "reason": "TP",
            })
            state["drop_position"] = None
            state["consecutive_exp_losses"] = 0
            save_state(state)
            # 同步关闭 qbot DB 持仓
            from qbot import db
            for p in db.get_open_positions(strategy="EWY_DROP", symbol="EWY"):
                db.close_position(p["id"])

            qty = cfg.get("quantity", 100)
            msg = (
                f"🟢 <b>EWY 反弹达标!</b>\n\n"
                f"👉 卖出 {qty} 股 EWY（{pos['buy_date']} 买入 @ ${buy_price:.2f}）\n\n"
                f"当前: <b>${price:.2f}</b>\n"
                f"收益: {rebound*100:+.1f}%（目标 +{DROP_EXIT*100:.1f}%）\n\n"
                f"建议立即卖出\n"
                f"⏰ {datetime.now(MARKET_TZ).strftime('%H:%M ET')}"
            )
            notifier.send_text(msg)
            log.info(f"Rebound alert sent: {rebound*100:+.1f}%, position closed")

    # ---- IBS 持仓监控 ----
    if state.get("ibs_position"):
        pos = state["ibs_position"]
        buy_price = pos["buy_price"]
        ret = (price / buy_price - 1)
        log.info(f"IBS position: {pos['buy_date']} @ ${buy_price:.2f}, "
                 f"current return={ret*100:+.2f}%")

    # ---- IBS 收盘前信号检查（3:45 PM ET 之后）----
    from datetime import time as dtime
    now_et = datetime.now(MARKET_TZ)

    if now_et.time() >= dtime(15, 45):
        _check_ibs_pre_close(price, prev_close, state, alerts, today)

    save_alerts(alerts)


def _check_ibs_pre_close(price: float, prev_close: float, state: dict, alerts: dict, today: str):
    """收盘前 15 分钟检查 IBS 信号。用盘中 H/L + 当前价近似 IBS。"""
    if alerts.get("ibs_alerted"):
        return  # 今天已通知过

    # 已有 IBS 持仓 → 检查是否该卖
    ibs_sell = cfg.get("ibs_sell", 0.8)
    ibs_buy = cfg.get("ibs_buy", 0.2)
    max_hold = cfg.get("max_hold", 10)

    # 获取今日盘中 High/Low
    try:
        import yfinance as yf
        t = yf.Ticker("EWY")
        info = t.fast_info
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
        pos = state["ibs_position"]
        ret = (price / pos["buy_price"] - 1)
        days = pos["days_held"] + 1  # 今天算一天

        if ibs >= ibs_sell or days >= max_hold:
            reason = f"IBS={ibs:.3f} >= {ibs_sell}" if ibs >= ibs_sell else f"持有 {days} 天到期"
            alerts["ibs_alerted"] = True
            qty = cfg.get("quantity", 100)
            msg = (
                f"🟢 <b>EWY IBS 卖出信号</b>\n\n"
                f"👉 卖出 {qty} 股 EWY @ <b>${price:.2f}</b>\n"
                f"买入: {pos['buy_date']} @ ${pos['buy_price']:.2f}\n"
                f"收益: {ret*100:+.1f}%，持有 {days} 天\n"
                f"原因: {reason}\n\n"
                f"⏰ 收盘前 15 分钟"
            )
            notifier.send_text(msg)

            # 平仓
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
            from qbot import db
            for p in db.get_open_positions(strategy="EWY_IBS", symbol="EWY"):
                db.close_position(p["id"])
            log.info(f"IBS sell signal: {reason}, position closed")
    else:
        # 无持仓 → 检查买入
        # MA200 过滤
        try:
            import yfinance as yf
            data = yf.download("EWY", period="1y", interval="1d", progress=False)
            ma200 = float(data["Close"].rolling(200).mean().iloc[-1])
        except Exception:
            ma200 = 0

        above_ma = ma200 > 0 and price > ma200

        if ibs < ibs_buy and above_ma and not state.get("circuit_breaker"):
            alerts["ibs_alerted"] = True
            qty = cfg.get("quantity", 100)
            msg = (
                f"🔵 <b>EWY IBS 买入信号</b>\n\n"
                f"👉 买入 {qty} 股 EWY @ <b>${price:.2f}</b>\n"
                f"IBS = {ibs:.4f}（阈值 &lt; {ibs_buy}）\n"
                f"MA200 = ${ma200:.2f} ✅\n\n"
                f"卖出条件: IBS &gt; {ibs_sell} 或持有 {max_hold} 天\n\n"
                f"⏰ 收盘前 15 分钟"
            )
            notifier.send_text(msg)

            # 记持仓
            state["ibs_position"] = {
                "buy_date": today,
                "buy_price": round(price, 2),
                "days_held": 0,
            }
            save_state(state)
            from qbot import db
            db.open_position("EWY_IBS", "EWY", qty, round(price, 2), today)
            log.info(f"IBS buy signal: IBS={ibs:.4f}, price=${price:.2f}")
        else:
            reasons = []
            if ibs >= ibs_buy:
                reasons.append(f"IBS={ibs:.3f}")
            if not above_ma:
                reasons.append(f"Close<MA200" if ma200 > 0 else "MA200 N/A")
            if state.get("circuit_breaker"):
                reasons.append("circuit breaker")
            if reasons:
                log.info(f"IBS no entry ({', '.join(reasons)})")


def is_market_hours() -> bool:
    """美东 9:30-16:00 开盘时段。"""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time as dtime
    return dtime(9, 30) <= t <= dtime(16, 0)


if __name__ == "__main__":
    import time

    if "--once" in sys.argv:
        run()
    else:
        # 持续运行模式：每 60 秒检查一次，仅盘中运行
        log.info("Starting continuous monitor (60s interval)")
        while True:
            if is_market_hours():
                try:
                    run()
                except Exception as e:
                    log.error(f"Run error: {e}")
            time.sleep(60)
