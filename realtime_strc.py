"""
STRC 实时分钟 K 线监控 (Finnhub WebSocket)

功能:
  - 通过 Finnhub WebSocket 接收 STRC 逐笔成交
  - 实时聚合为 1 分钟 K 线 (OHLCV)
  - 终端实时显示最新 K 线
  - 收盘后自动追加到历史 CSV

用法:
  uv run python realtime_strc.py

Ctrl+C 退出后自动保存当日数据。
"""

import json
import time
import signal
import sys
from datetime import datetime, timezone
from collections import defaultdict

try:
    import websocket
except ImportError:
    print("需要安装 websocket-client:")
    print("  uv pip install websocket-client")
    sys.exit(1)

import pandas as pd
from config import FINNHUB_KEY

# ---- 配置 ----
TICKER = "STRC"
HISTORY_CSV = "strc_minute_data.csv"
TODAY_CSV = f"strc_realtime_{datetime.now().strftime('%Y%m%d')}.csv"

# ---- 全局状态 ----
# 存储每分钟的 K 线: { "2026-03-20 09:30": {"open":..., "high":..., ...} }
candles = {}
current_minute = None
trade_count = 0


def get_minute_key(ts_ms):
    """将毫秒时间戳转为分钟 key，如 '2026-03-20 09:30'"""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime('%Y-%m-%d %H:%M')


def update_candle(minute_key, price, volume):
    """用新的成交更新对应分钟的 K 线"""
    global current_minute

    if minute_key not in candles:
        candles[minute_key] = {
            'Open': price,
            'High': price,
            'Low': price,
            'Close': price,
            'Volume': volume,
            'Trades': 1
        }
    else:
        c = candles[minute_key]
        c['High'] = max(c['High'], price)
        c['Low'] = min(c['Low'], price)
        c['Close'] = price
        c['Volume'] += volume
        c['Trades'] += 1

    # 分钟切换时，打印上一分钟的完整 K 线
    if current_minute and minute_key != current_minute:
        print_candle(current_minute)

    current_minute = minute_key


def print_candle(minute_key):
    """打印一条完整的分钟 K 线"""
    c = candles[minute_key]
    change = c['Close'] - c['Open']
    arrow = "🟢" if change >= 0 else "🔴"
    print(
        f"  {arrow} {minute_key} | "
        f"O:{c['Open']:>8.3f}  H:{c['High']:>8.3f}  "
        f"L:{c['Low']:>8.3f}  C:{c['Close']:>8.3f} | "
        f"Vol:{c['Volume']:>10,.0f}  Trades:{c['Trades']:>4}"
    )


def print_live_tick(price, volume):
    """实时显示最新成交（覆盖同一行）"""
    now = datetime.now().strftime('%H:%M:%S')
    sys.stdout.write(
        f"\r  ⚡ {now} | STRC ${price:.3f}  vol:{volume:,.0f}  "
        f"| 分钟K线:{len(candles)}条  总成交:{trade_count}笔"
    )
    sys.stdout.flush()


def save_candles():
    """保存当日 K 线到 CSV"""
    if not candles:
        print("\n没有数据可保存。")
        return

    df = pd.DataFrame.from_dict(candles, orient='index')
    df.index.name = 'timestamp'
    df.sort_index(inplace=True)
    df.to_csv(TODAY_CSV)
    print(f"\n💾 当日数据已保存至 {TODAY_CSV} ({len(df)} 条K线)")

    # 尝试追加到历史文件
    try:
        hist = pd.read_csv(HISTORY_CSV, index_col='timestamp')
        combined = pd.concat([hist, df])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined.sort_index(inplace=True)
        combined.to_csv(HISTORY_CSV)
        print(f"📊 已追加至历史文件 {HISTORY_CSV} (总计 {len(combined)} 条)")
    except FileNotFoundError:
        print(f"⚠️  未找到历史文件 {HISTORY_CSV}，仅保存当日数据。")


# ---- WebSocket 回调 ----
def on_message(ws, message):
    global trade_count
    data = json.loads(message)

    if data.get("type") == "trade":
        for trade in data.get("data", []):
            price = trade["p"]
            volume = trade["v"]
            ts = trade["t"]  # 毫秒时间戳
            trade_count += 1

            minute_key = get_minute_key(ts)
            update_candle(minute_key, price, volume)
            print_live_tick(price, volume)

    elif data.get("type") == "ping":
        pass  # Finnhub 心跳


def on_error(ws, error):
    print(f"\n❌ WebSocket 错误: {error}")


def on_close(ws, close_status_code, close_msg):
    print(f"\n🔌 连接已关闭 (code={close_status_code})")
    # 打印最后一条未输出的 K 线
    if current_minute and current_minute in candles:
        print_candle(current_minute)
    save_candles()


def on_open(ws):
    print(f"✅ 已连接 Finnhub WebSocket")
    print(f"📡 正在订阅 {TICKER} 实时成交数据...\n")
    print(f"{'─' * 80}")
    print(f"  时间              | 开盘     最高     最低     收盘     | 成交量       笔数")
    print(f"{'─' * 80}")
    ws.send(json.dumps({"type": "subscribe", "symbol": TICKER}))


# ---- 主程序 ----
def main():
    print(f"🚀 STRC 实时分钟 K 线监控")
    print(f"   数据源: Finnhub WebSocket")
    print(f"   按 Ctrl+C 退出并保存数据\n")

    # 优雅退出
    def signal_handler(sig, frame):
        print("\n\n⏹️  正在停止...")
        if current_minute and current_minute in candles:
            print_candle(current_minute)
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
