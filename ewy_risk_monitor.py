"""
EWY 策略风控监控脚本
每月运行一次，自动拉取可量化指标，生成风控报告。
手动项列出待查清单。

用法: python3 ewy_risk_monitor.py
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import json
import urllib.request
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime('%Y-%m-%d')
RED = '🔴'
YELLOW = '🟡'
GREEN = '🟢'

print(f"{'='*70}")
print(f"  EWY 策略风控月度报告 — {TODAY}")
print(f"{'='*70}\n")

alerts = []  # (level, category, message)

# ============================================================
# 1. 美国 10Y-2Y 利差（衰退指标）
# ============================================================
print("▶ [1/8] 美国 10Y-2Y 利差")
try:
    tnx = yf.Ticker('^TNX').history(period='1y')  # 10Y
    t2y = yf.Ticker('2YY=F').history(period='1y')  # 2Y

    if len(tnx) > 0 and len(t2y) > 0:
        # align dates
        tnx_daily = tnx['Close'].resample('D').last().dropna()
        t2y_daily = t2y['Close'].resample('D').last().dropna()
        spread = tnx_daily - t2y_daily
        spread = spread.dropna()

        current_spread = spread.iloc[-1]
        spread_30d_ago = spread.iloc[-22] if len(spread) > 22 else spread.iloc[0]

        # check: was inverted and now positive (recession signal)
        was_inverted = (spread.iloc[-252:] < 0).any() if len(spread) >= 252 else False
        now_positive = current_spread > 0

        print(f"  当前利差: {current_spread:.3f}%")
        print(f"  30天前:   {spread_30d_ago:.3f}%")
        print(f"  过去1年是否倒挂: {'是' if was_inverted else '否'}")

        if was_inverted and now_positive:
            status = RED
            msg = f"利差曾倒挂现已转正 ({current_spread:.3f}%)，衰退风险升高"
            alerts.append(('RED', '全球衰退', msg))
        elif current_spread < 0:
            status = YELLOW
            msg = f"利差倒挂中 ({current_spread:.3f}%)"
            alerts.append(('YELLOW', '全球衰退', msg))
        else:
            status = GREEN
            msg = f"利差正常 ({current_spread:.3f}%)"
        print(f"  状态: {status} {msg}\n")
    else:
        print("  ⚠ 数据不足\n")
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 2. 铜价 vs 200日均线（衰退指标）
# ============================================================
print("▶ [2/8] 铜价 vs 200日均线")
try:
    copper = yf.Ticker('HG=F').history(period='2y')
    if len(copper) > 200:
        copper['ma200'] = copper['Close'].rolling(200).mean()
        current_cu = copper['Close'].iloc[-1]
        ma200_cu = copper['ma200'].iloc[-1]
        pct_vs_ma = (current_cu / ma200_cu - 1) * 100

        print(f"  当前铜价: ${current_cu:.2f}")
        print(f"  200日均线: ${ma200_cu:.2f}")
        print(f"  偏离: {pct_vs_ma:+.1f}%")

        if current_cu < ma200_cu:
            status = RED
            msg = f"铜价跌破200日均线 ({pct_vs_ma:+.1f}%)，衰退信号"
            alerts.append(('RED', '全球衰退', msg))
        elif pct_vs_ma < 5:
            status = YELLOW
            msg = f"铜价接近200日均线 ({pct_vs_ma:+.1f}%)"
            alerts.append(('YELLOW', '全球衰退', msg))
        else:
            status = GREEN
            msg = f"铜价健康 ({pct_vs_ma:+.1f}%)"
        print(f"  状态: {status} {msg}\n")
    else:
        print("  ⚠ 数据不足\n")
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 3. ISM 制造业 PMI（用 ISM 代理指标）
# ============================================================
print("▶ [3/8] ISM 制造业 PMI")
print("  ⚠ 需手动查询（https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/）")
print("  警戒线: <47 连续 2 月 → 衰退信号")
print("  状态: 📋 待手动检查\n")

# ============================================================
# 4. NVIDIA 股价趋势 + 下次财报日
# ============================================================
print("▶ [4/8] NVIDIA 趋势与财报")
try:
    nvda = yf.Ticker('NVDA')
    hist = nvda.history(period='1y')
    if len(hist) > 50:
        current = hist['Close'].iloc[-1]
        ma50 = hist['Close'].rolling(50).mean().iloc[-1]
        ma200 = hist['Close'].rolling(200).mean().iloc[-1] if len(hist) > 200 else None
        high_52w = hist['Close'].max()
        drawdown = (current / high_52w - 1) * 100

        print(f"  当前: ${current:.2f}")
        print(f"  50日均线: ${ma50:.2f} ({(current/ma50-1)*100:+.1f}%)")
        if ma200:
            print(f"  200日均线: ${ma200:.2f} ({(current/ma200-1)*100:+.1f}%)")
        print(f"  52周高点: ${high_52w:.2f}, 回撤: {drawdown:+.1f}%")

        # earnings date
        try:
            cal = nvda.calendar
            if cal is not None and 'Earnings Date' in cal.columns:
                earnings = cal['Earnings Date'].iloc[0]
                print(f"  下次财报: {earnings}")
            elif hasattr(cal, 'get') and cal.get('Earnings Date'):
                print(f"  下次财报: {cal['Earnings Date']}")
        except:
            print("  下次财报: 查询失败，请手动确认")

        if drawdown < -30:
            status = RED
            msg = f"NVIDIA 从高点回撤 {drawdown:.0f}%，AI 需求可能见顶"
            alerts.append(('RED', 'AI需求', msg))
        elif drawdown < -15:
            status = YELLOW
            msg = f"NVIDIA 回撤 {drawdown:.0f}%，需关注财报指引"
            alerts.append(('YELLOW', 'AI需求', msg))
        else:
            status = GREEN
            msg = f"NVIDIA 健康 (回撤 {drawdown:.0f}%)"
        print(f"  状态: {status} {msg}\n")
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 5. 三星电子 / SK Hynix 股价趋势（利润率代理）
# ============================================================
print("▶ [5/8] 三星电子 & SK 海力士趋势")
for name, ticker in [('三星电子', '005930.KS'), ('SK 海力士', '000660.KS')]:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period='1y')
        if len(hist) > 50:
            current = hist['Close'].iloc[-1]
            ma50 = hist['Close'].rolling(50).mean().iloc[-1]
            high_52w = hist['Close'].max()
            drawdown = (current / high_52w - 1) * 100
            vs_ma50 = (current / ma50 - 1) * 100

            print(f"  {name}: ₩{current:,.0f} (vs 50日均 {vs_ma50:+.1f}%, 52周高点 {drawdown:+.1f}%)")

            if drawdown < -25:
                status = RED
                msg = f"{name}从高点回撤{drawdown:.0f}%"
                alerts.append(('RED', '半导体周期', msg))
            elif drawdown < -15:
                status = YELLOW
                msg = f"{name}回撤{drawdown:.0f}%"
                alerts.append(('YELLOW', '半导体周期', msg))
    except Exception as e:
        print(f"  {name}: ⚠ 获取失败 ({e})")
print()

# ============================================================
# 6. DRAM 现货价趋势（用 MU 美光股价做代理）
# ============================================================
print("▶ [6/8] DRAM 趋势（美光 MU 代理）")
try:
    mu = yf.Ticker('MU')
    hist = mu.history(period='6mo')
    if len(hist) > 20:
        current = hist['Close'].iloc[-1]
        w4_ago = hist['Close'].iloc[-20] if len(hist) > 20 else hist['Close'].iloc[0]
        w4_change = (current / w4_ago - 1) * 100
        ma50 = hist['Close'].rolling(50).mean().iloc[-1] if len(hist) > 50 else None

        print(f"  MU 当前: ${current:.2f}")
        print(f"  4周变化: {w4_change:+.1f}%")
        if ma50:
            print(f"  vs 50日均: {(current/ma50-1)*100:+.1f}%")

        if w4_change < -15:
            status = RED
            msg = f"MU 4周跌{w4_change:.0f}%，DRAM 可能转弱"
            alerts.append(('RED', '半导体周期', msg))
        elif w4_change < -8:
            status = YELLOW
            msg = f"MU 4周跌{w4_change:.0f}%"
            alerts.append(('YELLOW', '半导体周期', msg))
        else:
            status = GREEN
            msg = f"MU 趋势正常 ({w4_change:+.1f}%)"
        print(f"  状态: {status} {msg}\n")
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 7. EWY 自身状态
# ============================================================
print("▶ [7/8] EWY 自身状态")
try:
    ewy = yf.Ticker('EWY')
    hist = ewy.history(period='2y')
    if len(hist) > 200:
        current = hist['Close'].iloc[-1]
        ma200 = hist['Close'].rolling(200).mean().iloc[-1]
        ma50 = hist['Close'].rolling(50).mean().iloc[-1]
        high_52w = hist['Close'].iloc[-252:].max()
        drawdown = (current / high_52w - 1) * 100

        # IBS
        today_h = hist['High'].iloc[-1]
        today_l = hist['Low'].iloc[-1]
        today_c = hist['Close'].iloc[-1]
        ibs = (today_c - today_l) / (today_h - today_l) if (today_h - today_l) > 0 else 0.5

        # 波动率
        ret = hist['Close'].pct_change().dropna()
        vol_20d = ret.iloc[-20:].std() * np.sqrt(252) * 100

        print(f"  当前: ${current:.2f}")
        print(f"  200日均线: ${ma200:.2f} ({(current/ma200-1)*100:+.1f}%)")
        print(f"  50日均线: ${ma50:.2f} ({(current/ma50-1)*100:+.1f}%)")
        print(f"  52周高点回撤: {drawdown:+.1f}%")
        print(f"  今日 IBS: {ibs:.3f}")
        print(f"  20日年化波动率: {vol_20d:.1f}%")

        if current < ma200:
            status = RED
            msg = f"EWY 跌破 200 日均线！策略应暂停"
            alerts.append(('RED', 'EWY趋势', msg))
        elif (current / ma200 - 1) < 0.05:
            status = YELLOW
            msg = f"EWY 距 200 日均线仅 {(current/ma200-1)*100:.1f}%"
            alerts.append(('YELLOW', 'EWY趋势', msg))
        else:
            status = GREEN
            msg = f"EWY 在 200 日均线上方 {(current/ma200-1)*100:.1f}%"

        print(f"  趋势: {status} {msg}")

        # IBS signal
        if ibs < 0.2 and current > ma200:
            print(f"  📍 IBS 信号: 今日 IBS={ibs:.3f} < 0.2，可能触发买入信号")
        elif ibs > 0.8:
            print(f"  📍 IBS 信号: 今日 IBS={ibs:.3f} > 0.8，持仓应考虑卖出")
        print()
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 8. 布伦特原油（地缘政治/能源风险）
# ============================================================
print("▶ [8/8] 布伦特原油（能源/地缘风险）")
try:
    brent = yf.Ticker('BZ=F')
    hist = brent.history(period='6mo')
    if len(hist) > 5:
        current = hist['Close'].iloc[-1]
        w1_ago = hist['Close'].iloc[-5] if len(hist) > 5 else hist['Close'].iloc[0]
        w1_change = (current / w1_ago - 1) * 100

        print(f"  布伦特原油: ${current:.2f}")
        print(f"  1周变化: {w1_change:+.1f}%")

        if current > 100:
            status = RED
            msg = f"原油 ${current:.0f} > $100，韩国能源危机风险极高"
            alerts.append(('RED', '能源风险', msg))
        elif current > 90:
            status = YELLOW
            msg = f"原油 ${current:.0f} > $90，关注中东局势"
            alerts.append(('YELLOW', '能源风险', msg))
        else:
            status = GREEN
            msg = f"原油 ${current:.0f}，正常水平"
        print(f"  状态: {status} {msg}\n")
except Exception as e:
    print(f"  ⚠ 获取失败: {e}\n")

# ============================================================
# 汇总
# ============================================================
print("=" * 70)
print("  风控汇总")
print("=" * 70)

red_alerts = [a for a in alerts if a[0] == 'RED']
yellow_alerts = [a for a in alerts if a[0] == 'YELLOW']

if red_alerts:
    print(f"\n{RED} 红色警报 ({len(red_alerts)} 个):")
    for _, cat, msg in red_alerts:
        print(f"  [{cat}] {msg}")

if yellow_alerts:
    print(f"\n{YELLOW} 黄色预警 ({len(yellow_alerts)} 个):")
    for _, cat, msg in yellow_alerts:
        print(f"  [{cat}] {msg}")

if not red_alerts and not yellow_alerts:
    print(f"\n{GREEN} 所有自动化指标正常")

# 判断是否需要暂停
red_categories = set(cat for _, cat, _ in red_alerts)
if len(red_alerts) >= 2:
    print(f"\n⚠️  两个以上红色警报！建议暂停策略。")
    print(f"   涉及: {', '.join(red_categories)}")
elif len(red_alerts) == 1:
    print(f"\n⚠️  单个红色警报，保持警惕但无需暂停。")
else:
    print(f"\n✅ 策略可继续运行。")

# 手动检查清单
print(f"\n{'='*70}")
print("  待手动检查项")
print(f"{'='*70}")
print("""
□ ISM 制造业 PMI
  → https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/
  → 警戒: <47 连续 2 月

□ 韩国半导体出口 YoY
  → https://www.customs.go.kr/english/main.do (每月1日公布)
  → 警戒: YoY 增速连续 2 月下滑

□ HBM 合约价趋势
  → TrendForce (https://www.trendforce.com/) 或新闻报道
  → 警戒: 合约价开始议价下调

□ 台积电先进制程产能利用率（季度）
  → 台积电法说会/财报
  → 警戒: <90%

□ NVIDIA 季度指引 vs 市场预期（财报季）
  → 需看实际财报内容
  → 警戒: 下季指引低于市场预期
""")

print(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
