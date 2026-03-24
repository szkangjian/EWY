import requests
import pandas as pd
import yfinance as yf
from config import POLYGON_KEY
import warnings
warnings.filterwarnings('ignore')

def analyze_announcement_trajectory():
    print("Fetching dividend declaration dates from Polygon API...")
    url = f"https://api.polygon.io/v3/reference/dividends?ticker=STRC&apiKey={POLYGON_KEY}"
    
    try:
         resp = requests.get(url).json()
         if 'results' not in resp:
             print("No dividend data from Polygon.")
             return
         div_data = resp['results']
    except Exception as e:
         print(f"Error fetching from Polygon: {e}")
         return
         
    # 提取并排序数据
    divs = []
    for d in div_data:
        ex_date = pd.to_datetime(d.get('ex_dividend_date')).date() if d.get('ex_dividend_date') else None
        if not ex_date:
            continue
        # Polygon API 没有提供 ETF 的 declaration_date，按照惯例 SEC 8-K 在除息日前 5-7 天发布，我们取 -6 天作为估算公告日
        est_dec_date = ex_date - pd.Timedelta(days=6)
        divs.append({
            'cash_amount': d.get('cash_amount', 0),
            'declaration_date': est_dec_date,
            'ex_dividend_date': ex_date
        })
        
    divs_df = pd.DataFrame(divs).dropna(subset=['declaration_date']).sort_values('ex_dividend_date')
    
    print("Fetching unadjusted STRC daily data from Yahoo Finance...")
    df = yf.download("STRC", period="2y", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_localize(None)
    daily_close = df['Close']
    
    print("\n========== STRC 股息公告后 10日股价运动轨迹 (T0为公告日) ==========\n")
    
    header = f"{'DeclareDate':<15} | {'Div Amt':<7} | {'Diff':<7} | {'T-1':<6} | {'T0(Ann)':<7} | "
    for i in range(1, 11):
        header += f"{'T+'+str(i):<6} | "
    print(header)
    print("-" * 135)
    
    avg_trajectory = {}
    valid_count = 0
    prev_amt = 0
    
    for _, row in divs_df.iterrows():
        dec_date = pd.to_datetime(row['declaration_date'])
        amt = row['cash_amount']
        diff = amt - prev_amt if prev_amt else 0
        prev_amt = amt
        
        if dec_date < pd.to_datetime('2025-08-01'):
            continue
            
        future_dates = daily_close.index[daily_close.index >= dec_date]
        if len(future_dates) == 0:
            continue
            
        # 实际交易日T0
        t0_date = future_dates[0]
        t0_idx = daily_close.index.get_loc(t0_date)
        
        # 获取 T-1
        t_minus_1_price = daily_close.iloc[t0_idx - 1] if t0_idx >= 1 else 0
        
        diff_str = f"+{diff:.2f}" if diff >= 0 else f"{diff:.2f}"
        if abs(diff) < 0.001: diff_str = " 0.00 "
        
        row_str = f"{dec_date.date().isoformat():<15} | ${amt:<6.2f} | {diff_str:<7} | {t_minus_1_price:<6.2f} | "
        
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
        
        if len([p for p in trajectory if p is not None]) == 11:
            valid_count += 1
            for i, p in enumerate(trajectory):
                avg_trajectory[i] = avg_trajectory.get(i, 0) + p
            avg_trajectory['T-1'] = avg_trajectory.get('T-1', 0) + t_minus_1_price
            
    if valid_count > 0:
        print("-" * 135)
        avg_row = f"{'AVERAGE':<15} | {'--':<7} | {'--':<7} | {avg_trajectory['T-1']/valid_count:<6.2f} | "
        for i in range(0, 11):
            avg_row += f"{avg_trajectory[i]/valid_count:<6.2f} | "
        print(avg_row)

if __name__ == '__main__':
    analyze_announcement_trajectory()
