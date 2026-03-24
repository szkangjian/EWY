import yfinance as yf
import pandas as pd

def analyze_div_gap():
    print("Fetching STRC data from Yahoo Finance...")
    ticker = yf.Ticker("STRC")
    
    # 获取历史分红
    dividends = ticker.dividends
    if dividends.empty:
        print("No dividend data found.")
        return
        
    dividends.index = dividends.index.tz_localize(None)
    div_dates = dividends.index
    
    # 获取历史日线数据
    hist = ticker.history(period="2y")
    hist.index = hist.index.tz_localize(None)
    
    print("\n========== STRC 除息日真实跳空幅度 vs 派息金额 ==========\n")
    print(f"{'Ex-Date':<15} | {'Div Amt':<10} | {'T-1 Close':<10} | {'T+0 Open':<10} | {'Gap Down':<10} | {'Cushion (Div - Gap)':<20}|")
    print("-" * 90)
    
    for div_date in div_dates:
        # 找到这天或者之后的最近交易日作为 T+0
        future_dates = hist.index[hist.index >= div_date]
        if len(future_dates) == 0:
            continue
        t0_date = future_dates[0]
        t0_open = hist.loc[t0_date, 'Open']
        
        div_amt = dividends.loc[div_date]
        
        # 找 T-1 (找离 t0_date 最近的且小于它的日期)
        past_dates = hist.index[hist.index < t0_date]
        if len(past_dates) == 0:
            continue
            
        t1_date = past_dates[-1]
        t1_close = hist.loc[t1_date, 'Close']
        
        # 计算跳空幅度
        gap_down = t1_close - t0_open
        cushion = div_amt - gap_down  # Cushion 是被买盘托起的幅度
        
        # 格式化输出
        print(f"{div_date.date().isoformat():<15} | ${div_amt:<9.3f} | ${t1_close:<9.2f} | ${t0_open:<9.2f} | ${gap_down:<9.2f} | ${cushion:<19.3f}|")
        
if __name__ == '__main__':
    analyze_div_gap()
