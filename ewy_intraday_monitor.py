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

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from qbot import config, notifier
from qbot.data_feed import get_quote
from qbot.log_util import get_logger

log = get_logger("intraday_monitor", "EWY")

DATA_CSV = Path(__file__).parent / "ewy_minute_data.csv"
STATE_FILE = Path(__file__).parent / "ewy_signal_state.json"
ALERT_FILE = Path(__file__).parent / ".ewy_intraday_alerts.json"

# 参数
cfg = config.strategy_params("EWY_IBS") or {}
DROP_ENTRY = cfg.get("drop_entry", -0.03)
DROP_EXIT = cfg.get("drop_exit", 0.025)


def load_prev_close() -> float | None:
    """从历史 CSV 获取最近一个交易日收盘价。"""
    try:
        df = pd.read_csv(DATA_CSV, parse_dates=['timestamp'])
        df['date'] = df['timestamp'].dt.date
        daily = df.groupby('date').agg(Close=('Close', 'last')).reset_index()
        daily = daily.sort_values('date')
        if len(daily) >= 1:
            return float(daily.iloc[-1]['Close'])
    except Exception as e:
        log.error(f"Failed to load prev close: {e}")
    return None


def load_alerts() -> dict:
    """加载今日已发送的告警记录。"""
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
    return {}


def run():
    today = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== EWY Intraday Check {datetime.now().strftime('%H:%M')} ===")

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
    if drop_pct <= DROP_ENTRY:
        # 按整数百分点分档，避免重复通知
        level = int(drop_pct * 100)  # e.g., -3, -5, -7
        if level not in alerts["drop_levels"]:
            alerts["drop_levels"].append(level)

            entry_price = prev_close * (1 + DROP_ENTRY)
            target_price = entry_price * (1 + DROP_EXIT)

            msg = (
                f"🔴 <b>EWY 跌幅买入信号</b>\n\n"
                f"当前: <b>${price:.2f}</b> ({drop_pct*100:+.1f}%)\n"
                f"前日收盘: ${prev_close:.2f}\n"
                f"入场价: ${entry_price:.2f} (跌{abs(DROP_ENTRY)*100:.0f}%触发)\n"
                f"反弹目标: ${target_price:.2f} (+{DROP_EXIT*100:.1f}%)\n"
                f"最大持有: {cfg.get('drop_max_hold', 3)} 天\n\n"
                f"⏰ {datetime.now().strftime('%H:%M ET')}"
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
            msg = (
                f"🟢 <b>EWY 反弹达标!</b>\n\n"
                f"当前: <b>${price:.2f}</b>\n"
                f"买入: ${buy_price:.2f} ({pos['buy_date']})\n"
                f"反弹: {rebound*100:+.1f}% (目标 +{DROP_EXIT*100:.1f}%)\n\n"
                f"建议收盘前卖出\n"
                f"⏰ {datetime.now().strftime('%H:%M ET')}"
            )
            notifier.send_text(msg)
            log.info(f"Rebound alert sent: {rebound*100:+.1f}%")

    # ---- IBS 持仓反弹/止损监控 ----
    if state.get("ibs_position"):
        pos = state["ibs_position"]
        buy_price = pos["buy_price"]
        ret = (price / buy_price - 1)
        log.info(f"IBS position: {pos['buy_date']} @ ${buy_price:.2f}, "
                 f"current return={ret*100:+.2f}%")

    save_alerts(alerts)


if __name__ == "__main__":
    run()
