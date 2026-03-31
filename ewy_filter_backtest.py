import pandas as pd
import numpy as np
import warnings
from ewy_market_data import build_daily_bars, load_regular_session_data

warnings.filterwarnings('ignore')

print("加载数据...")
df = load_regular_session_data('ewy_minute_data.csv')

# 构建日线
daily = build_daily_bars(df)

# 技术指标
daily['ma200'] = daily['Close'].rolling(200).mean()
daily['ret'] = daily['Close'].pct_change()
daily['IBS'] = (daily['Close'] - daily['Low']) / (daily['High'] - daily['Low'])

# RSI-2
delta = daily['Close'].diff()
gain = delta.clip(lower=0)
loss = (-delta).clip(lower=0)
avg_gain = gain.ewm(span=2, adjust=False).mean()
avg_loss = loss.ewm(span=2, adjust=False).mean()
daily['rsi2'] = 100 - (100 / (1 + avg_gain / avg_loss))

# RSI-2 连跌天数
daily['rsi2_down'] = 0
for i in range(1, len(daily)):
    if daily.loc[i, 'rsi2'] < daily.loc[i-1, 'rsi2']:
        daily.loc[i, 'rsi2_down'] = daily.loc[i-1, 'rsi2_down'] + 1

# Bollinger Bands
daily['bb_mid'] = daily['Close'].rolling(20).mean()
daily['bb_std'] = daily['Close'].rolling(20).std()
daily['bb_lower'] = daily['bb_mid'] - 2 * daily['bb_std']

# RSI-6
avg_gain6 = gain.ewm(span=6, adjust=False).mean()
avg_loss6 = loss.ewm(span=6, adjust=False).mean()
daily['rsi6'] = 100 - (100 / (1 + avg_gain6 / avg_loss6))

# 分钟级数据预处理
dates_list = sorted(df['date'].unique())
day_arrays = {}
prev_close_map = {}
for d in dates_list:
    mask = df['date'] == d
    day_df = df[mask].sort_values('timestamp')
    day_arrays[d] = (day_df['High'].values, day_df['Low'].values, day_df['Close'].values)
    prev_close_map[d] = day_df.iloc[-1]['Close']

# ============ 通用回测函数 ============
def run_backtest(daily_df, dates, day_arrays, prev_close_map,
                 entry_th=-0.03, exit_tg=0.025, max_hold=3,
                 filter_func=None, name="基准"):
    """filter_func(daily_df, idx) -> bool, True=允许交易"""
    trades = []
    date_to_idx = {d: i for i, d in enumerate(dates)}
    
    i = 1
    while i < len(dates):
        d = dates[i]
        d_dt = pd.Timestamp(d)
        
        # 在daily_df中找到对应行
        didx = daily_df[daily_df['date'] == d_dt].index
        if len(didx) == 0:
            i += 1
            continue
        didx = didx[0]
        
        # 过滤器检查
        if filter_func is not None and not filter_func(daily_df, didx):
            i += 1
            continue
        
        pc = prev_close_map[dates[i-1]]
        highs, lows, closes = day_arrays[d]
        
        trigger_mask = (lows / pc - 1) <= entry_th
        if not trigger_mask.any():
            i += 1
            continue
        
        tidx = np.argmax(trigger_mask)
        buy_price = pc * (1 + entry_th)
        
        sell_price = None
        sell_reason = None
        sell_day_idx = i
        
        rh = highs[tidx:]
        rl = lows[tidx:]
        found = False
        for k in range(len(rh)):
            if (rh[k] / buy_price - 1) >= exit_tg:
                sell_price = buy_price * (1 + exit_tg)
                sell_reason = 'TP'
                found = True
                break
        
        if not found:
            for hd in range(1, max_hold):
                ni = i + hd
                if ni >= len(dates):
                    break
                nh, nl, nc = day_arrays[dates[ni]]
                for k in range(len(nh)):
                    if (nh[k] / buy_price - 1) >= exit_tg:
                        sell_price = buy_price * (1 + exit_tg)
                        sell_reason = 'TP'
                        sell_day_idx = ni
                        found = True
                        break
                if found:
                    break
        
        if sell_price is None:
            li = min(i + max_hold - 1, len(dates) - 1)
            _, _, lc = day_arrays[dates[li]]
            sell_price = lc[-1]
            sell_reason = 'EXP'
            sell_day_idx = li
        
        ret = sell_price / buy_price - 1
        trades.append({'date': str(d), 'buy': round(buy_price,2),
                       'sell': round(sell_price,2), 'reason': sell_reason, 'ret': ret})
        i = sell_day_idx + 1
    
    return trades

def print_summary(trades, name):
    if not trades:
        print(f"  {name}: 0笔交易")
        return
    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wins = (tdf['ret'] > 0).sum()
    tp = (tdf['reason'] == 'TP').sum()
    exp_loss = tdf[tdf['reason'] == 'EXP']
    exp_loss_n = (exp_loss['ret'] < 0).sum() if len(exp_loss) > 0 else 0
    total = tdf['ret'].sum() * 100
    avg = tdf['ret'].mean() * 100
    worst = tdf['ret'].min() * 100
    print(f"  {name}:")
    print(f"    笔数: {n}, 胜率: {wins/n*100:.0f}%, TP: {tp}, 到期亏损: {exp_loss_n}")
    print(f"    总收益: {total:+.1f}%, 均收益: {avg:+.2f}%, 最大亏损: {worst:+.2f}%")
    return tdf

def print_trades(trades, name):
    print(f"\n  {name} 逐笔:")
    print(f"  {'日期':>12} | {'买入':>7} | {'卖出':>7} | {'收益':>7} | 原因")
    print(f"  {'-'*52}")
    for t in trades:
        print(f"  {t['date']:>12} | ${t['buy']:>6.2f} | ${t['sell']:>6.2f} | {t['ret']*100:>+6.2f}% | {t['reason']}")

dates = dates_list
entry_th = -0.03
exit_tg = 0.025
max_hold = 3

# ============ 0. 基准：无过滤 ============
print("=" * 80)
print(f"参数: 入场{entry_th*100:.1f}%, 反弹+{exit_tg*100:.1f}%, 持有{max_hold}天")
print("=" * 80)

trades_base = run_backtest(daily, dates, day_arrays, prev_close_map,
                           entry_th, exit_tg, max_hold, None, "基准")
print("\n[0] 基准（无过滤器）")
tdf_base = print_summary(trades_base, "无过滤")

# ============ 1. +200日均线过滤 ============
def filter_ma200(df, idx):
    if idx < 200:
        return False
    return df.loc[idx, 'Close'] > df.loc[idx, 'ma200']

trades_ma = run_backtest(daily, dates, day_arrays, prev_close_map,
                         entry_th, exit_tg, max_hold, filter_ma200)
print("\n[1] + 200日均线过滤（收盘 > MA200 才交易）")
tdf_ma = print_summary(trades_ma, "MA200")

# ============ 2. +IBS过滤 ============
def filter_ibs(df, idx):
    if idx < 1:
        return False
    return df.loc[idx-1, 'IBS'] < 0.2  # 前一天IBS<0.2（收在低位）

trades_ibs = run_backtest(daily, dates, day_arrays, prev_close_map,
                          entry_th, exit_tg, max_hold, filter_ibs)
print("\n[2] + IBS过滤（前日IBS < 0.2）")
tdf_ibs = print_summary(trades_ibs, "IBS<0.2")

# 放宽IBS
def filter_ibs_loose(df, idx):
    if idx < 1:
        return False
    return df.loc[idx-1, 'IBS'] < 0.3

trades_ibs2 = run_backtest(daily, dates, day_arrays, prev_close_map,
                            entry_th, exit_tg, max_hold, filter_ibs_loose)
print("\n[2b] + IBS过滤（前日IBS < 0.3）")
tdf_ibs2 = print_summary(trades_ibs2, "IBS<0.3")

# ============ 3. +RSI-2过滤（Connors R3风格）============
def filter_rsi2(df, idx):
    if idx < 200:
        return False
    # 收盘在200日均线之上
    if df.loc[idx, 'Close'] <= df.loc[idx, 'ma200']:
        return False
    # RSI-2 < 10
    if df.loc[idx-1, 'rsi2'] >= 10:
        return False
    return True

trades_rsi2 = run_backtest(daily, dates, day_arrays, prev_close_map,
                           entry_th, exit_tg, max_hold, filter_rsi2)
print("\n[3] + RSI-2 < 10 + MA200（Connors R3风格）")
tdf_rsi2 = print_summary(trades_rsi2, "RSI2+MA200")

# 放宽RSI
def filter_rsi2_loose(df, idx):
    if idx < 200:
        return False
    if df.loc[idx, 'Close'] <= df.loc[idx, 'ma200']:
        return False
    if df.loc[idx-1, 'rsi2'] >= 20:
        return False
    return True

trades_rsi2b = run_backtest(daily, dates, day_arrays, prev_close_map,
                            entry_th, exit_tg, max_hold, filter_rsi2_loose)
print("\n[3b] + RSI-2 < 20 + MA200")
tdf_rsi2b = print_summary(trades_rsi2b, "RSI2<20+MA200")

# ============ 4. +Bollinger Band + RSI-6 ============
def filter_bb_rsi(df, idx):
    if idx < 20:
        return False
    # 前一天收盘跌破布林下轨 且 RSI-6 < 30
    prev = idx - 1
    return (df.loc[prev, 'Close'] < df.loc[prev, 'bb_lower']) and (df.loc[prev, 'rsi6'] < 30)

trades_bb = run_backtest(daily, dates, day_arrays, prev_close_map,
                         entry_th, exit_tg, max_hold, filter_bb_rsi)
print("\n[4] + Bollinger下轨 + RSI-6 < 30")
tdf_bb = print_summary(trades_bb, "BB+RSI6")

# 放宽BB
def filter_bb_only(df, idx):
    if idx < 20:
        return False
    prev = idx - 1
    return df.loc[prev, 'Close'] < df.loc[prev, 'bb_lower']

trades_bb2 = run_backtest(daily, dates, day_arrays, prev_close_map,
                          entry_th, exit_tg, max_hold, filter_bb_only)
print("\n[4b] + Bollinger下轨（无RSI限制）")
tdf_bb2 = print_summary(trades_bb2, "BB only")

# ============ 5. 组合：MA200 + IBS ============
def filter_ma_ibs(df, idx):
    if idx < 200:
        return False
    if df.loc[idx, 'Close'] <= df.loc[idx, 'ma200']:
        return False
    if df.loc[idx-1, 'IBS'] >= 0.3:
        return False
    return True

trades_combo = run_backtest(daily, dates, day_arrays, prev_close_map,
                            entry_th, exit_tg, max_hold, filter_ma_ibs)
print("\n[5] MA200 + IBS < 0.3 组合")
tdf_combo = print_summary(trades_combo, "MA200+IBS")

# ============ 6. 纯IBS策略（不用跌幅入场）============
print("\n\n" + "=" * 80)
print("纯IBS策略（不用跌幅触发，IBS < 0.2 收盘买入，IBS > 0.8 卖出）")
print("=" * 80)

ibs_trades = []
holding = False
buy_p = 0
buy_d = ''
for i in range(200, len(daily)):
    row = daily.iloc[i]
    if not holding:
        if row['IBS'] < 0.2 and row['Close'] > row['ma200']:
            buy_p = row['Close']
            buy_d = str(row['date'].date())
            holding = True
    else:
        if row['IBS'] > 0.8 or (pd.Timestamp(row['date']) - pd.Timestamp(buy_d)).days > 10:
            sell_p = row['Close']
            ret = sell_p / buy_p - 1
            reason = 'IBS>0.8' if row['IBS'] > 0.8 else 'EXP'
            ibs_trades.append({'date': buy_d, 'buy': round(buy_p,2),
                              'sell': round(sell_p,2), 'reason': reason, 'ret': ret})
            holding = False

if ibs_trades:
    tdf_ibs_pure = pd.DataFrame(ibs_trades)
    n = len(tdf_ibs_pure)
    wins = (tdf_ibs_pure['ret'] > 0).sum()
    total = tdf_ibs_pure['ret'].sum() * 100
    avg = tdf_ibs_pure['ret'].mean() * 100
    worst = tdf_ibs_pure['ret'].min() * 100
    print(f"  笔数: {n}, 胜率: {wins/n*100:.0f}%")
    print(f"  总收益: {total:+.1f}%, 均收益: {avg:+.2f}%, 最大亏损: {worst:+.2f}%")

# ============ 打印逐笔对比 ============
print("\n\n" + "=" * 80)
print("逐笔对比：基准 vs MA200 vs BB")
print("=" * 80)
print_trades(trades_base, "基准(无过滤)")
if trades_ma:
    print_trades(trades_ma, "MA200过滤")
if trades_bb2:
    print_trades(trades_bb2, "BB过滤")
