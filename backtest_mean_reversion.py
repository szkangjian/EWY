"""
EWY 大跌后短期反弹策略回测
事件驱动的均值回归：跌幅超阈值时买入，反弹或到期卖出

参数网格：
  - 入场阈值：日跌幅 -2%, -3%, -4%, -5%
  - 出场方式：反弹 1%/2%/3% 或持有 1/2/3/5 天（先到先出）
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')


def load_daily_data(csv_path="ewy_minute_data.csv"):
    """从分钟数据聚合为日线"""
    print("加载分钟数据并聚合为日线...")
    df = pd.read_csv(csv_path, parse_dates=['timestamp'])
    df['date'] = df['timestamp'].dt.date

    daily = df.groupby('date').agg(
        Open=('Open', 'first'),
        High=('High', 'max'),
        Low=('Low', 'min'),
        Close=('Close', 'last'),
        Volume=('Volume', 'sum')
    ).reset_index()
    daily['date'] = pd.to_datetime(daily['date'])
    daily = daily.sort_values('date').reset_index(drop=True)
    daily['ret'] = daily['Close'].pct_change()
    print(f"  日线数据: {len(daily)} 天 ({daily['date'].iloc[0].date()} → {daily['date'].iloc[-1].date()})")
    return daily


def run_backtest(daily, entry_threshold, exit_rebound, max_hold_days):
    """
    单次回测
    entry_threshold: 负数，如 -0.03 表示日跌 3% 时买入
    exit_rebound: 正数，如 0.02 表示从买入价反弹 2% 时卖出
    max_hold_days: 最大持有天数，到期强制卖出
    """
    trades = []
    i = 0
    n = len(daily)

    while i < n:
        if daily['ret'].iloc[i] <= entry_threshold:
            # 信号日收盘买入
            buy_date = daily['date'].iloc[i]
            buy_price = daily['Close'].iloc[i]
            buy_day_ret = daily['ret'].iloc[i]

            # 寻找出场点
            sold = False
            for j in range(1, max_hold_days + 1):
                if i + j >= n:
                    break
                # 检查当天最高价是否触及反弹目标
                day_high = daily['High'].iloc[i + j]
                day_close = daily['Close'].iloc[i + j]
                target_price = buy_price * (1 + exit_rebound)

                if day_high >= target_price:
                    # 触及目标，以目标价成交
                    sell_price = target_price
                    sell_date = daily['date'].iloc[i + j]
                    hold_days = j
                    exit_type = "反弹达标"
                    trades.append({
                        'buy_date': buy_date,
                        'buy_price': buy_price,
                        'trigger_drop': buy_day_ret,
                        'sell_date': sell_date,
                        'sell_price': sell_price,
                        'hold_days': hold_days,
                        'pnl_pct': (sell_price / buy_price - 1) * 100,
                        'exit_type': exit_type
                    })
                    sold = True
                    i = i + j + 1  # 卖出后从下一天继续
                    break

            if not sold:
                # 到期强制卖出（收盘价）
                sell_idx = min(i + max_hold_days, n - 1)
                sell_price = daily['Close'].iloc[sell_idx]
                sell_date = daily['date'].iloc[sell_idx]
                hold_days = sell_idx - i
                trades.append({
                    'buy_date': buy_date,
                    'buy_price': buy_price,
                    'trigger_drop': buy_day_ret,
                    'sell_date': sell_date,
                    'sell_price': sell_price,
                    'hold_days': hold_days,
                    'pnl_pct': (sell_price / buy_price - 1) * 100,
                    'exit_type': "到期卖出"
                })
                i = sell_idx + 1
            continue
        i += 1

    return pd.DataFrame(trades)


def analyze_trades(trades_df):
    """分析交易结果"""
    if trades_df.empty:
        return {
            'trades': 0, 'win_rate': 0, 'avg_pnl': 0,
            'total_pnl': 0, 'max_win': 0, 'max_loss': 0,
            'avg_hold': 0, 'rebound_exit_pct': 0
        }

    wins = trades_df[trades_df['pnl_pct'] > 0]
    return {
        'trades': len(trades_df),
        'win_rate': len(wins) / len(trades_df) * 100,
        'avg_pnl': trades_df['pnl_pct'].mean(),
        'total_pnl': trades_df['pnl_pct'].sum(),
        'max_win': trades_df['pnl_pct'].max(),
        'max_loss': trades_df['pnl_pct'].min(),
        'avg_hold': trades_df['hold_days'].mean(),
        'rebound_exit_pct': (trades_df['exit_type'] == '反弹达标').sum() / len(trades_df) * 100
    }


def main():
    daily = load_daily_data()

    # 参数网格
    entry_thresholds = [-0.02, -0.03, -0.04, -0.05]
    exit_rebounds = [0.01, 0.02, 0.03]
    max_hold_days_list = [1, 2, 3, 5]

    print(f"\n{'='*100}")
    print("EWY 大跌反弹策略 — 参数网格回测")
    print(f"{'='*100}")

    # 汇总表
    results = []
    for entry in entry_thresholds:
        for rebound in exit_rebounds:
            for hold in max_hold_days_list:
                trades_df = run_backtest(daily, entry, rebound, hold)
                stats = analyze_trades(trades_df)
                stats['entry'] = entry
                stats['rebound'] = rebound
                stats['max_hold'] = hold
                results.append(stats)

    results_df = pd.DataFrame(results)

    # ==========================================
    # 1. 参数网格总览（按总收益排序）
    # ==========================================
    print(f"\n{'='*100}")
    print("一、参数网格总览（按总收益排序 Top 15）")
    print(f"{'='*100}")

    header = f"{'入场阈值':>8} | {'反弹目标':>8} | {'最大持有':>8} | {'交易次数':>8} | {'胜率':>8} | {'平均收益':>8} | {'总收益':>8} | {'最大盈':>8} | {'最大亏':>8} | {'均持有日':>8} | {'达标出场':>8}"
    print(header)
    print("-" * 120)

    top = results_df.sort_values('total_pnl', ascending=False).head(15)
    for _, r in top.iterrows():
        print(f"  {r['entry']*100:>5.0f}% | {r['rebound']*100:>6.0f}% | {r['max_hold']:>6.0f}天 | "
              f"{r['trades']:>7.0f} | {r['win_rate']:>6.1f}% | {r['avg_pnl']:>+6.2f}% | "
              f"{r['total_pnl']:>+7.1f}% | {r['max_win']:>+6.2f}% | {r['max_loss']:>+6.2f}% | "
              f"{r['avg_hold']:>6.1f}天 | {r['rebound_exit_pct']:>6.1f}%")

    # ==========================================
    # 2. 按入场阈值分组汇总
    # ==========================================
    print(f"\n{'='*100}")
    print("二、按入场阈值分组（平均表现）")
    print(f"{'='*100}")

    for entry in entry_thresholds:
        subset = results_df[results_df['entry'] == entry]
        avg_trades = subset['trades'].mean()
        avg_winrate = subset['win_rate'].mean()
        avg_pnl = subset['avg_pnl'].mean()
        print(f"  跌幅 >{abs(entry)*100:.0f}%: 平均 {avg_trades:.0f} 次交易, 胜率 {avg_winrate:.1f}%, 平均单笔 {avg_pnl:+.2f}%")

    # ==========================================
    # 3. 最优策略的逐笔交易明细
    # ==========================================
    best = results_df.sort_values('total_pnl', ascending=False).iloc[0]
    print(f"\n{'='*100}")
    print(f"三、最优策略逐笔明细: 入场 {best['entry']*100:.0f}% | 反弹 {best['rebound']*100:.0f}% | 持有 {best['max_hold']:.0f}天")
    print(f"{'='*100}")

    best_trades = run_backtest(daily, best['entry'], best['rebound'], int(best['max_hold']))
    print(f"\n{'买入日期':<12} | {'买入价':>8} | {'触发跌幅':>8} | {'卖出日期':<12} | {'卖出价':>8} | {'持有天数':>8} | {'收益':>8} | {'出场方式':<8}")
    print("-" * 105)

    for _, t in best_trades.iterrows():
        print(f"{t['buy_date'].strftime('%Y-%m-%d'):<12} | ${t['buy_price']:>7.2f} | {t['trigger_drop']*100:>+6.2f}% | "
              f"{t['sell_date'].strftime('%Y-%m-%d'):<12} | ${t['sell_price']:>7.2f} | {t['hold_days']:>6.0f}天 | "
              f"{t['pnl_pct']:>+6.2f}% | {t['exit_type']:<8}")

    cum_pnl = best_trades['pnl_pct'].cumsum()
    print(f"\n  累计收益: {cum_pnl.iloc[-1]:+.2f}%")
    print(f"  最大回撤（累计收益）: {(cum_pnl - cum_pnl.cummax()).min():+.2f}%")

    # ==========================================
    # 4. 高胜率策略（胜率 > 70% 的）
    # ==========================================
    print(f"\n{'='*100}")
    print("四、高胜率策略（胜率 > 70% 且交易 ≥ 5 次）")
    print(f"{'='*100}")

    high_wr = results_df[(results_df['win_rate'] > 70) & (results_df['trades'] >= 5)]
    high_wr = high_wr.sort_values('avg_pnl', ascending=False)

    if high_wr.empty:
        print("  无满足条件的策略")
    else:
        print(f"{'入场阈值':>8} | {'反弹目标':>8} | {'最大持有':>8} | {'交易次数':>8} | {'胜率':>8} | {'平均收益':>8} | {'总收益':>8}")
        print("-" * 75)
        for _, r in high_wr.iterrows():
            print(f"  {r['entry']*100:>5.0f}% | {r['rebound']*100:>6.0f}% | {r['max_hold']:>6.0f}天 | "
                  f"{r['trades']:>7.0f} | {r['win_rate']:>6.1f}% | {r['avg_pnl']:>+6.2f}% | {r['total_pnl']:>+7.1f}%")

    # ==========================================
    # 5. 按年份拆分表现
    # ==========================================
    print(f"\n{'='*100}")
    print("五、最优策略按年份拆分")
    print(f"{'='*100}")

    best_trades['year'] = best_trades['buy_date'].dt.year
    for year in sorted(best_trades['year'].unique()):
        yr_trades = best_trades[best_trades['year'] == year]
        yr_wins = yr_trades[yr_trades['pnl_pct'] > 0]
        print(f"\n  {year}年: {len(yr_trades)} 笔交易, "
              f"胜率 {len(yr_wins)/len(yr_trades)*100:.1f}%, "
              f"平均收益 {yr_trades['pnl_pct'].mean():+.2f}%, "
              f"总收益 {yr_trades['pnl_pct'].sum():+.2f}%")
        for _, t in yr_trades.iterrows():
            print(f"    {t['buy_date'].strftime('%m-%d')} → {t['sell_date'].strftime('%m-%d')} | "
                  f"${t['buy_price']:.2f} → ${t['sell_price']:.2f} | "
                  f"{t['pnl_pct']:>+6.2f}% | {t['exit_type']}")

    # ==========================================
    # 6. 风险调整后排名（Sharpe-like）
    # ==========================================
    print(f"\n{'='*100}")
    print("六、风险调整后排名（收益/波动比，交易 ≥ 5 次）")
    print(f"{'='*100}")

    valid = results_df[results_df['trades'] >= 5].copy()
    if not valid.empty:
        # 对每个参数组合重新获取交易，计算 pnl 标准差
        sharpe_results = []
        for _, r in valid.iterrows():
            trades_df = run_backtest(daily, r['entry'], r['rebound'], int(r['max_hold']))
            if len(trades_df) >= 5:
                pnl_std = trades_df['pnl_pct'].std()
                sharpe_like = r['avg_pnl'] / pnl_std if pnl_std > 0 else 0
                sharpe_results.append({
                    'entry': r['entry'], 'rebound': r['rebound'], 'max_hold': r['max_hold'],
                    'trades': r['trades'], 'win_rate': r['win_rate'],
                    'avg_pnl': r['avg_pnl'], 'pnl_std': pnl_std,
                    'sharpe': sharpe_like
                })

        sharpe_df = pd.DataFrame(sharpe_results).sort_values('sharpe', ascending=False).head(10)
        print(f"\n{'入场阈值':>8} | {'反弹目标':>8} | {'最大持有':>8} | {'交易次数':>8} | {'胜率':>8} | {'平均收益':>8} | {'收益波动':>8} | {'收益/波动':>8}")
        print("-" * 90)
        for _, r in sharpe_df.iterrows():
            print(f"  {r['entry']*100:>5.0f}% | {r['rebound']*100:>6.0f}% | {r['max_hold']:>6.0f}天 | "
                  f"{r['trades']:>7.0f} | {r['win_rate']:>6.1f}% | {r['avg_pnl']:>+6.2f}% | "
                  f"{r['pnl_std']:>6.2f}% | {r['sharpe']:>+6.3f}")

    print(f"\n{'='*100}")
    print("回测完成")
    print(f"{'='*100}")


if __name__ == '__main__':
    main()
