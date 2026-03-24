"""
EWY 除息日真实跳空幅度 vs 派息金额
合并了 adjusted 和 unadjusted 两种视角的分析
"""
import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings('ignore')


def analyze_div_gap():
    print("Fetching EWY data from Yahoo Finance...")
    ticker = yf.Ticker("EWY")

    # 获取历史分红
    dividends = ticker.dividends
    if dividends.empty:
        print("No dividend data found.")
        return

    dividends.index = dividends.index.tz_localize(None)
    div_dates = dividends.index

    # 获取未复权的真实历史价格
    df = yf.download("EWY", period="5y", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_localize(None)

    # 也获取复权数据做对比
    hist = ticker.history(period="5y")
    hist.index = hist.index.tz_localize(None)

    print("\n========== EWY 除息日真实跳空幅度 vs 派息金额 (未复权原始数据) ==========\n")
    print(f"{'Ex-Date':<15} | {'Div Amt':<10} | {'T-1 Close':<10} | {'T+0 Open':<10} | {'Raw Gap':<10} | {'Cushion':<10} | {'Div Yield':<10}|")
    print("-" * 95)

    total_cushion = 0
    count = 0

    for div_date in div_dates:
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
        div_yield = div_amt / t1_close * 100

        total_cushion += cushion
        count += 1

        print(f"{div_date.date().isoformat():<15} | ${div_amt:<9.3f} | ${t1_close:<9.2f} | ${t0_open:<9.2f} | ${gap_down:<9.2f} | ${cushion:<9.3f} | {div_yield:<9.2f}%|")

    if count > 0:
        print("-" * 95)
        print(f"{'AVERAGE':<15} | {'':10s} | {'':10s} | {'':10s} | {'':10s} | ${total_cushion/count:<9.3f} |")
        print(f"\n📊 分析结论:")
        print(f"   - 共分析 {count} 次除息")
        print(f"   - 平均 Cushion: ${total_cushion/count:.3f}")
        if total_cushion/count > 0:
            print(f"   - 除息日开盘跌幅平均小于股息金额 → 存在 Dividend Capture 机会")
        else:
            print(f"   - 除息日开盘跌幅平均大于股息金额 → Dividend Capture 不利")


if __name__ == '__main__':
    analyze_div_gap()
