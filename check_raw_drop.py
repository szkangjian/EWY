import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

def verify_raw_gap():
    print("Fetching strictly UNADJUSTED STRC daily data...")
    ticker = yf.Ticker("STRC")
    
    dividends = ticker.dividends
    if dividends.empty:
        return
        
    dividends.index = dividends.index.tz_localize(None)
    div_dates = dividends.index
    
    # 强制不复权
    df = yf.download("STRC", period="2y", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_localize(None)
    
    print("\n========== STRC 真实除权跳空幅度 (完全无复权原始数据) ==========\n")
    print(f"{'Ex-Date':<15} | {'Div Amt':<10} | {'T-1 Close':<10} | {'T+0 Open':<10} | {'Raw Gap Down':<15} | {'Cushion (Div - Gap)':<20}|")
    print("-" * 95)
    
    for div_date in div_dates:
        if div_date < pd.to_datetime('2025-08-01'):
            continue
            
        future_dates = df.index[df.index >= div_date]
        if len(future_dates) == 0:
            continue
        
        t0_date = future_dates[0]
        if t0_date not in df.index:
            continue
            
        t0_open = df.loc[t0_date, 'Open']
        
        # 找 T-1
        past_dates = df.index[df.index < t0_date]
        if len(past_dates) == 0:
            continue
            
        t1_date = past_dates[-1]
        t1_close = df.loc[t1_date, 'Close']
        
        div_amt = dividends.loc[div_date]
        gap_down = t1_close - t0_open
        cushion = div_amt - gap_down
        
        print(f"{div_date.date().isoformat():<15} | ${div_amt:<9.3f} | ${t1_close:<9.2f} | ${t0_open:<9.2f} | ${gap_down:<14.2f} | ${cushion:<19.3f}|")

if __name__ == '__main__':
    verify_raw_gap()
