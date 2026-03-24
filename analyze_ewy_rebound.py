"""
EWY 除息后 10日修复轨迹分析
使用未复权价格，观察除息后价格恢复模式
"""
import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings('ignore')


def analyze_rebound_trajectory():
    print("Fetching unadjusted EWY daily data from Yahoo Finance...")
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
    daily_close = df['Close']

    print("\n========== EWY 除息后 10日修复轨迹 (T+0 到 T+10 每日收盘价) ==========\n")

    # 打印表头
    header = f"{'Ex-Date':<11} | {'Div':<6} | {'T-1':<6} | {'T+0':<6} | "
    for i in range(1, 11):
        header += f"{'T+'+str(i):<6} | "
    header += "恢复?"
    print(header)
    print("-" * 120)

    avg_trajectory = {}
    valid_count = 0

    for div_date in div_dates:
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
        row_str = f"{div_date.date().isoformat():<11} | {div_amt:<6.2f} | {t_minus_1_price:<6.2f} | "

        trajectory = []
        recovered = False
        for i in range(0, 11):
            if t0_idx + i < len(daily_close):
                price = daily_close.iloc[t0_idx + i]
                row_str += f"{price:<6.2f} | "
                trajectory.append(price)
                if i > 0 and price >= t_minus_1_price:
                    recovered = True
            else:
                row_str += f"{'N/A':<6} | "
                trajectory.append(None)

        row_str += "✅" if recovered else "❌"
        print(row_str)

        # 统计平均修复轨迹
        if len([p for p in trajectory if p is not None]) == 11:
            valid_count += 1
            for i, p in enumerate(trajectory):
                avg_trajectory[i] = avg_trajectory.get(i, 0) + p
            avg_trajectory['T-1'] = avg_trajectory.get('T-1', 0) + t_minus_1_price

    if valid_count > 0:
        print("-" * 120)
        avg_row = f"{'AVERAGE':<11} | {'--':<6} | {avg_trajectory['T-1']/valid_count:<6.2f} | "
        for i in range(0, 11):
            avg_row += f"{avg_trajectory[i]/valid_count:<6.2f} | "
        print(avg_row)

        # 以百分比形式展示相对 T-1 的恢复
        print(f"\n📊 平均恢复轨迹 (相对 T-1 收盘价的变化百分比):")
        avg_t1 = avg_trajectory['T-1'] / valid_count
        for i in range(0, 11):
            avg_p = avg_trajectory[i] / valid_count
            pct = (avg_p - avg_t1) / avg_t1 * 100
            bar = "█" * max(0, int(abs(pct) * 10)) if pct >= 0 else "▒" * int(abs(pct) * 10)
            sign = "+" if pct >= 0 else ""
            print(f"   T+{i:<2d}: {sign}{pct:.2f}% {bar}")


if __name__ == '__main__':
    analyze_rebound_trajectory()
