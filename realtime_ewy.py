"""
EWY 实时分钟 K 线监控 + 策略信号提醒 (Finnhub WebSocket)

功能:
  - 通过 Finnhub WebSocket 接收 EWY 逐笔成交
  - 实时聚合为 1 分钟 K 线 (OHLCV)
  - 盘中跌幅策略: 跌破前日收盘 -3.5% 时提醒买入
  - 盘中反弹监控: 持仓时监控反弹 +2.5% 目标
  - IBS 策略: 15:50 后开始计算实时 IBS，提醒收盘操作
  - 持仓状态与 ewy_signal.py 共享 (ewy_signal_state.json)
  - 收盘后自动追加到历史 CSV

用法:
  uv run python realtime_ewy.py

Ctrl+C 退出后自动保存当日数据。
"""

import json
import time
import signal
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import websocket
except ImportError:
    print("需要安装 websocket-client:")
    print("  uv pip install websocket-client")
    sys.exit(1)

import pandas as pd
import numpy as np
from config import FINNHUB_KEY
from ewy_market_data import load_daily_bars, load_minute_data

# ---- 配置 ----
TICKER = "EWY"
HISTORY_CSV = "ewy_minute_data.csv"
MARKET_TZ = ZoneInfo("US/Eastern")
TODAY_CSV = f"ewy_realtime_{datetime.now(MARKET_TZ).strftime('%Y%m%d')}.csv"
STATE_FILE = "ewy_signal_state.json"

# 策略参数
DROP_ENTRY = -0.035      # 跌幅触发: -3.5%
DROP_EXIT = 0.025        # 反弹目标: +2.5%
IBS_BUY = 0.2            # IBS 买入阈值
IBS_SELL = 0.8           # IBS 卖出阈值

# ---- 全局状态 ----
candles = {}
current_minute = None
trade_count = 0

# 策略状态
prev_close = None        # 前日收盘价
ma200 = None             # 200日均线
day_open = None          # 今日开盘
day_high = None          # 今日最高
day_low = None           # 今日最低
day_close = None         # 最新价（实时更新）
day_volume = 0

# 信号状态
drop_triggered = False   # 跌幅信号已触发（避免重复提醒）
drop_alert_prices = set()  # 已提醒的跌幅档位
ibs_alerted = False      # IBS 信号已提醒
rebound_alerted = False  # 反弹达标已提醒
state = None             # 共享持仓状态


def load_state():
    """加载持仓状态（与 ewy_signal.py 共享）"""
    p = Path(STATE_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return {
        "ibs_position": None,
        "drop_position": None,
        "trade_log": [],
        "consecutive_exp_losses": 0,
        "circuit_breaker": False
    }


def save_state(s):
    Path(STATE_FILE).write_text(json.dumps(s, indent=2, ensure_ascii=False))


def load_prev_day_context():
    """从历史数据加载前日收盘、MA200"""
    global prev_close, ma200
    try:
        daily = load_daily_bars(HISTORY_CSV)

        if len(daily) >= 1:
            prev_close = daily.iloc[-1]['Close']
        if len(daily) >= 200:
            ma200 = daily['Close'].tail(200).mean()
        else:
            ma200 = daily['Close'].mean()

        print(f"  前日收盘: ${prev_close:.2f}")
        print(f"  MA200:    ${ma200:.2f}")
        print(f"  历史天数: {len(daily)}")
    except Exception as e:
        print(f"  加载历史数据失败: {e}")
        print(f"  策略信号将不可用")


def alert(msg):
    """打印高亮提醒"""
    print(f"\n{'!'*60}")
    print(f"  {msg}")
    print(f"{'!'*60}\n")
    # 响铃
    sys.stdout.write('\a')
    sys.stdout.flush()


def check_signals(price):
    """每次收到新价格时检查策略信号"""
    global drop_triggered, ibs_alerted, rebound_alerted
    global day_open, day_high, day_low, day_close, day_volume, state

    if prev_close is None:
        return

    # 更新日内统计
    if day_open is None:
        day_open = price
    day_high = max(day_high, price) if day_high else price
    day_low = min(day_low, price) if day_low else price
    day_close = price

    now = datetime.now(MARKET_TZ)
    drop_from_prev = (price / prev_close - 1)

    # ---- 熔断检查 ----
    if state and state.get("circuit_breaker"):
        return

    # ---- 跌幅策略: 盘中监控 ----
    # 每跌破一个整数百分点档位提醒一次
    if drop_from_prev <= DROP_ENTRY:
        pct_level = int(drop_from_prev * 100)  # e.g., -3, -4, -5
        if pct_level not in drop_alert_prices:
            drop_alert_prices.add(pct_level)
            entry_price = prev_close * (1 + DROP_ENTRY)
            alert(
                f"跌幅策略 BUY 信号 | EWY ${price:.2f} | "
                f"跌幅 {drop_from_prev*100:+.1f}% (vs 前日 ${prev_close:.2f})\n"
                f"  入场价: ${entry_price:.2f} | 目标: ${entry_price*(1+DROP_EXIT):.2f} (+{DROP_EXIT*100:.1f}%)"
            )

    # ---- 持仓反弹监控 ----
    if state and state.get("drop_position") and not rebound_alerted:
        pos = state["drop_position"]
        buy_price = pos["buy_price"]
        rebound = (price / buy_price - 1)
        if rebound >= DROP_EXIT:
            rebound_alerted = True
            alert(
                f"跌幅策略 反弹达标! | EWY ${price:.2f} | "
                f"买入 ${buy_price:.2f} → 反弹 {rebound*100:+.1f}%\n"
                f"  建议收盘前卖出 或 挂限价单 ${buy_price*(1+DROP_EXIT):.2f}"
            )

    # ---- IBS 策略: 收盘前提醒 ----
    # 15:50 后开始计算实时 IBS
    if now.hour == 15 and now.minute >= 50 and day_high and day_low:
        ibs = (price - day_low) / (day_high - day_low) if day_high != day_low else 0.5

        if not ibs_alerted:
            above_ma = ma200 is not None and price > ma200

            # IBS 买入信号
            if ibs < IBS_BUY and above_ma and not (state and state.get("ibs_position")):
                ibs_alerted = True
                alert(
                    f"IBS 策略 BUY 信号 | IBS={ibs:.3f} < {IBS_BUY} | "
                    f"EWY ${price:.2f} > MA200 ${ma200:.2f}\n"
                    f"  建议收盘价买入 (MOC 或 15:59 市价单)"
                )

            # IBS 卖出信号
            elif ibs > IBS_SELL and state and state.get("ibs_position"):
                ibs_alerted = True
                pos = state["ibs_position"]
                ret = (price / pos["buy_price"] - 1) * 100
                alert(
                    f"IBS 策略 SELL 信号 | IBS={ibs:.3f} > {IBS_SELL} | "
                    f"持仓 {pos['buy_date']} @ ${pos['buy_price']:.2f}\n"
                    f"  浮盈 {ret:+.1f}% | 建议收盘价卖出"
                )


def get_minute_key(ts_ms):
    """将毫秒时间戳转为分钟 key"""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(MARKET_TZ)
    dt = dt.replace(second=0, microsecond=0, tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M')


def update_candle(minute_key, price, volume):
    """用新的成交更新对应分钟的 K 线"""
    global current_minute

    if minute_key not in candles:
        candles[minute_key] = {
            'Open': price, 'High': price, 'Low': price,
            'Close': price, 'Volume': volume, 'Trades': 1
        }
    else:
        c = candles[minute_key]
        c['High'] = max(c['High'], price)
        c['Low'] = min(c['Low'], price)
        c['Close'] = price
        c['Volume'] += volume
        c['Trades'] += 1

    if current_minute and minute_key != current_minute:
        print_candle(current_minute)

    current_minute = minute_key


def print_candle(minute_key):
    """打印一条完整的分钟 K 线"""
    c = candles[minute_key]
    change = c['Close'] - c['Open']
    arrow = "+" if change >= 0 else "-"

    # 附加日内统计
    drop_str = ""
    if prev_close and day_low:
        drop_pct = (c['Close'] / prev_close - 1) * 100
        drop_str = f" | vs昨收:{drop_pct:+.2f}%"

    ibs_str = ""
    if day_high and day_low and day_high != day_low:
        ibs = (c['Close'] - day_low) / (day_high - day_low)
        ibs_str = f" | IBS:{ibs:.2f}"

    print(
        f"  {arrow} {minute_key} | "
        f"O:{c['Open']:>8.3f}  H:{c['High']:>8.3f}  "
        f"L:{c['Low']:>8.3f}  C:{c['Close']:>8.3f} | "
        f"Vol:{c['Volume']:>10,.0f}{drop_str}{ibs_str}"
    )


def print_live_tick(price, volume):
    """实时显示最新成交"""
    now = datetime.now(MARKET_TZ).strftime('%H:%M:%S')
    drop_str = ""
    if prev_close:
        drop_pct = (price / prev_close - 1) * 100
        drop_str = f"  vs昨收:{drop_pct:+.2f}%"

    ibs_str = ""
    if day_high and day_low and day_high != day_low:
        ibs = (price - day_low) / (day_high - day_low)
        ibs_str = f"  IBS:{ibs:.2f}"

    pos_str = ""
    if state:
        if state.get("ibs_position"):
            p = state["ibs_position"]
            ret = (price / p["buy_price"] - 1) * 100
            pos_str = f"  [IBS持仓 {ret:+.1f}%]"
        if state.get("drop_position"):
            p = state["drop_position"]
            ret = (price / p["buy_price"] - 1) * 100
            pos_str += f"  [DROP持仓 {ret:+.1f}%]"

    sys.stdout.write(
        f"\r  >> {now} | ${price:.3f}{drop_str}{ibs_str}{pos_str}  "
        f"| K线:{len(candles)}  成交:{trade_count}"
    )
    sys.stdout.flush()


def save_candles():
    """保存当日 K 线到 CSV"""
    if not candles:
        print("\n没有数据可保存。")
        return

    df = pd.DataFrame.from_dict(candles, orient='index')
    df.index.name = 'timestamp'
    df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)
    df.to_csv(TODAY_CSV)
    print(f"\n当日数据已保存至 {TODAY_CSV} ({len(df)} 条K线)")

    try:
        hist = load_minute_data(HISTORY_CSV).set_index('timestamp')
        combined = pd.concat([hist, df])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined.sort_index(inplace=True)
        combined.to_csv(HISTORY_CSV)
        print(f"已追加至历史文件 {HISTORY_CSV} (总计 {len(combined)} 条)")
    except FileNotFoundError:
        print(f"未找到历史文件 {HISTORY_CSV}，仅保存当日数据。")


def print_session_summary():
    """收盘时打印日内总结"""
    if not day_open or not day_close:
        return
    print(f"\n{'='*60}")
    print(f"  EWY 日内总结")
    print(f"{'='*60}")
    print(f"  开盘: ${day_open:.2f}  最高: ${day_high:.2f}  最低: ${day_low:.2f}  收盘: ${day_close:.2f}")
    if prev_close:
        ret = (day_close / prev_close - 1) * 100
        print(f"  日涨跌: {ret:+.2f}% (vs 前日收盘 ${prev_close:.2f})")
    if day_high and day_low and day_high != day_low:
        ibs = (day_close - day_low) / (day_high - day_low)
        print(f"  最终 IBS: {ibs:.3f}", end="")
        if ibs < IBS_BUY:
            print(f"  << 低 IBS，明日可能反弹")
        elif ibs > IBS_SELL:
            print(f"  << 高 IBS，如有持仓考虑卖出")
        else:
            print()
    if drop_alert_prices:
        print(f"  跌幅信号触发: {len(drop_alert_prices)} 次 (最大跌幅档位: {min(drop_alert_prices)}%)")
    print(f"  分钟 K 线: {len(candles)} 条  |  总成交笔数: {trade_count}")
    print(f"{'='*60}\n")


# ---- WebSocket 回调 ----
def on_message(ws, message):
    global trade_count
    data = json.loads(message)

    if data.get("type") == "trade":
        for trade in data.get("data", []):
            price = trade["p"]
            volume = trade["v"]
            ts = trade["t"]
            trade_count += 1

            minute_key = get_minute_key(ts)
            update_candle(minute_key, price, volume)
            check_signals(price)
            print_live_tick(price, volume)

    elif data.get("type") == "ping":
        pass


def on_error(ws, error):
    print(f"\nWebSocket 错误: {error}")


def on_close(ws, close_status_code, close_msg):
    print(f"\n连接已关闭 (code={close_status_code})")
    if current_minute and current_minute in candles:
        print_candle(current_minute)
    print_session_summary()
    save_candles()


def on_open(ws):
    print(f"  已连接 Finnhub WebSocket")
    print(f"  正在订阅 {TICKER} 实时成交数据...\n")
    print(f"{'─' * 80}")
    print(f"  时间              | 开盘     最高     最低     收盘     | 成交量     | 日内指标")
    print(f"{'─' * 80}")
    ws.send(json.dumps({"type": "subscribe", "symbol": TICKER}))


# ---- 主程序 ----
def main():
    global state

    print(f"\n  EWY 实时监控 + 策略信号")
    print(f"  数据源: Finnhub WebSocket")
    print(f"  按 Ctrl+C 退出并保存数据\n")

    # 加载上下文
    print(f"  --- 加载上下文 ---")
    load_prev_day_context()
    state = load_state()

    if state.get("circuit_breaker"):
        print(f"  !! 策略已熔断 — 仅监控，不发信号")
    else:
        print(f"  策略: 正常运行")

    if state.get("ibs_position"):
        p = state["ibs_position"]
        print(f"  IBS 持仓: {p['buy_date']} @ ${p['buy_price']:.2f} (第{p['days_held']}天)")
    if state.get("drop_position"):
        p = state["drop_position"]
        target = p["buy_price"] * (1 + DROP_EXIT)
        print(f"  DROP 持仓: {p['buy_date']} @ ${p['buy_price']:.2f} | 反弹目标 ${target:.2f}")

    # 信号阈值
    if prev_close:
        drop_price = prev_close * (1 + DROP_ENTRY)
        print(f"\n  --- 信号触发价 ---")
        print(f"  跌幅买入触发: ${drop_price:.2f} (前日收盘 ${prev_close:.2f} × {1+DROP_ENTRY:.2f})")
        if state.get("drop_position"):
            target = state["drop_position"]["buy_price"] * (1 + DROP_EXIT)
            print(f"  反弹卖出目标: ${target:.2f}")
        if ma200:
            print(f"  MA200 熔断线: ${ma200:.2f}")

    print()

    def signal_handler(sig, frame):
        print("\n\n  正在停止...")
        if current_minute and current_minute in candles:
            print_candle(current_minute)
        print_session_summary()
        save_candles()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    ws_url = f"wss://ws.finnhub.io?token={FINNHUB_KEY}"
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()


if __name__ == "__main__":
    main()
