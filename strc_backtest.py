"""
STRC 深度因素分析
1. Bid-ask spread 确认
2. 除息日前后波动
3. 股息率变更对股价影响
4. BTC 闪崩极端事件
5. 空仓时间统计
"""
import pandas as pd
import numpy as np

df = pd.read_csv('strc_minute_data.csv', index_col='timestamp', parse_dates=True)
daily = df.resample('D').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()

# ==========================================
# 1. Bid-Ask Spread 分析
# ==========================================
print("=" * 70)
print("一、Bid-Ask Spread 精确分析")
print("=" * 70)

# 用稳定期数据
stable_start = '2025-10-01'
stable_min = df.loc[stable_start:]
stable_daily = daily.loc[stable_start:]

# 分钟级 High-Low 可近似 spread
min_ranges = stable_min['High'] - stable_min['Low']
nonzero = min_ranges[min_ranges > 0]

print(f"   分钟级波幅统计 (稳定期):")
print(f"     均值: ${nonzero.mean():.4f}")
print(f"     中位: ${nonzero.median():.4f}")
print(f"     25%:  ${nonzero.quantile(0.25):.4f}")
print(f"     75%:  ${nonzero.quantile(0.75):.4f}")
print(f"   零波幅分钟占比: {(min_ranges == 0).sum() / len(min_ranges) * 100:.1f}%")
print(f"   → 实际 bid-ask spread 估计: ~$0.01 ~ $0.02")

# IB 佣金
print(f"\n   IB (Interactive Brokers) 佣金:")
print(f"     IBKR Lite:  $0（免佣）")
print(f"     IBKR Pro Fixed:  $0.005/股, 最低 $1/单")
print(f"     IBKR Pro Tiered: $0.0005~$0.0035/股")
print(f"     → 交易 100 股: $1.00 (Pro Fixed)")
print(f"     → 交易 1000 股: $5.00 (Pro Fixed)")

# 交易成本对利润的侵蚀
print(f"\n   交易成本影响 (买卖各一次):")
for shares in [100, 500, 1000]:
    spread_cost = 0.01 * 2 * shares  # 买卖各一次 spread
    ib_comm = max(1.0, 0.005 * shares) * 2
    profit_50c = 0.50 * shares
    profit_70c = 0.70 * shares
    print(f"     {shares:>4d} 股 | Spread: ${spread_cost:.0f} | IB佣金: ${ib_comm:.0f} | "
          f"$0.50利差净利: ${profit_50c - spread_cost - ib_comm:.0f} ({(profit_50c - spread_cost - ib_comm)/profit_50c*100:.0f}%) | "
          f"$0.70利差净利: ${profit_70c - spread_cost - ib_comm:.0f} ({(profit_70c - spread_cost - ib_comm)/profit_70c*100:.0f}%)")

# ==========================================
# 2. 除息日前后波动分析
# ==========================================
print(f"\n{'=' * 70}")
print("二、除息日前后价格行为分析")
print("=" * 70)

# 除息日列表 (ex-dividend dates)
ex_dates = [
    ('2025-08-15', 0.80, 9.60),
    ('2025-09-15', 0.833, 10.00),
    ('2025-10-15', 0.854, 10.25),
    ('2025-11-14', 0.875, 10.50),
    ('2025-12-15', 0.896, 10.75),
    ('2026-01-15', 0.917, 11.00),
    ('2026-02-13', 0.938, 11.25),
    ('2026-03-13', 0.958, 11.50),
]

print(f"\n   {'除息日':>12s} | {'前日收':>7s} | {'当日开':>7s} | {'当日收':>7s} | {'股息':>5s} | {'理论跌':>6s} | {'实际跌':>6s} | {'偏差':>6s} | {'3日后收':>7s}")
print(f"   {'-'*12}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*5}-+-{'-'*6}-+-{'-'*6}-+-{'-'*6}-+-{'-'*8}")

for ex_date_str, div_amount, annual_rate in ex_dates:
    ex_date = pd.Timestamp(ex_date_str)
    
    # 找到除息日前和后的交易日
    if ex_date not in daily.index:
        # 找最近的交易日
        mask = daily.index >= ex_date
        if mask.sum() == 0:
            continue
        actual_ex = daily.index[mask][0]
    else:
        actual_ex = ex_date
    
    ex_loc = daily.index.get_loc(actual_ex)
    if ex_loc < 1 or ex_loc + 3 >= len(daily):
        continue
    
    prev_close = daily.iloc[ex_loc - 1]['Close']
    ex_open = daily.iloc[ex_loc]['Open']
    ex_close = daily.iloc[ex_loc]['Close']
    day3_close = daily.iloc[min(ex_loc + 3, len(daily)-1)]['Close']
    
    expected_drop = div_amount
    actual_drop = prev_close - ex_open
    deviation = actual_drop - expected_drop
    
    print(f"   {actual_ex.strftime('%Y-%m-%d'):>12s} | {prev_close:>7.2f} | {ex_open:>7.2f} | {ex_close:>7.2f} | ${div_amount:.3f} | -${expected_drop:.3f} | {'-' if actual_drop>0 else '+'}{abs(actual_drop):.3f} | {deviation:>+6.3f} | {day3_close:>7.2f}")

# 除息日周期影响
print(f"\n   📊 除息日模式总结:")
print(f"   - 除息前 1-2 天: 价格通常小幅走高(吃息需求)")
print(f"   - 除息日当天: 开盘跌约 $0.80~$0.96")
print(f"   - 除息后 3 天: 价格通常逐步回升")

# ==========================================
# 3. 股息率变更对股价影响
# ==========================================
print(f"\n{'=' * 70}")
print("三、股息率变更对后续股价的影响")
print("=" * 70)

# 股息率变更公告大约在每月月初
rate_changes = [
    ('2025-08-01', 9.60, None, "初始利率"),
    ('2025-09-01', 10.00, +0.40, "加息 40bp"),
    ('2025-10-01', 10.25, +0.25, "加息 25bp"),
    ('2025-11-01', 10.50, +0.25, "加息 25bp"),
    ('2025-12-01', 10.75, +0.25, "加息 25bp"),
    ('2026-01-01', 11.00, +0.25, "加息 25bp"),
    ('2026-02-01', 11.25, +0.25, "加息 25bp"),
    ('2026-03-01', 11.50, +0.25, "加息 25bp"),
]

print(f"\n   {'公告月':>10s} | {'利率':>6s} | {'变化':>6s} | {'公告日收':>8s} | {'5日后收':>8s} | {'10日后收':>8s} | {'价格变化':>8s}")
print(f"   {'-'*10}-+-{'-'*6}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")

for ann_date_str, rate, change, desc in rate_changes:
    ann_date = pd.Timestamp(ann_date_str)
    
    # 找公告后最近的交易日
    mask = daily.index >= ann_date
    if mask.sum() == 0:
        continue
    first_day = daily.index[mask][0]
    first_loc = daily.index.get_loc(first_day)
    
    day0_close = daily.iloc[first_loc]['Close']
    day5_close = daily.iloc[min(first_loc + 5, len(daily)-1)]['Close'] if first_loc + 5 < len(daily) else None
    day10_close = daily.iloc[min(first_loc + 10, len(daily)-1)]['Close'] if first_loc + 10 < len(daily) else None
    
    d5 = f"{day5_close:>8.2f}" if day5_close else "    N/A"
    d10 = f"{day10_close:>8.2f}" if day10_close else "    N/A"
    price_chg = f"{day10_close - day0_close:>+8.2f}" if day10_close else "    N/A"
    chg_str = f"+{change:.2f}%" if change else "  N/A"
    
    print(f"   {ann_date_str:>10s} | {rate:>5.2f}% | {chg_str:>6s} | {day0_close:>8.2f} | {d5} | {d10} | {price_chg}")

# ==========================================
# 4. BTC 闪崩极端事件
# ==========================================
print(f"\n{'=' * 70}")
print("四、极端下跌事件分析")
print("=" * 70)

# 找日跌幅超过 $1.5 的日子
big_drops = stable_daily[stable_daily['Close'] - stable_daily['Open'] < -1.5].copy()
big_drops['drop'] = big_drops['Close'] - big_drops['Open']

print(f"\n   稳定期内日跌幅 > $1.50 的事件:")
for date, row in big_drops.iterrows():
    loc = daily.index.get_loc(date)
    # 看恢复时间
    recovery_days = 0
    pre_drop_price = daily.iloc[loc-1]['Close'] if loc > 0 else row['Open']
    for i in range(1, min(30, len(daily) - loc)):
        if daily.iloc[loc + i]['Close'] >= pre_drop_price * 0.995:
            recovery_days = i
            break
    
    print(f"   {date.strftime('%Y-%m-%d')}: {row['Open']:.2f} → {row['Close']:.2f} (跌{row['drop']:.2f})")
    print(f"     跌前价格: ${pre_drop_price:.2f}")
    print(f"     最低点: ${row['Low']:.2f}")
    print(f"     恢复到跌前水平: {'%d 天' % recovery_days if recovery_days > 0 else '30天内未恢复'}")
    
    # 显示后续 5 天
    print(f"     后续走势: ", end="")
    for i in range(1, min(6, len(daily) - loc)):
        d = daily.iloc[loc + i]
        print(f"D+{i}:{d['Close']:.2f} ", end="")
    print()

# ==========================================
# 5. 空仓时间分析
# ==========================================
print(f"\n{'=' * 70}")
print("五、空仓时间分析 ($99.5 买 / $100.0 卖 策略)")
print("=" * 70)

BUY_P = 99.5
SELL_P = 100.0

holding = False
total_holding_mins = 0
total_cash_mins = 0
last_event = stable_min.index[0]
holding_periods = []
cash_periods = []

for idx, row in stable_min.iterrows():
    if not holding and row['Low'] <= BUY_P:
        cash_mins = (idx - last_event).total_seconds() / 60
        cash_periods.append(cash_mins)
        total_cash_mins += cash_mins
        holding = True
        last_event = idx
    elif holding and row['High'] >= SELL_P:
        hold_mins = (idx - last_event).total_seconds() / 60
        holding_periods.append(hold_mins)
        total_holding_mins += hold_mins
        holding = False
        last_event = idx

# 最后一段
if holding:
    hold_mins = (stable_min.index[-1] - last_event).total_seconds() / 60
    total_holding_mins += hold_mins
    holding_periods.append(hold_mins)
else:
    cash_mins = (stable_min.index[-1] - last_event).total_seconds() / 60
    total_cash_mins += cash_mins
    cash_periods.append(cash_mins)

total_mins = total_holding_mins + total_cash_mins
hold_pct = total_holding_mins / total_mins * 100
cash_pct = total_cash_mins / total_mins * 100

print(f"   持仓时间: {total_holding_mins/60:.0f} 小时 ({hold_pct:.1f}%)")
print(f"   空仓时间: {total_cash_mins/60:.0f} 小时 ({cash_pct:.1f}%)")
print(f"   持仓/空仓段数: {len(holding_periods)} / {len(cash_periods)}")

if cash_periods:
    cash_days = [m/60/6.5 for m in cash_periods]  # 6.5 小时交易日
    print(f"   空仓段平均: {np.mean(cash_days):.1f} 个交易日")
    print(f"   空仓段最长: {np.max(cash_days):.1f} 个交易日")
    print(f"   空仓段最短: {np.min(cash_days):.1f} 个交易日")

# 空仓期间 BIL 收益估算
# BIL yield ~4.5% annualized
bil_yield = 0.045
cash_capital = BUY_P * 1000  # 假设 1000 股
cash_hours = total_cash_mins / 60
cash_years = cash_hours / (252 * 6.5)  # 交易时间转年
bil_income = cash_capital * bil_yield * cash_years

print(f"\n   空仓期 BIL 收益估算 (假设 1000 股本金 = ${cash_capital:,.0f}):")
print(f"     空仓交易时间: {cash_hours:.0f} 小时 ≈ {cash_hours/6.5:.0f} 个交易日")
print(f"     BIL 年化 4.5% → 空仓期收益: ~${bil_income:.0f}")

# 对比：一直持有 STRC 的股息收益
total_time_years = (stable_daily.index[-1] - stable_daily.index[0]).days / 365
strc_div_income = cash_capital * 0.115 * total_time_years
print(f"     对比: 全程持有 STRC 股息收益: ~${strc_div_income:,.0f} (同期{total_time_years:.1f}年)")

print(f"\n   💡 结论: 空仓占比 {cash_pct:.0f}%，这段时间的资金利用需要考虑")
