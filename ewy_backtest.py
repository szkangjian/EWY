"""
EWY 深度因素分析
1. Bid-ask spread 分析
2. 除息日前后波动（自动从 Polygon 拉取除息日）
3. 极端事件分析（日跌幅 >2%）
4. 波动率特征（日内/日间）
5. 成交量模式（开盘/收盘集中度、除息日异常）
"""
import pandas as pd
import numpy as np
import requests
import yfinance as yf
from config import POLYGON_KEY
from ewy_market_data import load_regular_session_data

CSV_FILE = 'ewy_minute_data.csv'

# ==========================================
# 辅助函数：从 Polygon 获取 EWY 除息日
# ==========================================
def fetch_ex_dates():
    """从 Polygon API 获取 EWY 的历史除息日和股息金额"""
    url = f"https://api.polygon.io/v3/reference/dividends?ticker=EWY&limit=50&apiKey={POLYGON_KEY}"
    try:
        resp = requests.get(url).json()
        if 'results' not in resp:
            print("⚠️  无法从 Polygon 获取除息日数据，尝试 yfinance...")
            return fetch_ex_dates_yf()
        divs = []
        for d in resp['results']:
            ex_date = d.get('ex_dividend_date')
            amount = d.get('cash_amount', 0)
            if ex_date:
                divs.append({'ex_date': pd.to_datetime(ex_date), 'amount': amount})
        return pd.DataFrame(divs).sort_values('ex_date')
    except Exception as e:
        print(f"⚠️  Polygon API 错误: {e}，尝试 yfinance...")
        return fetch_ex_dates_yf()


def fetch_ex_dates_yf():
    """备用：从 yfinance 获取除息日"""
    ticker = yf.Ticker("EWY")
    dividends = ticker.dividends
    if dividends.empty:
        return pd.DataFrame(columns=['ex_date', 'amount'])
    dividends.index = dividends.index.tz_localize(None)
    df = pd.DataFrame({'ex_date': dividends.index, 'amount': dividends.values})
    return df.sort_values('ex_date')


# ==========================================
# 加载数据
# ==========================================
print("📊 加载 EWY 分钟级数据...")
try:
    df = load_regular_session_data(CSV_FILE).set_index('timestamp')
except FileNotFoundError:
    print(f"❌ 未找到 {CSV_FILE}，请先运行 download_ewy_polygon.py")
    exit(1)

daily = df.groupby(df.index.date).agg({
    'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
}).dropna()
daily.index = pd.to_datetime(daily.index)

print(f"   分钟数据: {len(df):,} 条")
print(f"   日线数据: {len(daily)} 天")
print(f"   时间跨度: {df.index.min()} → {df.index.max()}")

# 获取除息日
div_df = fetch_ex_dates()
# 只保留数据范围内的除息日
div_df = div_df[(div_df['ex_date'] >= df.index.min()) & (div_df['ex_date'] <= df.index.max())]
print(f"   数据范围内除息日: {len(div_df)} 次")

# ==========================================
# 1. Bid-Ask Spread 分析
# ==========================================
print(f"\n{'=' * 70}")
print("一、Bid-Ask Spread 精确分析")
print("=" * 70)

min_ranges = df['High'] - df['Low']
nonzero = min_ranges[min_ranges > 0]

print(f"   分钟级波幅统计:")
print(f"     均值: ${nonzero.mean():.4f}")
print(f"     中位: ${nonzero.median():.4f}")
print(f"     25%:  ${nonzero.quantile(0.25):.4f}")
print(f"     75%:  ${nonzero.quantile(0.75):.4f}")
print(f"   零波幅分钟占比: {(min_ranges == 0).sum() / len(min_ranges) * 100:.1f}%")

# 以百分比表示 (EWY 价格不同于 STRC 的 $100)
avg_price = df['Close'].mean()
print(f"\n   平均价格: ${avg_price:.2f}")
print(f"   中位波幅占价格比: {nonzero.median() / avg_price * 100:.3f}%")

# 交易成本影响
print(f"\n   交易成本影响 (买卖各一次, 假设 spread ~${nonzero.median():.2f}):")
spread_est = nonzero.median()
for shares in [100, 500, 1000]:
    spread_cost = spread_est * 2 * shares
    ib_comm = max(1.0, 0.005 * shares) * 2
    print(f"     {shares:>4d} 股 | Spread: ${spread_cost:.0f} | IB Pro佣金: ${ib_comm:.0f} | 合计: ${spread_cost + ib_comm:.0f}")

# ==========================================
# 2. 除息日前后波动分析
# ==========================================
print(f"\n{'=' * 70}")
print("二、除息日前后价格行为分析")
print("=" * 70)

if len(div_df) > 0:
    print(f"\n   {'除息日':>12s} | {'前日收':>7s} | {'当日开':>7s} | {'当日收':>7s} | {'股息':>6s} | {'理论跌':>7s} | {'实际跌':>7s} | {'Cushion':>8s} | {'3日后收':>7s}")
    print(f"   {'-'*12}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

    for _, row in div_df.iterrows():
        ex_date = row['ex_date']
        div_amount = row['amount']

        # 找到除息日对应的交易日
        mask = daily.index >= ex_date
        if mask.sum() == 0:
            continue
        actual_ex = daily.index[mask][0]
        ex_loc = daily.index.get_loc(actual_ex)

        if ex_loc < 1 or ex_loc + 3 >= len(daily):
            continue

        prev_close = daily.iloc[ex_loc - 1]['Close']
        ex_open = daily.iloc[ex_loc]['Open']
        ex_close = daily.iloc[ex_loc]['Close']
        day3_close = daily.iloc[min(ex_loc + 3, len(daily)-1)]['Close']

        actual_drop = prev_close - ex_open
        cushion = div_amount - actual_drop

        print(f"   {actual_ex.strftime('%Y-%m-%d'):>12s} | {prev_close:>7.2f} | {ex_open:>7.2f} | {ex_close:>7.2f} | ${div_amount:.3f} | -${div_amount:.3f} | {'-' if actual_drop>0 else '+'}{abs(actual_drop):.3f} | {cushion:>+8.3f} | {day3_close:>7.2f}")

    print(f"\n   📊 除息日模式总结:")
    print(f"   - EWY 为季度分红 ETF，除息金额通常在 $0.20~$1.50 之间")
    print(f"   - 需观察实际跳空幅度 vs 股息金额的偏差（Cushion）")
else:
    print("   ⚠️  数据范围内无除息日记录")

# ==========================================
# 3. 极端事件分析（日跌幅 >2%）
# ==========================================
print(f"\n{'=' * 70}")
print("三、极端下跌事件分析 (日跌幅 > 2%)")
print("=" * 70)

daily['pct_change'] = daily['Close'].pct_change() * 100
big_drops = daily[daily['pct_change'] < -2.0].copy()

print(f"\n   数据范围内日跌幅 > 2% 的事件: {len(big_drops)} 次")
if len(big_drops) > 0:
    print(f"\n   {'日期':>12s} | {'开盘':>7s} | {'收盘':>7s} | {'跌幅%':>7s} | {'最低':>7s} | {'恢复天数':>8s} | {'后续5日走势'}")
    print(f"   {'-'*12}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*40}")

    for date, row in big_drops.iterrows():
        loc = daily.index.get_loc(date)
        # 恢复时间
        recovery_days = 0
        pre_drop_price = daily.iloc[loc-1]['Close'] if loc > 0 else row['Open']
        for i in range(1, min(30, len(daily) - loc)):
            if daily.iloc[loc + i]['Close'] >= pre_drop_price * 0.995:
                recovery_days = i
                break

        # 后续 5 天走势
        future = ""
        for i in range(1, min(6, len(daily) - loc)):
            d = daily.iloc[loc + i]
            future += f"D+{i}:{d['Close']:.2f} "

        recovery_str = f'{recovery_days} 天' if recovery_days > 0 else '30天内未恢复'
        print(f"   {date.strftime('%Y-%m-%d'):>12s} | {row['Open']:>7.2f} | {row['Close']:>7.2f} | {row['pct_change']:>+7.2f}% | {row['Low']:>7.2f} | {recovery_str:>8s} | {future}")

# 统计汇总
print(f"\n   📊 极端事件统计:")
print(f"   - 日跌幅 > 2% 的天数: {len(big_drops)} ({len(big_drops)/len(daily)*100:.1f}%)")
print(f"   - 日跌幅 > 3% 的天数: {len(daily[daily['pct_change'] < -3.0])}")
print(f"   - 日跌幅 > 5% 的天数: {len(daily[daily['pct_change'] < -5.0])}")
print(f"   - 最大单日跌幅: {daily['pct_change'].min():.2f}%")
print(f"   - 最大单日涨幅: {daily['pct_change'].max():.2f}%")

# ==========================================
# 4. 波动率特征
# ==========================================
print(f"\n{'=' * 70}")
print("四、波动率特征分析")
print("=" * 70)

# 日内波动率 (High-Low range as % of Open)
daily['intraday_range_pct'] = (daily['High'] - daily['Low']) / daily['Open'] * 100

print(f"\n   日内波动率 (High-Low / Open):")
print(f"     均值: {daily['intraday_range_pct'].mean():.2f}%")
print(f"     中位: {daily['intraday_range_pct'].median():.2f}%")
print(f"     25%:  {daily['intraday_range_pct'].quantile(0.25):.2f}%")
print(f"     75%:  {daily['intraday_range_pct'].quantile(0.75):.2f}%")
print(f"     最大: {daily['intraday_range_pct'].max():.2f}%")

# 日间波动率 (收盘价的滚动标准差)
daily['ret'] = daily['Close'].pct_change()
vol_20d = daily['ret'].rolling(20).std() * np.sqrt(252) * 100
print(f"\n   年化波动率 (20日滚动):")
print(f"     当前: {vol_20d.iloc[-1]:.1f}%")
print(f"     均值: {vol_20d.mean():.1f}%")
print(f"     最低: {vol_20d.min():.1f}%")
print(f"     最高: {vol_20d.max():.1f}%")

# 收盘价分布
print(f"\n   收盘价分布:")
print(f"     均值: ${daily['Close'].mean():.2f}")
print(f"     中位: ${daily['Close'].median():.2f}")
print(f"     最低: ${daily['Close'].min():.2f}")
print(f"     最高: ${daily['Close'].max():.2f}")
print(f"     标准差: ${daily['Close'].std():.2f}")

# ==========================================
# 5. 成交量模式
# ==========================================
print(f"\n{'=' * 70}")
print("五、成交量模式分析")
print("=" * 70)

# 分钟级成交量分布（按时间段）
df_with_time = df.copy()
df_with_time['hour_min'] = df_with_time.index.strftime('%H:%M')
df_with_time['hour'] = df_with_time.index.hour

# 按小时汇总
hourly_vol = df_with_time.groupby('hour')['Volume'].mean()

print(f"\n   各小时段平均分钟成交量:")
for hour in sorted(hourly_vol.index):
    bar_len = int(hourly_vol[hour] / hourly_vol.max() * 30)
    print(f"     {hour:02d}:00  {'█' * bar_len} {hourly_vol[hour]:,.0f}")

# 开盘/收盘集中度
first_30min = df_with_time[(df_with_time.index.hour == 9) & (df_with_time.index.minute < 60)]
last_30min = df_with_time[(df_with_time.index.hour == 15) & (df_with_time.index.minute >= 30)]
total_vol = df_with_time['Volume'].sum()
first_30_vol = first_30min['Volume'].sum()
last_30_vol = last_30min['Volume'].sum()

print(f"\n   成交量集中度:")
print(f"     开盘前30分钟 (9:30-10:00): {first_30_vol/total_vol*100:.1f}% 的总成交量")
print(f"     收盘前30分钟 (15:30-16:00): {last_30_vol/total_vol*100:.1f}% 的总成交量")

# 日均成交量
print(f"\n   日均成交量: {daily['Volume'].mean():,.0f} 股")
print(f"   日均成交额: ${daily['Volume'].mean() * avg_price:,.0f}")

# 除息日成交量异常
if len(div_df) > 0:
    print(f"\n   除息日 vs 非除息日成交量:")
    for _, row in div_df.iterrows():
        ex_date = row['ex_date']
        mask = daily.index >= ex_date
        if mask.sum() == 0:
            continue
        actual_ex = daily.index[mask][0]
        ex_loc = daily.index.get_loc(actual_ex)

        if ex_loc < 20:
            continue

        # 前20日平均成交量
        adv_20 = daily.iloc[ex_loc-20:ex_loc]['Volume'].mean()
        ex_vol = daily.iloc[ex_loc]['Volume']
        t1_vol = daily.iloc[ex_loc-1]['Volume'] if ex_loc >= 1 else 0

        print(f"     {actual_ex.strftime('%Y-%m-%d')}: T-1={t1_vol:,.0f} ({t1_vol/adv_20:.1f}x) | T+0={ex_vol:,.0f} ({ex_vol/adv_20:.1f}x) | ADV20={adv_20:,.0f}")
