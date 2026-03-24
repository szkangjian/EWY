import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

print("加载数据...")
df = pd.read_csv('ewy_minute_data.csv', parse_dates=['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
df['date'] = df['timestamp'].dt.date

dates = sorted(df['date'].unique())
day_arrays = {}
prev_close_map = {}

for d in dates:
    mask = df['date'] == d
    day_df = df[mask].sort_values('timestamp')
    day_arrays[d] = (day_df['High'].values, day_df['Low'].values, day_df['Close'].values)
    prev_close_map[d] = day_df.iloc[-1]['Close']

print(f"共 {len(dates)} 个交易日, {len(df)} 条分钟数据\n")

entry_thresholds = [-0.03, -0.035, -0.04, -0.045, -0.05]
exit_targets     = [0.01, 0.015, 0.02, 0.025, 0.03]
max_hold_days    = [1, 2, 3, 5]
stop_losses      = [None, -0.02, -0.03, -0.05]

results = []

for entry_th in entry_thresholds:
    for exit_tg in exit_targets:
        for max_hold in max_hold_days:
            for stop_loss in stop_losses:
                trades = []
                i = 1
                while i < len(dates):
                    d = dates[i]
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

                    # scan from trigger minute onward, same day
                    rh = highs[tidx:]
                    rl = lows[tidx:]
                    found = False
                    for k in range(len(rh)):
                        if stop_loss is not None and (rl[k] / buy_price - 1) <= stop_loss:
                            sell_price = buy_price * (1 + stop_loss)
                            sell_reason = 'SL'
                            found = True
                            break
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
                                if stop_loss is not None and (nl[k] / buy_price - 1) <= stop_loss:
                                    sell_price = buy_price * (1 + stop_loss)
                                    sell_reason = 'SL'
                                    sell_day_idx = ni
                                    found = True
                                    break
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

                if not trades:
                    continue

                tdf = pd.DataFrame(trades)
                wins = (tdf['ret'] > 0).sum()
                n = len(tdf)
                total = tdf['ret'].sum() * 100
                avg = tdf['ret'].mean() * 100
                worst = tdf['ret'].min() * 100

                sl_s = f"{stop_loss*100:.0f}%" if stop_loss else "无"
                results.append({
                    'entry': f"{entry_th*100:.1f}%", 'exit': f"+{exit_tg*100:.1f}%",
                    'hold': f"{max_hold}天", 'stop': sl_s,
                    'n': n, 'wr': f"{wins/n*100:.0f}%", 'total': total,
                    'avg': avg, 'worst': worst, 'wins': wins, '_t': trades
                })

rdf = pd.DataFrame(results).sort_values('total', ascending=False)

print("=" * 105)
print("EWY 盘中均值回归 - 参数扫描（分钟级）")
print("=" * 105)
print(f"\n{'入场':>7} | {'反弹':>6} | {'持有':>4} | {'止损':>4} | {'笔数':>4} | {'胜率':>4} | {'总收益':>7} | {'均收益':>6} | {'最大亏':>7}")
print("-" * 105)
for _, r in rdf.head(30).iterrows():
    print(f"{r['entry']:>7} | {r['exit']:>6} | {r['hold']:>4} | {r['stop']:>4} | {r['n']:>4} | {r['wr']:>4} | {r['total']:>+6.1f}% | {r['avg']:>+5.2f}% | {r['worst']:>+6.2f}%")
print(f"\n共 {len(rdf)} 种参数组合")

print("\n" + "=" * 80)
print("持有天数影响（入场-4%, 反弹+2%, 无止损）")
print("=" * 80)
for mh in [1,2,3,5]:
    s = rdf[(rdf['entry']=='-4.0%')&(rdf['exit']=='+2.0%')&(rdf['hold']==f'{mh}天')&(rdf['stop']=='无')]
    if len(s)>0:
        r=s.iloc[0]
        print(f"  {mh}天: {r['n']}笔, 胜率{r['wr']}, 总{r['total']:+.1f}%, 均{r['avg']:+.2f}%, 最大亏{r['worst']:+.2f}%")

print("\n" + "=" * 80)
print("止损影响（入场-4%, 反弹+2%, 持有3天）")
print("=" * 80)
for sl in ['无','-2%','-3%','-5%']:
    s = rdf[(rdf['entry']=='-4.0%')&(rdf['exit']=='+2.0%')&(rdf['hold']=='3天')&(rdf['stop']==sl)]
    if len(s)>0:
        r=s.iloc[0]
        print(f"  止损{sl:>4}: {r['n']}笔, 胜率{r['wr']}, 总{r['total']:+.1f}%, 均{r['avg']:+.2f}%, 最大亏{r['worst']:+.2f}%")

# 最优无止损逐笔
print("\n" + "=" * 80)
best = rdf[rdf['stop']=='无'].iloc[0]
print(f"最优无止损参数逐笔: 入场{best['entry']}, 反弹{best['exit']}, 持有{best['hold']}")
print("=" * 80)
print(f"{'日期':>12} | {'买入':>7} | {'卖出':>7} | {'收益':>7} | 原因")
print("-" * 55)
for t in best['_t']:
    print(f"{t['date']:>12} | ${t['buy']:>6.2f} | ${t['sell']:>6.2f} | {t['ret']*100:>+6.2f}% | {t['reason']}")

# 最优带止损逐笔
print("\n" + "=" * 80)
best_sl = rdf[rdf['stop']!='无'].sort_values('total', ascending=False).iloc[0]
print(f"最优带止损参数逐笔: 入场{best_sl['entry']}, 反弹{best_sl['exit']}, 持有{best_sl['hold']}, 止损{best_sl['stop']}")
print("=" * 80)
print(f"{'日期':>12} | {'买入':>7} | {'卖出':>7} | {'收益':>7} | 原因")
print("-" * 55)
for t in best_sl['_t']:
    print(f"{t['date']:>12} | ${t['buy']:>6.2f} | ${t['sell']:>6.2f} | {t['ret']*100:>+6.2f}% | {t['reason']}")
