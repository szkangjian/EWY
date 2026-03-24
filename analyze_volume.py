import pandas as pd

def analyze_exdiv_volume():
    # 1. 加载数据
    print("加载数据...")
    df = pd.read_csv('strc_minute_data.csv', parse_dates=['timestamp'])
    
    # 获取纽约时间日期 (这里认为已经本身是纽约时间)
    df['date'] = df['timestamp'].dt.date
    
    # 按天汇总交易量
    daily_vol = df.groupby('date')['Volume'].sum().reset_index()
    daily_vol['date'] = pd.to_datetime(daily_vol['date'])
    daily_vol.set_index('date', inplace=True)
    
    # 我们之前的 6 个交易除息日
    ex_dates = [
        '2025-10-15',
        '2025-11-14',
        '2025-12-15',
        '2026-01-15',
        '2026-02-13',
        '2026-03-13'
    ]
    
    print("\n========== STRC 除息日交易量大揭秘 (机构套利实锤) ==========\n")
    
    for dt_str in ex_dates:
        ex_date = pd.to_datetime(dt_str)
        month_start = ex_date.replace(day=1)
        month_end = month_start + pd.offsets.MonthEnd(1)
        
        # 这个月的日均交易量 (ADV)
        month_data = daily_vol[(daily_vol.index >= month_start) & (daily_vol.index <= month_end)]
        if month_data.empty:
            continue
            
        adv = month_data['Volume'].mean()
        
        # 获取周围几天的交易量
        try:
            # 找到 ex_date 及其前后交易日
            all_dates = daily_vol.index.tolist()
            if ex_date not in all_dates:
                continue
                
            idx = all_dates.index(ex_date)
            
            t_minus_2 = all_dates[idx-2] if idx >= 2 else None
            t_minus_1 = all_dates[idx-1] if idx >= 1 else None
            t_plus_1 = all_dates[idx+1] if idx+1 < len(all_dates) else None
            
            vol_t2 = daily_vol.loc[t_minus_2, 'Volume'] if t_minus_2 else 0
            vol_t1 = daily_vol.loc[t_minus_1, 'Volume'] if t_minus_1 else 0
            vol_t0 = daily_vol.loc[ex_date, 'Volume']
            vol_p1 = daily_vol.loc[t_plus_1, 'Volume'] if t_plus_1 else 0
            
            print(f"【除息日：{dt_str}】")
            print(f"  当月日均平时量 (ADV): {adv:,.0f} 股")
            print(f"  T-2 (前两天):       {vol_t2:,.0f} 股 ({vol_t2/adv:.1f}x)")
            print(f"  T-1 (抢权买入日):   {vol_t1:,.0f} 股 ({vol_t1/adv:.1f}x) <-- 机构抢筹")
            print(f"  T+0 (除息抛售日):   {vol_t0:,.0f} 股 ({vol_t0/adv:.1f}x) <-- 机构获利砸盘")
            print(f"  T+1 (恢复期):       {vol_p1:,.0f} 股 ({vol_p1/adv:.1f}x)")
            print("-" * 50)
            
        except Exception as e:
            print(f"Error processing {dt_str}: {e}")

if __name__ == '__main__':
    analyze_exdiv_volume()
