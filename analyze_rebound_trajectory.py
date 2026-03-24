import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

def analyze_rebound_trajectory():
    print("Fetching unadjusted STRC daily data from Yahoo Finance...")
    ticker = yf.Ticker("STRC")
    
    # 获取历史分红
    dividends = ticker.dividends
    if dividends.empty:
        print("No dividend data found.")
        return
        
    dividends.index = dividends.index.tz_localize(None)
    div_dates = dividends.index
    
    # 关键：获取【未复权】的真实历史价格 (auto_adjust=False)
    # 因为我们要看的是屏幕上真实的标价有没有回到 100
    df = yf.download("STRC", period="2y", auto_adjust=False, progress=False)
    
    # 处理 MultiIndex columns (yfinance 新版行为)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
        
    df.index = df.index.tz_localize(None)
    daily_close = df['Close']
    
    print("\n========== STRC 除息后 10日修复轨迹 (T+0 到 T+10 每日收盘价) ==========\n")
    
    # 打印表头
    header = f"{'Ex-Date':<11} | {'Div':<5} | {'T-1':<6} | {'T+0':<6} | "
    for i in range(1, 11):
        header += f"{'T+'+str(i):<6} | "
    print(header)
    print("-" * 105)
    
    avg_trajectory = {}
    valid_count = 0
    
    for div_date in div_dates:
        # 跳过太早的数据或没有后续几天的数据
        if div_date < pd.to_datetime('2025-08-01'):
            continue
            
        future_dates = daily_close.index[daily_close.index >= div_date]
        if len(future_dates) == 0:
            continue
            
        t0_date = future_dates[0]
        t0_idx = daily_close.index.get_loc(t0_date)
        
        # 获取 T-1
        if t0_idx < 1:
            continue
        t_minus_1_price = daily_close.iloc[t0_idx - 1]
        
        div_amt = dividends.loc[div_date]
        row_str = f"{div_date.date().isoformat():<11} | {div_amt:.2f} | {t_minus_1_price:<6.2f} | "
        
        trajectory = []
        for i in range(0, 11):
            if t0_idx + i < len(daily_close):
                price = daily_close.iloc[t0_idx + i]
                row_str += f"{price:<6.2f} | "
                trajectory.append(price)
            else:
                row_str += f"{'N/A':<6} | "
                trajectory.append(None)
                
        print(row_str)
        
        # 统计平均修复轨迹 (只统计完全走完10天的)
        if len([p for p in trajectory if p is not None]) == 11:
            valid_count += 1
            for i, p in enumerate(trajectory):
                avg_trajectory[i] = avg_trajectory.get(i, 0) + p
            avg_trajectory['T-1'] = avg_trajectory.get('T-1', 0) + t_minus_1_price
            
    if valid_count > 0:
        print("-" * 105)
        avg_row = f"{'AVERAGE':<11} | {'--':<5} | {avg_trajectory['T-1']/valid_count:<6.2f} | "
        for i in range(0, 11):
            avg_row += f"{avg_trajectory[i]/valid_count:<6.2f} | "
        print(avg_row)

if __name__ == '__main__':
    analyze_rebound_trajectory()
