import pandas as pd

def analyze_window_volatility():
    print("加载并处理分钟级数据...")
    df = pd.read_csv('strc_minute_data.csv', parse_dates=['timestamp'])
    
    # 获取纽约时间日期和时间
    df['date'] = df['timestamp'].dt.date
    df['time'] = df['timestamp'].dt.time
    
    # 我们之前的 6 个交易除息日
    ex_dates = [
        '2025-10-15',
        '2025-11-14',
        '2025-12-15',
        '2026-01-15',
        '2026-02-13',
        '2026-03-13'
    ]
    
    # 获取所有唯一的交易日
    all_dates = sorted(df['date'].unique())
    
    print("\n========== STRC 交易窗口波动率分析 ==========\n")
    
    for dt_str in ex_dates:
        ex_date = pd.to_datetime(dt_str).date()
        
        if ex_date not in all_dates:
            continue
            
        idx = all_dates.index(ex_date)
        t_minus_1 = all_dates[idx-1] if idx >= 1 else None
        t_plus_0 = ex_date
        
        if not t_minus_1:
            continue
            
        # 1. T-1最后15分钟 (15:45 - 16:00)
        t1_window = df[(df['date'] == t_minus_1) & 
                       (df['time'] >= pd.to_datetime('15:45').time()) & 
                       (df['time'] <= pd.to_datetime('16:00').time())]
        
        # 2. T+0前15分钟 (09:30 - 09:45)
        t0_window = df[(df['date'] == t_plus_0) & 
                       (df['time'] >= pd.to_datetime('09:30').time()) & 
                       (df['time'] <= pd.to_datetime('09:45').time())]
                       
        print(f"【除息日：{dt_str}】")
        
        # 处理 T-1
        if not t1_window.empty:
            t1_vol = t1_window['Volume'].sum()
            t1_high = t1_window['High'].max()
            t1_low = t1_window['Low'].min()
            t1_open = t1_window.iloc[0]['Open']
            t1_close = t1_window.iloc[-1]['Close']
            t1_spread = t1_high - t1_low
            
            print(f"  [T-1] 尾盘抢筹期 (15:45-16:00)")
            print(f"    交易量: {t1_vol:,.0f} 股")
            print(f"    价位: 开 ${t1_open:.2f} -> 收 ${t1_close:.2f} | 最高 ${t1_high:.2f} / 最低 ${t1_low:.2f}")
            print(f"    15分钟最大波动差价: ${t1_spread:.2f} ({(t1_spread/t1_open)*100:.2f}%)")
        else:
            print(f"  [T-1] 无数据")
            
        # 处理 T+0
        if not t0_window.empty:
            t0_vol = t0_window['Volume'].sum()
            t0_high = t0_window['High'].max()
            t0_low = t0_window['Low'].min()
            t0_open = t0_window.iloc[0]['Open']
            t0_close = t0_window.iloc[-1]['Close']
            t0_spread = t0_high - t0_low
            
            print(f"  [T+0] 早盘抛售期 (09:30-09:45)")
            print(f"    交易量: {t0_vol:,.0f} 股")
            print(f"    价位: 开 ${t0_open:.2f} -> 收 ${t0_close:.2f} | 最高 ${t0_high:.2f} / 最低 ${t0_low:.2f}")
            print(f"    15分钟最大波动差价: ${t0_spread:.2f} ({(t0_spread/t0_open)*100:.2f}%)")
        else:
            print(f"  [T+0] 无数据")
            
        print("-" * 50)

if __name__ == '__main__':
    analyze_window_volatility()
