"""
EWY 相关性分析
- EWY vs SPY (美国大盘 / 全球风险偏好)
- EWY vs EEM (新兴市场 ETF)
- EWY vs SMH (半导体 ETF，三星/SK海力士关联)
- EWY vs USD/KRW 汇率代理 (通过 FXY 日元或直接用 USDKRW=X)
"""
import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')


def analyze_correlations():
    print("📊 EWY 相关性分析")
    print("=" * 70)

    # 下载数据
    tickers = {
        'EWY': 'iShares MSCI South Korea ETF',
        'SPY': 'S&P 500 ETF (美国大盘)',
        'EEM': 'iShares MSCI Emerging Markets (新兴市场)',
        'SMH': 'VanEck Semiconductor ETF (半导体)',
        'SOXX': 'iShares Semiconductor ETF (半导体)',
    }

    print("\n正在下载数据...")
    data = {}
    for ticker in tickers:
        df = yf.download(ticker, period="2y", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.index = df.index.tz_localize(None)
        data[ticker] = df['Close']
        print(f"  ✅ {ticker}: {len(df)} 天")

    # 尝试获取 USD/KRW 汇率
    print("  正在获取 USD/KRW 汇率...")
    try:
        fx = yf.download("KRW=X", period="2y", progress=False)
        if isinstance(fx.columns, pd.MultiIndex):
            fx.columns = fx.columns.droplevel(1)
        fx.index = fx.index.tz_localize(None)
        data['USDKRW'] = fx['Close']
        tickers['USDKRW'] = 'USD/KRW 汇率'
        print(f"  ✅ USD/KRW: {len(fx)} 天")
    except Exception:
        print("  ⚠️  无法获取 USD/KRW 汇率数据")

    # 合并为 DataFrame
    prices = pd.DataFrame(data)
    prices = prices.dropna()
    returns = prices.pct_change().dropna()

    print(f"\n共 {len(returns)} 个交易日的重叠数据")

    # ==========================================
    # 1. 日收益率相关系数矩阵
    # ==========================================
    print(f"\n{'=' * 70}")
    print("一、日收益率相关系数矩阵 (Pearson)")
    print("=" * 70)

    corr = returns.corr()
    print(f"\n{'':>10}", end="")
    for t in corr.columns:
        print(f"{t:>10}", end="")
    print()
    for t1 in corr.index:
        print(f"{t1:>10}", end="")
        for t2 in corr.columns:
            val = corr.loc[t1, t2]
            print(f"{val:>10.3f}", end="")
        print()

    # ==========================================
    # 2. EWY 与各标的的详细相关性
    # ==========================================
    print(f"\n{'=' * 70}")
    print("二、EWY 与各标的相关性详解")
    print("=" * 70)

    for ticker_name in [t for t in tickers if t != 'EWY']:
        if ticker_name not in returns.columns:
            continue
        corr_val = returns['EWY'].corr(returns[ticker_name])
        desc = tickers[ticker_name]

        # 滚动相关性
        rolling_corr = returns['EWY'].rolling(60).corr(returns[ticker_name])

        print(f"\n  📌 EWY vs {ticker_name} ({desc})")
        print(f"     全期相关系数: {corr_val:.3f}")
        print(f"     60日滚动相关系数: 最近={rolling_corr.iloc[-1]:.3f} | 最高={rolling_corr.max():.3f} | 最低={rolling_corr.min():.3f}")

        # Beta 计算
        cov = returns[['EWY', ticker_name]].cov()
        beta = cov.loc['EWY', ticker_name] / returns[ticker_name].var()
        print(f"     Beta (EWY 对 {ticker_name}): {beta:.3f}")

    # ==========================================
    # 3. EWY 收益率分布
    # ==========================================
    print(f"\n{'=' * 70}")
    print("三、EWY 收益率分布特征")
    print("=" * 70)

    ewy_ret = returns['EWY']
    print(f"\n   日收益率统计:")
    print(f"     均值: {ewy_ret.mean()*100:.3f}% ({ewy_ret.mean()*252*100:.1f}% 年化)")
    print(f"     标准差: {ewy_ret.std()*100:.3f}% ({ewy_ret.std()*np.sqrt(252)*100:.1f}% 年化)")
    print(f"     偏度: {ewy_ret.skew():.3f}")
    print(f"     峰度: {ewy_ret.kurtosis():.3f}")
    print(f"     最大单日涨幅: {ewy_ret.max()*100:.2f}%")
    print(f"     最大单日跌幅: {ewy_ret.min()*100:.2f}%")
    print(f"     正收益天数比例: {(ewy_ret > 0).sum() / len(ewy_ret) * 100:.1f}%")

    # Sharpe Ratio (假设无风险利率 4.5%)
    rf_daily = 0.045 / 252
    sharpe = (ewy_ret.mean() - rf_daily) / ewy_ret.std() * np.sqrt(252)
    print(f"     Sharpe Ratio (假设 Rf=4.5%): {sharpe:.2f}")

    # ==========================================
    # 4. EWY 价格走势概览
    # ==========================================
    print(f"\n{'=' * 70}")
    print("四、EWY 价格走势概览")
    print("=" * 70)

    ewy_close = prices['EWY']
    # 按月汇总
    monthly = ewy_close.resample('ME').last()
    print(f"\n   月度收盘价:")
    for date, price in monthly.tail(12).items():
        mom_ret = ""
        if date in monthly.index:
            idx = monthly.index.get_loc(date)
            if idx > 0:
                prev = monthly.iloc[idx - 1]
                ret = (price - prev) / prev * 100
                mom_ret = f"({'+' if ret >= 0 else ''}{ret:.1f}%)"
        print(f"     {date.strftime('%Y-%m')}: ${price:.2f} {mom_ret}")

    # 年度表现
    print(f"\n   年度表现:")
    yearly = ewy_close.resample('YE').last()
    for i in range(1, len(yearly)):
        year = yearly.index[i]
        ret = (yearly.iloc[i] - yearly.iloc[i-1]) / yearly.iloc[i-1] * 100
        print(f"     {year.year}: {'+' if ret >= 0 else ''}{ret:.1f}%")

    # ==========================================
    # 5. 当 EWY 大跌时其他标的表现
    # ==========================================
    print(f"\n{'=' * 70}")
    print("五、EWY 大跌日 (>2%) 其他标的联动表现")
    print("=" * 70)

    big_drop_days = returns[returns['EWY'] < -0.02]
    if len(big_drop_days) > 0:
        print(f"\n   EWY 日跌幅 > 2% 的 {len(big_drop_days)} 天，各标的平均表现:")
        for ticker_name in [t for t in tickers if t != 'EWY' and t in returns.columns]:
            avg_ret = big_drop_days[ticker_name].mean() * 100
            print(f"     {ticker_name:>8}: {'+' if avg_ret >= 0 else ''}{avg_ret:.2f}%")

        print(f"\n   最近 5 次 EWY 大跌:")
        recent_drops = big_drop_days.tail(5)
        header = f"     {'日期':<12}"
        for t in [t for t in tickers if t in returns.columns]:
            header += f" | {t:>8}"
        print(header)
        print(f"     {'-'*12}" + ("-+-" + "-"*8) * len([t for t in tickers if t in returns.columns]))
        for date, row in recent_drops.iterrows():
            line = f"     {date.strftime('%Y-%m-%d'):<12}"
            for t in [t for t in tickers if t in returns.columns]:
                ret = row[t] * 100
                line += f" | {'+' if ret >= 0 else ''}{ret:>7.2f}%"
            print(line)


if __name__ == '__main__':
    analyze_correlations()
