"""
EWY 股息公告后 10日股价运动轨迹
分析 declaration date 公布后市场反应
"""
import requests
import pandas as pd
import yfinance as yf
from config import POLYGON_KEY
import warnings
warnings.filterwarnings('ignore')


def analyze_announcement_trajectory():
    print("Fetching dividend declaration dates from Polygon API...")
    url = f"https://api.polygon.io/v3/reference/dividends?ticker=EWY&limit=50&apiKey={POLYGON_KEY}"

    try:
        resp = requests.get(url).json()
        if 'results' not in resp:
            print("No dividend data from Polygon. Trying yfinance fallback...")
            analyze_from_yfinance()
            return
        div_data = resp['results']
    except Exception as e:
        print(f"Error fetching from Polygon: {e}")
        return

    # 提取并排序数据
    divs = []
    for d in div_data:
        ex_date = pd.to_datetime(d.get('ex_dividend_date')).date() if d.get('ex_dividend_date') else None
        dec_date = d.get('declaration_date')
        if not ex_date:
            continue
        # 如果 Polygon 提供了 declaration_date 则使用，否则估算 (ex_date - 7天)
        if dec_date:
            est_dec_date = pd.to_datetime(dec_date).date()
        else:
            est_dec_date = ex_date - pd.Timedelta(days=7)
        divs.append({
            'cash_amount': d.get('cash_amount', 0),
            'declaration_date': est_dec_date,
            'ex_dividend_date': ex_date
        })

    divs_df = pd.DataFrame(divs).dropna(subset=['declaration_date']).sort_values('ex_dividend_date')

    print("Fetching unadjusted EWY daily data from Yahoo Finance...")
    df = yf.download("EWY", period="5y", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_localize(None)
    daily_close = df['Close']

    # 只保留在价格数据范围内的除息记录
    price_start = daily_close.index.min()
    divs_df = divs_df[divs_df['declaration_date'].apply(lambda x: pd.to_datetime(x)) >= price_start]

    print("\n========== EWY 股息公告后 10日股价运动轨迹 (T0为公告日) ==========\n")

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

        future_dates = daily_close.index[daily_close.index >= dec_date]
        if len(future_dates) == 0:
            continue

        # 实际交易日T0
        t0_date = future_dates[0]
        t0_idx = daily_close.index.get_loc(t0_date)

        # 获取 T-1
        t_minus_1_price = daily_close.iloc[t0_idx - 1] if t0_idx >= 1 else 0

        diff_str = f"+{diff:.2f}" if diff >= 0 else f"{diff:.2f}"
        if abs(diff) < 0.001:
            diff_str = " 0.00 "

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


def analyze_from_yfinance():
    """备用方案：用 yfinance 的除息日估算公告日"""
    ticker = yf.Ticker("EWY")
    dividends = ticker.dividends
    if dividends.empty:
        print("No dividend data found.")
        return
    dividends.index = dividends.index.tz_localize(None)
    print(f"Found {len(dividends)} dividend records from yfinance.")
    print("Note: yfinance does not provide declaration dates, using ex_date - 7 days as estimate.")
    # 简化处理：将除息日往前推 7 天作为公告日
    for div_date, amt in dividends.items():
        est_dec = div_date - pd.Timedelta(days=7)
        print(f"  Ex-date: {div_date.date()} | Amt: ${amt:.3f} | Est. declaration: {est_dec.date()}")


if __name__ == '__main__':
    analyze_announcement_trajectory()
