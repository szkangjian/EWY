"""
从 Yahoo Finance 获取今天的 EWY 1分钟数据，追加到主数据库 CSV
"""
import yfinance as yf
import pandas as pd

MAIN_CSV = "ewy_minute_data.csv"

print("📥 从 Yahoo Finance 获取今天的 EWY 1分钟数据...")
data = yf.download("EWY", period="1d", interval="1m")

if data.empty:
    print("❌ 未获取到数据")
    exit(1)

# yfinance 返回 MultiIndex columns，展平
if isinstance(data.columns, pd.MultiIndex):
    data.columns = data.columns.get_level_values(0)

# 统一格式：去掉时区信息，转为 US/Eastern 本地时间（与 Polygon 数据一致）
data.index = data.index.tz_convert('US/Eastern').tz_localize(None)
data.index.name = 'timestamp'
data = data[['Open', 'High', 'Low', 'Close', 'Volume']]

print(f"✅ 获取到 {len(data)} 条今日分钟数据")
print(f"   范围: {data.index.min()} → {data.index.max()}")

# 读取历史并合并（确保 index 都是 datetime 类型）
hist = pd.read_csv(MAIN_CSV, index_col='timestamp', parse_dates=True)
print(f"📊 历史数据: {len(hist)} 条")

combined = pd.concat([hist, data])
combined = combined[~combined.index.duplicated(keep='last')]
combined.sort_index(inplace=True)
combined.to_csv(MAIN_CSV)

print(f"✅ 合并完成！总计 {len(combined)} 条 → {MAIN_CSV}")
