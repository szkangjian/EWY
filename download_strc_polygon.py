"""
从 Polygon.io 分批下载 STRC 近 2 年的 1 分钟级别历史数据。

免费版限制:
  - 每分钟 5 次 API 请求
  - 每次最多 50,000 条数据 (~3 个月的分钟数据)

策略: 按月分批请求，每次请求之间 sleep 13 秒，确保不超限。
"""

import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from config import POLYGON_KEY

# ---- 配置 ----
API_KEY = POLYGON_KEY
TICKER = "STRC"
OUTPUT_FILE = "strc_minute_data.csv"

# 起始和结束日期
# STRC 约从 2025 年 7 月底开始交易，无需从 2 年前开始
END_DATE = datetime.now().date()
START_DATE = datetime(2025, 7, 1).date()

# 每批覆盖的天数（~1 个月，确保不超 50,000 条）
BATCH_DAYS = 30

# 请求间隔（秒），免费版每分钟 5 次 → 至少 12 秒
REQUEST_INTERVAL = 13


def fetch_batch(date_from: str, date_to: str) -> pd.DataFrame | None:
    """获取一批分钟数据"""
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{TICKER}/range/1/minute/"
        f"{date_from}/{date_to}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={API_KEY}"
    )
    resp = requests.get(url)
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        if not results:
            return None
        df = pd.DataFrame(results)
        df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df.rename(columns={
            'o': 'Open', 'h': 'High', 'l': 'Low',
            'c': 'Close', 'v': 'Volume', 'vw': 'VWAP', 'n': 'Trades'
        }, inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume', 'VWAP', 'Trades']]
    elif resp.status_code == 429:
        print(f"  ⚠️  速率限制！等待 60 秒后重试...")
        time.sleep(60)
        return fetch_batch(date_from, date_to)  # 重试
    else:
        print(f"  ❌ 请求失败: {resp.status_code} - {resp.text[:200]}")
        return None


def main():
    print(f"📊 开始下载 {TICKER} 分钟数据")
    print(f"   时间范围: {START_DATE} → {END_DATE}")
    print(f"   每批天数: {BATCH_DAYS} 天")
    print(f"   请求间隔: {REQUEST_INTERVAL} 秒")
    print()

    all_dfs = []
    total_rows = 0
    batch_num = 0

    current_start = START_DATE
    while current_start < END_DATE:
        current_end = min(current_start + timedelta(days=BATCH_DAYS - 1), END_DATE)
        batch_num += 1

        date_from = current_start.strftime('%Y-%m-%d')
        date_to = current_end.strftime('%Y-%m-%d')

        print(f"[{batch_num:02d}] {date_from} → {date_to} ... ", end="", flush=True)

        df = fetch_batch(date_from, date_to)
        if df is not None and not df.empty:
            all_dfs.append(df)
            total_rows += len(df)
            print(f"✅ {len(df)} 条 (累计 {total_rows:,})")
        else:
            print(f"⏭️  无数据（可能是周末/假日）")

        current_start = current_end + timedelta(days=1)

        # 限速：最后一批不需要等
        if current_start < END_DATE:
            print(f"     ⏳ 等待 {REQUEST_INTERVAL} 秒...", flush=True)
            time.sleep(REQUEST_INTERVAL)

    # 合并并保存
    if all_dfs:
        final_df = pd.concat(all_dfs)
        final_df.sort_index(inplace=True)
        final_df = final_df[~final_df.index.duplicated(keep='first')]

        final_df.to_csv(OUTPUT_FILE)
        print()
        print(f"{'=' * 50}")
        print(f"✅ 下载完成！")
        print(f"   总数据量: {len(final_df):,} 条")
        print(f"   时间跨度: {final_df.index.min()} → {final_df.index.max()}")
        print(f"   保存至: {OUTPUT_FILE}")
        print(f"{'=' * 50}")
    else:
        print("\n❌ 未获取到任何数据。")


if __name__ == "__main__":
    main()
