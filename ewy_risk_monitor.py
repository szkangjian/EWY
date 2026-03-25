"""
EWY 策略风控月度报告

自动拉取可量化指标，生成风控报告并保存为 Markdown 文件。
手动项列出待查清单。

用法:
  uv run python ewy_risk_monitor.py              # 生成报告（终端 + 文件）
  uv run python ewy_risk_monitor.py --no-save    # 仅终端输出不保存文件
"""

import sys
import json
import os
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import warnings
import urllib.request
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime('%Y-%m-%d')
MONTH = datetime.now().strftime('%Y-%m')
STATE_FILE = "ewy_signal_state.json"
REPORT_DIR = "docs/risk_reports"
REPORT_FILE = f"{REPORT_DIR}/{MONTH}.md"
SAVE_FILE = "--no-save" not in sys.argv

# 报告内容缓冲
report_lines = []

def out(text=""):
    """同时输出到终端和报告"""
    print(text)
    report_lines.append(text)

def fetch_ticker(symbol, period='1y'):
    """安全获取 yfinance 数据"""
    try:
        t = yf.Ticker(symbol)
        h = t.history(period=period)
        return h if len(h) > 0 else None
    except:
        return None

# ==============================================================
out(f"# EWY 策略风控月度报告 — {MONTH}")
out(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
out()

alerts = []  # (level, category, message)

# ==============================================================
# 一、策略绩效
# ==============================================================
out("## 一、策略绩效\n")

try:
    state = json.loads(Path(STATE_FILE).read_text())
    trades = state.get("trade_log", [])

    if trades:
        # 本月交易
        month_trades = [t for t in trades if t.get("sell_date", "").startswith(MONTH)]
        all_rets = [t["ret"] for t in trades]
        month_rets = [t["ret"] for t in month_trades]

        out("### 累计绩效\n")
        total_trades = len(trades)
        total_wins = sum(1 for r in all_rets if r > 0)
        total_ret = sum(all_rets)
        out(f"| 指标 | 数值 |")
        out(f"|------|------|")
        out(f"| 总交易笔数 | {total_trades} |")
        out(f"| 累计胜率 | {total_wins/total_trades*100:.0f}% ({total_wins}/{total_trades}) |")
        out(f"| 累计收益 | {total_ret:+.1f}% |")
        out(f"| 平均收益/笔 | {np.mean(all_rets):+.2f}% |")
        out(f"| 最大单笔亏损 | {min(all_rets):+.2f}% |")
        out(f"| 连续到期亏损 | {state.get('consecutive_exp_losses', 0)} (熔断线: 3) |")
        out(f"| 策略状态 | {'**熔断中**' if state.get('circuit_breaker') else '正常运行'} |")
        out()

        if month_trades:
            out(f"### 本月交易 ({MONTH})\n")
            out(f"| 策略 | 买入日 | 卖出日 | 买入 | 卖出 | 天数 | 收益 | 原因 |")
            out(f"|------|--------|--------|------|------|------|------|------|")
            for t in month_trades:
                out(f"| {t.get('strategy','?')} | {t['buy_date']} | {t['sell_date']} | "
                    f"${t['buy_price']:.2f} | ${t['sell_price']:.2f} | "
                    f"{t['days']} | {t['ret']:+.2f}% | {t['reason']} |")
            month_ret = sum(month_rets)
            month_wins = sum(1 for r in month_rets if r > 0)
            out(f"\n本月: {len(month_trades)} 笔, 胜率 {month_wins/len(month_trades)*100:.0f}%, 收益 {month_ret:+.1f}%")
        else:
            out(f"本月无已完成交易。")
        out()

        # 持仓
        if state.get("ibs_position"):
            p = state["ibs_position"]
            out(f"**当前 IBS 持仓**: {p['buy_date']} @ ${p['buy_price']:.2f}, 第 {p['days_held']} 天")
        if state.get("drop_position"):
            p = state["drop_position"]
            out(f"**当前 DROP 持仓**: {p['buy_date']} @ ${p['buy_price']:.2f}, 第 {p['days_held']} 天")
        if not state.get("ibs_position") and not state.get("drop_position"):
            out("当前无持仓。")
    else:
        out("暂无交易记录（ewy_signal_state.json 为空）。")
except FileNotFoundError:
    out("未找到 ewy_signal_state.json，跳过绩效统计。")
except Exception as e:
    out(f"读取交易记录失败: {e}")

out()

# ==============================================================
# 二、风控指标
# ==============================================================
out("## 二、风控指标\n")

# ---- 2.1 EWY 自身状态 ----
out("### 2.1 EWY 自身状态\n")
try:
    hist = fetch_ticker('EWY', '2y')
    if hist is not None and len(hist) > 200:
        current = hist['Close'].iloc[-1]
        ma200 = hist['Close'].rolling(200).mean().iloc[-1]
        ma50 = hist['Close'].rolling(50).mean().iloc[-1]
        high_52w = hist['Close'].iloc[-252:].max()
        drawdown = (current / high_52w - 1) * 100

        today_h = hist['High'].iloc[-1]
        today_l = hist['Low'].iloc[-1]
        ibs = (current - today_l) / (today_h - today_l) if (today_h - today_l) > 0 else 0.5

        ret = hist['Close'].pct_change().dropna()
        vol_20d = ret.iloc[-20:].std() * np.sqrt(252) * 100
        vol_60d = ret.iloc[-60:].std() * np.sqrt(252) * 100

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| 当前价格 | ${current:.2f} | |")

        # MA200
        pct_ma200 = (current / ma200 - 1) * 100
        if current < ma200:
            status = "RED 跌破均线！策略应暂停"
            alerts.append(('RED', 'EWY趋势', f"EWY 跌破 200 日均线"))
        elif pct_ma200 < 5:
            status = "YELLOW 接近均线"
            alerts.append(('YELLOW', 'EWY趋势', f"EWY 距 200 日均线仅 {pct_ma200:.1f}%"))
        else:
            status = "GREEN 正常"
        out(f"| 200 日均线 | ${ma200:.2f} ({pct_ma200:+.1f}%) | {status} |")

        out(f"| 50 日均线 | ${ma50:.2f} ({(current/ma50-1)*100:+.1f}%) | |")
        out(f"| 52 周高点回撤 | {drawdown:+.1f}% | |")
        out(f"| 最新 IBS | {ibs:.3f} | {'买入区' if ibs < 0.2 else '卖出区' if ibs > 0.8 else '中性'} |")
        out(f"| 20 日波动率(年化) | {vol_20d:.1f}% | {'偏高' if vol_20d > 40 else '正常'} |")
        out(f"| 60 日波动率(年化) | {vol_60d:.1f}% | |")
    else:
        out("数据不足。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.2 美国 10Y-2Y 利差 ----
out("### 2.2 美国 10Y-2Y 利差（衰退指标）\n")
try:
    tnx = fetch_ticker('^TNX', '1y')
    t2y = fetch_ticker('^IRX', '1y')  # 3-month as fallback, or try 2Y
    if t2y is None or len(t2y) < 10:
        t2y = fetch_ticker('2YY=F', '1y')

    if tnx is not None and t2y is not None and len(tnx) > 10 and len(t2y) > 10:
        # Align by date index (strip timezone first — tickers may have different tz)
        tnx_d = tnx['Close'].copy()
        t2y_d = t2y['Close'].copy()
        tnx_d.index = tnx_d.index.tz_localize(None).normalize()
        t2y_d.index = t2y_d.index.tz_localize(None).normalize()
        combined = pd.DataFrame({'tnx': tnx_d, 't2y': t2y_d}).dropna()

        if len(combined) < 5:
            out("数据对齐后样本不足。")
            raise ValueError("insufficient aligned data")

        spread = combined['tnx'] - combined['t2y']
        current_spread = spread.iloc[-1]
        was_inverted = (spread < 0).any()
        now_positive = current_spread > 0

        if was_inverted and now_positive:
            status = "RED 曾倒挂现已转正，衰退风险"
            alerts.append(('RED', '全球衰退', f"利差曾倒挂现转正 ({current_spread:.3f}%)"))
        elif current_spread < 0:
            status = "YELLOW 倒挂中"
            alerts.append(('YELLOW', '全球衰退', f"利差倒挂 ({current_spread:.3f}%)"))
        else:
            status = "GREEN 正常"

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| 当前利差 | {current_spread:.3f}% | {status} |")
        out(f"| 过去 1 年是否倒挂 | {'是' if was_inverted else '否'} | |")
    else:
        out("数据获取失败。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.3 铜价 ----
out("### 2.3 铜价 vs 200 日均线（衰退指标）\n")
try:
    copper = fetch_ticker('HG=F', '2y')
    if copper is not None and len(copper) > 200:
        current_cu = copper['Close'].iloc[-1]
        ma200_cu = copper['Close'].rolling(200).mean().iloc[-1]
        pct = (current_cu / ma200_cu - 1) * 100

        if current_cu < ma200_cu:
            status = "RED 跌破均线"
            alerts.append(('RED', '全球衰退', f"铜价跌破 200 日均线 ({pct:+.1f}%)"))
        elif pct < 5:
            status = "YELLOW 接近均线"
            alerts.append(('YELLOW', '全球衰退', f"铜价接近 200 日均线 ({pct:+.1f}%)"))
        else:
            status = "GREEN 正常"

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| 铜价 | ${current_cu:.2f} | |")
        out(f"| 200 日均线 | ${ma200_cu:.2f} ({pct:+.1f}%) | {status} |")
    else:
        out("数据不足。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.4 NVIDIA ----
out("### 2.4 NVIDIA（AI 需求指标）\n")
try:
    nvda = yf.Ticker('NVDA')
    hist = nvda.history(period='1y')
    if len(hist) > 50:
        current = hist['Close'].iloc[-1]
        ma50 = hist['Close'].rolling(50).mean().iloc[-1]
        high_52w = hist['Close'].max()
        drawdown = (current / high_52w - 1) * 100

        if drawdown < -30:
            status = "RED 大幅回撤，AI 需求见顶风险"
            alerts.append(('RED', 'AI需求', f"NVIDIA 回撤 {drawdown:.0f}%"))
        elif drawdown < -15:
            status = "YELLOW 需关注"
            alerts.append(('YELLOW', 'AI需求', f"NVIDIA 回撤 {drawdown:.0f}%"))
        else:
            status = "GREEN 正常"

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| NVDA 当前 | ${current:.2f} | |")
        out(f"| 50 日均线 | ${ma50:.2f} ({(current/ma50-1)*100:+.1f}%) | |")
        out(f"| 52 周高点回撤 | {drawdown:+.1f}% | {status} |")

        # 财报日
        try:
            cal = nvda.calendar
            if cal is not None:
                if hasattr(cal, 'columns') and 'Earnings Date' in cal.columns:
                    out(f"| 下次财报 | {cal['Earnings Date'].iloc[0]} | |")
                elif isinstance(cal, dict) and 'Earnings Date' in cal:
                    dates = cal['Earnings Date']
                    if isinstance(dates, list) and dates:
                        out(f"| 下次财报 | {dates[0]} | |")
                    else:
                        out(f"| 下次财报 | {dates} | |")
        except:
            out(f"| 下次财报 | 查询失败，请手动确认 | |")
    else:
        out("数据不足。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.5 三星/SK海力士 ----
out("### 2.5 三星电子 & SK 海力士（半导体周期）\n")
out(f"| 公司 | 当前价 | vs 50 日均 | 52 周高点回撤 | 状态 |")
out(f"|------|--------|-----------|-------------|------|")

for name, ticker in [('三星电子', '005930.KS'), ('SK 海力士', '000660.KS')]:
    try:
        hist = fetch_ticker(ticker, '1y')
        if hist is not None and len(hist) > 50:
            current = hist['Close'].iloc[-1]
            ma50 = hist['Close'].rolling(50).mean().iloc[-1]
            high_52w = hist['Close'].max()
            dd = (current / high_52w - 1) * 100
            vs50 = (current / ma50 - 1) * 100

            if dd < -25:
                status = "RED"
                alerts.append(('RED', '半导体周期', f"{name}回撤{dd:.0f}%"))
            elif dd < -15:
                status = "YELLOW"
                alerts.append(('YELLOW', '半导体周期', f"{name}回撤{dd:.0f}%"))
            else:
                status = "GREEN"
            out(f"| {name} | \\{current:,.0f} | {vs50:+.1f}% | {dd:+.1f}% | {status} |")
        else:
            out(f"| {name} | 数据不足 | | | |")
    except Exception as e:
        out(f"| {name} | 获取失败 | | | |")
out()

# ---- 2.6 DRAM (MU 代理) ----
out("### 2.6 DRAM 趋势（美光 MU 代理）\n")
try:
    hist = fetch_ticker('MU', '6mo')
    if hist is not None and len(hist) > 20:
        current = hist['Close'].iloc[-1]
        w4_ago = hist['Close'].iloc[-20]
        w4_change = (current / w4_ago - 1) * 100

        if w4_change < -15:
            status = "RED DRAM 可能转弱"
            alerts.append(('RED', '半导体周期', f"MU 4 周跌 {w4_change:.0f}%"))
        elif w4_change < -8:
            status = "YELLOW 需关注"
            alerts.append(('YELLOW', '半导体周期', f"MU 4 周跌 {w4_change:.0f}%"))
        else:
            status = "GREEN 正常"

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| MU 当前 | ${current:.2f} | |")
        out(f"| 4 周变化 | {w4_change:+.1f}% | {status} |")
    else:
        out("数据不足。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.7 布伦特原油 ----
out("### 2.7 布伦特原油（能源/地缘风险）\n")
try:
    hist = fetch_ticker('BZ=F', '6mo')
    if hist is not None and len(hist) > 5:
        current = hist['Close'].iloc[-1]
        w1_ago = hist['Close'].iloc[-5]
        w1_change = (current / w1_ago - 1) * 100

        if current > 100:
            status = "RED 极端，韩国能源危机风险"
            alerts.append(('RED', '能源风险', f"原油 ${current:.0f} > $100"))
        elif current > 90:
            status = "YELLOW 偏高"
            alerts.append(('YELLOW', '能源风险', f"原油 ${current:.0f} > $90"))
        else:
            status = "GREEN 正常"

        out(f"| 指标 | 数值 | 状态 |")
        out(f"|------|------|------|")
        out(f"| 布伦特原油 | ${current:.2f} | {status} |")
        out(f"| 1 周变化 | {w1_change:+.1f}% | |")
    else:
        out("数据不足。")
except Exception as e:
    out(f"获取失败: {e}")
out()

# ---- 2.8 ISM PMI (FRED) ----
out("### 2.8 ISM 制造业 PMI\n")
try:
    # 尝试从 FRED 获取 (MANEMP 不需要 API key, PMI 需要)
    # 用 ISM PMI 的免费代理: 通过 yfinance 无法获取, 尝试 FRED
    FRED_PMI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?bgcolor=%23e1e9f0&chart_type=line&drp=0&fo=open%20sans&graph_bgcolor=%23ffffff&height=450&mode=fred&recession_bars=on&txtcolor=%23444444&ts=12&tts=12&width=1168&nt=0&thu=0&trc=0&show_legend=yes&show_axis_titles=yes&show_tooltip=yes&id=MANEMP&scale=left&cosd=2024-01-01&coed=2026-12-31&line_color=%234572a7&link_values=false&line_style=solid&mark_type=none&mw=3&lw=2&ost=-99999&oet=99999&mma=0&fml=a&fq=Monthly&fam=avg&fgst=lin&fgsnd=2020-02-01&line_index=1&transformation=lin&vintage_date={TODAY}&revision_date={TODAY}&nd=2024-01-01"
    # MANEMP is manufacturing employment, not PMI. PMI (NAPM) needs API key.
    # Just flag as manual check
    out("ISM PMI 无法自动获取（需 FRED API key 或手动查询）。\n")
    out("最新数据请查看: https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/\n")
    out("警戒线: < 47 连续 2 月 → 衰退信号\n")
except:
    out("获取失败。")
out()

# ==============================================================
# 三、风控汇总
# ==============================================================
out("## 三、风控汇总\n")

red_alerts = [a for a in alerts if a[0] == 'RED']
yellow_alerts = [a for a in alerts if a[0] == 'YELLOW']

if red_alerts:
    out(f"### RED 红色警报 ({len(red_alerts)} 个)\n")
    for _, cat, msg in red_alerts:
        out(f"- **[{cat}]** {msg}")
    out()

if yellow_alerts:
    out(f"### YELLOW 黄色预警 ({len(yellow_alerts)} 个)\n")
    for _, cat, msg in yellow_alerts:
        out(f"- [{cat}] {msg}")
    out()

if not red_alerts and not yellow_alerts:
    out("所有自动化指标正常。\n")

# 判断
out("### 策略建议\n")
red_categories = set(cat for _, cat, _ in red_alerts)
if len(red_alerts) >= 2:
    out(f"**!! 两个以上红色警报 → 建议暂停策略**")
    out(f"涉及: {', '.join(red_categories)}")
elif len(red_alerts) == 1:
    out(f"单个红色警报，保持警惕但无需暂停。")
else:
    out(f"策略可继续运行。")
out()

# ==============================================================
# 四、待手动检查项
# ==============================================================
out("## 四、待手动检查项\n")
out("""- [ ] **ISM 制造业 PMI**
  - https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/
  - 警戒: < 47 连续 2 月

- [ ] **韩国半导体出口 YoY**
  - https://www.customs.go.kr/english/main.do (每月 1 日公布)
  - 警戒: YoY 增速连续 2 月下滑

- [ ] **HBM 合约价趋势**
  - TrendForce (https://www.trendforce.com/) 或新闻搜索 "HBM contract price"
  - 警戒: 合约价开始议价下调

- [ ] **台积电先进制程产能利用率**（季度）
  - 台积电法说会 / 财报
  - 警戒: < 90%

- [ ] **NVIDIA 季度指引 vs 市场预期**（财报季）
  - 财报内容 + 分析师反应
  - 警戒: 下季指引低于市场预期

- [ ] **北美大厂 AI 资本开支增速**（季度）
  - MSFT / GOOG / META / AMZN 财报
  - 警戒: 环比增速放缓至 < 10%
""")

# ==============================================================
# 保存报告
# ==============================================================
if SAVE_FILE:
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_content = "\n".join(report_lines)
    Path(REPORT_FILE).write_text(report_content, encoding='utf-8')
    print(f"\n{'='*60}")
    print(f"  报告已保存至: {REPORT_FILE}")
    print(f"{'='*60}")
