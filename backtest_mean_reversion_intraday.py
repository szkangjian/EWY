"""
EWY 盘中均值回归策略回测（分钟级）

逻辑：
  - 实时监控 EWY 价格 vs 前日收盘
  - 当盘中跌幅达到阈值时立即买入（用当分钟收盘价）
  - 买入后监控分钟级数据，触及反弹目标则卖出
  - 超过最大持有时间则强制平仓

对比三种买入时机：
  A) 盘中触发即买（首次跌破阈值的那一分钟）
  B) 收盘买入（等当天收盘确认跌幅后买）
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')


def load_data(csv_path="ewy_minute_data.csv"):
    print("加载分钟数据...")
    df = pd.read_csv(csv_path, parse_dates=['timestamp'])
    df['date'] = df['timestamp'].dt.date
    print(f"  {len(df)} 条分钟数据")

    # 日线用于确定前日收盘
    daily = df.groupby('date').agg(
        day_open=('Open', 'first'),
        day_close=('Close', 'last'),
    ).reset_index()
    daily['date'] = pd.to_datetime(daily['date']).dt.date
    daily = daily.sort_values('date').reset_index(drop=True)
    daily['prev_close'] = daily['day_close'].shift(1)

    # 合并前日收盘到分钟数据
    prev_close_map = dict(zip(daily['date'], daily['prev_close']))
    df['prev_close'] = df['date'].map(prev_close_map)
    df = df.dropna(subset=['prev_close'])
    df['drop_from_prev'] = (df['Close'] / df['prev_close'] - 1)

    print(f"  有效分钟数据: {len(df)} 条 ({df['date'].min()} → {df['date'].max()})")
    return df, daily


def backtest_intraday_trigger(df, daily, entry_threshold, exit_rebound, max_hold_days):
    """
    盘中触发策略：
    - 扫描每分钟，当 drop_from_prev <= entry_threshold 时买入
    - 从买入那一分钟起，在后续分钟中寻找 exit_rebound
    - 如果 max_hold_days 内未触发，按最后一分钟收盘价平仓
    """
    trades = []
    dates = sorted(df['date'].unique())
    skip_until = None

    for d in dates:
        if skip_until and d <= skip_until:
            continue

        day_data = df[df['date'] == d].sort_values('timestamp')
        if day_data.empty:
            continue

        # 找当天第一次跌破阈值的分钟
        trigger = day_data[day_data['drop_from_prev'] <= entry_threshold]
        if trigger.empty:
            continue

        # 买入：第一次触发的那分钟
        buy_row = trigger.iloc[0]
        buy_price = buy_row['Close']
        buy_time = buy_row['timestamp']
        buy_date = d
        trigger_drop = buy_row['drop_from_prev']

        # 从买入时刻开始，在后续分钟中找出场
        # 包含买入日剩余时间 + 后续 max_hold_days
        d_idx = list(dates).index(d)
        end_d_idx = min(d_idx + max_hold_days, len(dates) - 1)
        end_date = dates[end_d_idx]

        future = df[(df['timestamp'] > buy_time) & (df['date'] <= end_date)].sort_values('timestamp')

        target_price = buy_price * (1 + exit_rebound)
        sold = False

        for _, row in future.iterrows():
            if row['High'] >= target_price:
                # 触及目标
                sell_price = target_price
                sell_time = row['timestamp']
                trades.append({
                    'buy_date': buy_date,
                    'buy_time': buy_time,
                    'buy_price': buy_price,
                    'trigger_drop': trigger_drop,
                    'sell_date': row['date'],
                    'sell_time': sell_time,
                    'sell_price': sell_price,
                    'pnl_pct': exit_rebound * 100,
                    'exit_type': '反弹达标',
                    'hold_minutes': (sell_time - buy_time).total_seconds() / 60
                })
                sold = True
                skip_until = row['date']
                break

        if not sold:
            # 强制平仓：最后一分钟
            if not future.empty:
                last = future.iloc[-1]
                sell_price = last['Close']
                trades.append({
                    'buy_date': buy_date,
                    'buy_time': buy_time,
                    'buy_price': buy_price,
                    'trigger_drop': trigger_drop,
                    'sell_date': last['date'],
                    'sell_time': last['timestamp'],
                    'sell_price': sell_price,
                    'pnl_pct': (sell_price / buy_price - 1) * 100,
                    'exit_type': '到期平仓',
                    'hold_minutes': (last['timestamp'] - buy_time).total_seconds() / 60
                })
            skip_until = end_date

    return pd.DataFrame(trades)


def backtest_close_trigger(df, daily, entry_threshold, exit_rebound, max_hold_days):
    """
    收盘触发策略（原策略）：
    - 当天收盘跌幅 >= entry_threshold 时，以收盘价买入
    - 从次日开始，在分钟数据中找 exit_rebound
    """
    trades = []
    dates = sorted(df['date'].unique())

    daily_map = {}
    for d in dates:
        day_data = df[df['date'] == d].sort_values('timestamp')
        if not day_data.empty:
            daily_map[d] = {
                'close': day_data.iloc[-1]['Close'],
                'prev_close': day_data.iloc[0]['prev_close'],
            }

    skip_until = None
    for i, d in enumerate(dates):
        if skip_until and d <= skip_until:
            continue
        if d not in daily_map:
            continue

        info = daily_map[d]
        day_ret = (info['close'] / info['prev_close'] - 1) if info['prev_close'] else 0

        if day_ret > entry_threshold:
            continue

        buy_price = info['close']
        buy_date = d

        # 从次日开始找出场
        end_idx = min(i + max_hold_days, len(dates) - 1)
        end_date = dates[end_idx]

        future = df[(df['date'] > d) & (df['date'] <= end_date)].sort_values('timestamp')
        target_price = buy_price * (1 + exit_rebound)
        sold = False

        for _, row in future.iterrows():
            if row['High'] >= target_price:
                sell_price = target_price
                trades.append({
                    'buy_date': buy_date,
                    'buy_time': pd.Timestamp(f'{buy_date} 16:00:00'),
                    'buy_price': buy_price,
                    'trigger_drop': day_ret,
                    'sell_date': row['date'],
                    'sell_time': row['timestamp'],
                    'sell_price': sell_price,
                    'pnl_pct': exit_rebound * 100,
                    'exit_type': '反弹达标',
                    'hold_minutes': (row['timestamp'] - pd.Timestamp(f'{buy_date} 16:00:00')).total_seconds() / 60
                })
                sold = True
                skip_until = row['date']
                break

        if not sold and not future.empty:
            last = future.iloc[-1]
            sell_price = last['Close']
            trades.append({
                'buy_date': buy_date,
                'buy_time': pd.Timestamp(f'{buy_date} 16:00:00'),
                'buy_price': buy_price,
                'trigger_drop': day_ret,
                'sell_date': last['date'],
                'sell_time': last['timestamp'],
                'sell_price': sell_price,
                'pnl_pct': (sell_price / buy_price - 1) * 100,
                'exit_type': '到期平仓',
                'hold_minutes': (last['timestamp'] - pd.Timestamp(f'{buy_date} 16:00:00')).total_seconds() / 60
            })
            skip_until = end_date

    return pd.DataFrame(trades)


def print_trades(trades_df, label):
    if trades_df.empty:
        print(f"  {label}: 无交易")
        return

    wins = trades_df[trades_df['pnl_pct'] > 0]
    print(f"\n  {label}")
    print(f"  交易次数: {len(trades_df)} | 胜率: {len(wins)/len(trades_df)*100:.1f}% | "
          f"平均收益: {trades_df['pnl_pct'].mean():+.2f}% | 总收益: {trades_df['pnl_pct'].sum():+.1f}%")
    print(f"  最大盈利: {trades_df['pnl_pct'].max():+.2f}% | 最大亏损: {trades_df['pnl_pct'].min():+.2f}%")

    avg_hold_hrs = trades_df['hold_minutes'].mean() / 60
    print(f"  平均持有: {avg_hold_hrs:.1f} 小时 | 反弹达标比例: {(trades_df['exit_type']=='反弹达标').sum()/len(trades_df)*100:.0f}%")

    print(f"\n  {'买入日期':>12} {'买入时间':>8} | {'买入价':>8} | {'触发跌幅':>8} | {'卖出日':>12} {'卖出时间':>8} | {'卖出价':>8} | {'收益':>8} | {'持有':>8} | {'出场':<8}")
    print(f"  {'-'*115}")

    for _, t in trades_df.iterrows():
        buy_t = t['buy_time'].strftime('%H:%M') if pd.notna(t['buy_time']) else ''
        sell_t = t['sell_time'].strftime('%H:%M') if pd.notna(t['sell_time']) else ''
        hold_hrs = t['hold_minutes'] / 60
        print(f"  {str(t['buy_date']):>12} {buy_t:>8} | ${t['buy_price']:>7.2f} | {t['trigger_drop']*100:>+6.2f}% | "
              f"{str(t['sell_date']):>12} {sell_t:>8} | ${t['sell_price']:>7.2f} | {t['pnl_pct']:>+6.2f}% | {hold_hrs:>5.1f}hr | {t['exit_type']:<8}")


def main():
    df, daily = load_data()

    # 核心参数组合
    configs = [
        (-0.03, 0.02, 3, "跌3%→反弹2%→3天"),
        (-0.03, 0.03, 3, "跌3%→反弹3%→3天"),
        (-0.04, 0.02, 3, "跌4%→反弹2%→3天"),
        (-0.04, 0.03, 3, "跌4%→反弹3%→3天"),
        (-0.04, 0.03, 5, "跌4%→反弹3%→5天"),
        (-0.05, 0.03, 3, "跌5%→反弹3%→3天"),
    ]

    print(f"\n{'='*120}")
    print("EWY 盘中均值回归策略 — 分钟级回测")
    print(f"{'='*120}")

    # 汇总对比表
    summary = []

    for entry, rebound, hold, label in configs:
        print(f"\n{'='*120}")
        print(f"参数: {label}")
        print(f"{'='*120}")

        # 策略A：盘中触发
        trades_a = backtest_intraday_trigger(df, daily, entry, rebound, hold)
        print_trades(trades_a, "【策略A】盘中首次跌破即买")

        # 策略B：收盘触发
        trades_b = backtest_close_trigger(df, daily, entry, rebound, hold)
        print_trades(trades_b, "【策略B】收盘确认后买入")

        # 汇总
        for name, t_df in [("盘中买", trades_a), ("收盘买", trades_b)]:
            if not t_df.empty:
                wins = t_df[t_df['pnl_pct'] > 0]
                summary.append({
                    'params': label, 'timing': name,
                    'trades': len(t_df),
                    'win_rate': len(wins)/len(t_df)*100,
                    'avg_pnl': t_df['pnl_pct'].mean(),
                    'total_pnl': t_df['pnl_pct'].sum(),
                    'avg_hold_hrs': t_df['hold_minutes'].mean()/60,
                    'rebound_pct': (t_df['exit_type']=='反弹达标').sum()/len(t_df)*100,
                })

    # 汇总对比
    print(f"\n{'='*120}")
    print("总结：盘中买 vs 收盘买 对比")
    print(f"{'='*120}")

    print(f"\n{'参数':<25} | {'时机':<8} | {'次数':>5} | {'胜率':>7} | {'平均收益':>8} | {'总收益':>8} | {'均持有':>8} | {'达标率':>7}")
    print("-" * 100)
    for s in summary:
        print(f"{s['params']:<25} | {s['timing']:<8} | {s['trades']:>5} | {s['win_rate']:>5.1f}% | "
              f"{s['avg_pnl']:>+6.2f}% | {s['total_pnl']:>+6.1f}% | {s['avg_hold_hrs']:>5.1f}hr | {s['rebound_pct']:>5.0f}%")


if __name__ == '__main__':
    main()
