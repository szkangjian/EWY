# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yfinance>=0.2",
#   "pandas>=2.0",
#   "numpy>=1.24",
# ]
# ///
"""
伊朗冲突风险量化监控器  —  EWY 策略专用
==========================================

直接衡量冲突强度与物流中断，而非依赖油价等二阶反应指标。

数据源（按直接程度排序）:
  1. ACLED API        — 实际导弹/无人机/炮击攻击事件计数  [需免费注册]
  2. OpenSky Network  — 海湾 & 伊朗领空实时航班数         [免费]
  3. GDELT Doc API    — 冲突新闻加速度（文章数+情绪）     [免费]
  4. ILS/USD          — 以色列谢克尔（最直接地区当事方）  [yfinance]
  5. LNG 代理         — NG=F / LNG 股 (JKM 免费代理)      [yfinance]
  6. OVX              — 原油隐含波动率（低权重，情绪性）  [yfinance]

用法:
  uv run python iran_conflict_monitor.py              # 生成报告
  uv run python iran_conflict_monitor.py --no-save    # 仅终端输出

ACLED 注册:
  https://acleddata.com/register/  →  设置环境变量:
  export ACLED_EMAIL="your@email.com"
  export ACLED_API_KEY="your_key"
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ── 配置 ─────────────────────────────────────────────────────────────────────
TODAY     = datetime.now().strftime("%Y-%m-%d")
NOW       = datetime.now()
SAVE_FILE = "--no-save" not in sys.argv
REPORT_DIR  = "docs/risk_reports"
REPORT_PATH = f"{REPORT_DIR}/iran_conflict_{TODAY}.md"

ACLED_EMAIL = os.environ.get("ACLED_EMAIL", "")
ACLED_KEY   = os.environ.get("ACLED_API_KEY", "")

# 报告缓冲
_lines: list[str] = []

def out(text: str = "") -> None:
    print(text)
    _lines.append(text)

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def fetch_ticker(symbol: str, period: str = "3mo") -> pd.DataFrame | None:
    try:
        h = yf.Ticker(symbol).history(period=period)
        return h if len(h) > 0 else None
    except Exception:
        return None


def http_get(url: str, timeout: int = 15) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EWY-Monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return None


def score_band(value: float, thresholds: list[tuple[float, float]]) -> float:
    """
    thresholds: [(upper_bound, score), ...] sorted ascending.
    Returns score for first band where value <= upper_bound.
    """
    for bound, sc in thresholds:
        if value <= bound:
            return sc
    return thresholds[-1][1]

# ── 报告头 ────────────────────────────────────────────────────────────────────
out(f"# 伊朗冲突风险量化报告 — {TODAY}")
out(f"\n> 生成时间: {NOW.strftime('%Y-%m-%d %H:%M:%S')}  |  仅含非油价直接指标\n")

# ACLED 早期警告
if not ACLED_EMAIL or not ACLED_KEY:
    out("> ⚠️  **ACLED API 未配置**（权重最高指标缺失）")
    out("> 注册: https://acleddata.com/register/")
    out("> 设置: `export ACLED_EMAIL=... && export ACLED_API_KEY=...`\n")

risk_scores: dict[str, tuple[float, float, str]] = {}  # key → (score, weight, desc)


# ══════════════════════════════════════════════════════════════════════════════
# 一、ACLED — 实际攻击事件计数 (权重 3.0)
# ══════════════════════════════════════════════════════════════════════════════
out("## 一、ACLED 攻击事件计数（最直接指标）\n")
out("覆盖: 伊朗、也门、伊拉克、叙利亚 — 空袭/无人机/导弹/炮击\n")

if not ACLED_EMAIL or not ACLED_KEY:
    out("*跳过: 未配置 ACLED_EMAIL / ACLED_API_KEY*\n")
    risk_scores["acled"] = (50.0, 3.0, "ACLED 未配置，使用中性值 50")
else:
    try:
        date_since = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
        params = urllib.parse.urlencode({
            "key":          ACLED_KEY,
            "email":        ACLED_EMAIL,
            "country":      "Iran|Yemen|Iraq|Syria",
            "event_type":   "Explosions/Remote violence|Battles",
            "sub_event_type": "Air/drone strike|Shelling/artillery/missile attack",
            "fields":       "event_date,event_type,sub_event_type,actor1,country,fatalities,notes",
            "limit":        500,
            "event_date":   date_since,
            "event_date_where": ">=",
        })
        url  = f"https://api.acleddata.com/acled/read?{params}"
        data = http_get(url, timeout=20)

        if data and "data" in data and data["data"]:
            events = data["data"]
            df_ac  = pd.DataFrame(events)
            df_ac["event_date"] = pd.to_datetime(df_ac["event_date"])
            df_ac["fatalities"] = pd.to_numeric(df_ac["fatalities"], errors="coerce").fillna(0)

            total_events   = len(df_ac)
            total_fatalities = int(df_ac["fatalities"].sum())
            days_covered   = (NOW - (NOW - timedelta(days=30))).days
            daily_avg      = total_events / 30

            # 最近 7 天 vs 前 7 天
            cutoff_7d  = NOW - timedelta(days=7)
            cutoff_14d = NOW - timedelta(days=14)
            recent_7d  = df_ac[df_ac["event_date"] >= cutoff_7d]
            prior_7d   = df_ac[(df_ac["event_date"] >= cutoff_14d) & (df_ac["event_date"] < cutoff_7d)]

            n_recent = len(recent_7d)
            n_prior  = len(prior_7d) if len(prior_7d) > 0 else 1
            accel    = n_recent / n_prior  # >1 = 加速

            out(f"| 指标 | 数值 |")
            out(f"|------|------|")
            out(f"| 过去 30 天总事件数 | {total_events} |")
            out(f"| 日均攻击次数 | {daily_avg:.1f} |")
            out(f"| 总伤亡人数 | {total_fatalities} |")
            out(f"| 近 7 天事件数 | {n_recent} |")
            out(f"| 前 7 天事件数 | {n_prior} |")
            out(f"| 7 天加速比 | {accel:.2f}x {'🔺 加速' if accel > 1.3 else ('🔻 减速' if accel < 0.7 else '→ 平稳')} |")
            out()

            # 按国家分解
            out("### 1.1 按国家分解（近 7 天）\n")
            out("| 国家 | 事件数 | 伤亡 |")
            out("|------|--------|------|")
            for country, grp in recent_7d.groupby("country"):
                out(f"| {country} | {len(grp)} | {int(grp['fatalities'].sum())} |")
            out()

            # 按武器类型
            out("### 1.2 按攻击类型（近 7 天）\n")
            out("| 类型 | 次数 |")
            out("|------|------|")
            for stype, grp in recent_7d.groupby("sub_event_type"):
                out(f"| {stype} | {len(grp)} |")
            out()

            # 评分
            if accel >= 2.5 or n_recent >= 40:
                sc = 90; lv = "RED 急剧升级"
            elif accel >= 1.5 or n_recent >= 20:
                sc = 70; lv = "YELLOW 明显升级"
            elif accel >= 1.2 or n_recent >= 10:
                sc = 45; lv = "ORANGE 温和升级"
            else:
                sc = 20; lv = "GREEN 平稳或下降"

            risk_scores["acled"] = (sc, 3.0, f"事件加速 {accel:.2f}x，近7天 {n_recent} 次")
            out(f"**ACLED 风险信号**: {lv}\n")

        else:
            out("ACLED 返回空数据或请求失败。\n")
            risk_scores["acled"] = (50.0, 3.0, "ACLED 请求失败")

    except Exception as e:
        out(f"ACLED 获取失败: {e}\n")
        risk_scores["acled"] = (50.0, 3.0, f"ACLED 异常: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 二、OpenSky Network — 实时航班 (权重 Gulf=2.0, Iran FIR=1.5)
# ══════════════════════════════════════════════════════════════════════════════
out("## 二、OpenSky Network — 实时航班数\n")

ZONES = {
    "gulf":     {"name": "波斯湾+霍尔木兹",  "lamin": 22, "lomin": 48, "lamax": 30, "lomax": 60,
                 "normal_low": 60,  "normal_high": 150, "weight": 2.0},
    "iran_fir": {"name": "伊朗领空 (OIIX)", "lamin": 25, "lomin": 44, "lamax": 40, "lomax": 64,
                 "normal_low": 20,  "normal_high": 80,  "weight": 1.5},
}

for zone_key, z in ZONES.items():
    out(f"### 2.{list(ZONES.keys()).index(zone_key)+1} {z['name']}\n")
    url  = (f"https://opensky-network.org/api/states/all"
            f"?lamin={z['lamin']}&lomin={z['lomin']}&lamax={z['lamax']}&lomax={z['lomax']}")
    data = http_get(url, timeout=10)

    if data and isinstance(data.get("states"), list):
        count    = len(data["states"])
        mid_norm = (z["normal_low"] + z["normal_high"]) / 2
        drop_pct = (mid_norm - count) / mid_norm * 100 if count < mid_norm else 0

        if count < z["normal_low"] * 0.4:
            sc = 90; lv = "RED 极低 — 疑似空域关闭"
        elif count < z["normal_low"] * 0.65:
            sc = 70; lv = "YELLOW 显著偏低 — 主动回避"
        elif count < z["normal_low"]:
            sc = 40; lv = "ORANGE 略低"
        elif count <= z["normal_high"]:
            sc = 15; lv = "GREEN 正常"
        else:
            sc = 10; lv = "GREEN 繁忙"

        risk_scores[f"opensky_{zone_key}"] = (sc, z["weight"], f"{z['name']}={count}架")

        out(f"| 指标 | 数值 |")
        out(f"|------|------|")
        out(f"| 当前航班数 | **{count}** |")
        out(f"| 正常区间 | {z['normal_low']} – {z['normal_high']} |")
        out(f"| 偏离正常中值 | {drop_pct:+.0f}% |")
        out(f"| 风险信号 | {lv} |")
        out()

        # 简要列出运营航空公司
        callsigns = [s[1].strip()[:3] for s in data["states"] if s[1] and s[1].strip()]
        airlines: dict[str, int] = {}
        for cs in callsigns:
            airlines[cs] = airlines.get(cs, 0) + 1
        top5 = sorted(airlines.items(), key=lambda x: -x[1])[:5]
        if top5:
            out(f"主要呼号前缀: {', '.join(f'{k}({v})' for k,v in top5)}\n")
    else:
        out(f"OpenSky 数据不可用（可能触发速率限制或夜间数据稀少）\n")
        risk_scores[f"opensky_{zone_key}"] = (50.0, z["weight"], f"{z['name']} 数据不可用")


# ══════════════════════════════════════════════════════════════════════════════
# 三、GDELT — 冲突报道加速度 (权重 1.5)
# ══════════════════════════════════════════════════════════════════════════════
out("## 三、GDELT 冲突新闻加速度\n")
out("衡量近 7 天 vs 前 7 天报道量加速，反映媒体感知到的冲突烈度变化\n")

GDELT_QUERIES = [
    ("iran missile attack",       "伊朗导弹袭击"),
    ("iran drone strike hormuz",  "伊朗无人机+霍尔木兹"),
    ("strait of hormuz closure",  "霍尔木兹封锁"),
]


def gdelt_count(query: str, days_back: int, days_end: int = 0) -> int | None:
    end   = datetime.utcnow() - timedelta(days=days_end)
    start = end - timedelta(days=days_back)
    url   = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={urllib.parse.quote(query)}"
        "&mode=artlist&maxrecords=250&format=json"
        f"&startdatetime={start.strftime('%Y%m%d%H%M%S')}"
        f"&enddatetime={end.strftime('%Y%m%d%H%M%S')}"
    )
    data = http_get(url, timeout=20)
    if data and "articles" in data:
        return len(data["articles"])
    return None


out("| 查询主题 | 近 7 天 | 前 7 天 | 加速比 | 信号 |")
out("|----------|---------|---------|--------|------|")

accel_list: list[float] = []
for query, label in GDELT_QUERIES:
    n_recent = gdelt_count(query, days_back=7, days_end=0)
    time.sleep(1)
    n_prior  = gdelt_count(query, days_back=7, days_end=7)
    time.sleep(1)

    if n_recent is not None and n_prior is not None:
        prior_safe = max(n_prior, 1)
        accel      = n_recent / prior_safe
        accel_list.append(accel)
        sig = ("🔺 RED 急增" if accel >= 2.5 else
               "🔺 YELLOW 升温" if accel >= 1.5 else
               "→ 平稳" if accel >= 0.7 else
               "🔻 降温")
        out(f"| {label} | {n_recent} | {n_prior} | {accel:.2f}x | {sig} |")
    else:
        out(f"| {label} | 失败 | 失败 | — | — |")
out()

if accel_list:
    avg_accel = np.mean(accel_list)
    if avg_accel >= 2.5:
        sc = 85; lv = "RED 报道量急剧增加"
    elif avg_accel >= 1.5:
        sc = 60; lv = "YELLOW 报道明显升温"
    elif avg_accel >= 1.1:
        sc = 35; lv = "ORANGE 轻微升温"
    else:
        sc = 15; lv = "GREEN 平稳或降温"
    risk_scores["gdelt"] = (sc, 1.5, f"GDELT 平均加速 {avg_accel:.2f}x")
    out(f"**GDELT 综合**: 平均加速比 {avg_accel:.2f}x — {lv}\n")
else:
    risk_scores["gdelt"] = (50.0, 1.5, "GDELT 数据不可用")
    out("GDELT 数据获取失败\n")


# ══════════════════════════════════════════════════════════════════════════════
# 四、金融代理指标 — 非油价，更结构性
# ══════════════════════════════════════════════════════════════════════════════
out("## 四、金融代理指标\n")
out("> 油价本身情绪性强、噪音大，以下指标更直接或更结构性\n")

# ── 4.1 ILS/USD — 以色列谢克尔 (权重 2.0) ─────────────────────────────────
out("### 4.1 ILS/USD — 以色列谢克尔（最直接地区当事方）\n")
out("谢克尔贬值 = 以色列市场定价冲突升级风险，领先于国际油市\n")
try:
    ils = fetch_ticker("ILS=X", "3mo")
    if ils is not None and len(ils) > 60:
        cur    = ils["Close"].iloc[-1]
        w5_ago = ils["Close"].iloc[-5]
        avg60  = ils["Close"].iloc[-60:].mean()
        chg5d  = (cur / w5_ago - 1) * 100
        vs60   = (cur / avg60 - 1) * 100
        # Higher ILS=X means more USD per ILS → shekel stronger (lower = weaker shekel)
        # Actually ILS=X is USD/ILS so higher = dollar buys more shekels = shekel weaker
        out(f"| 指标 | 数值 |")
        out(f"|------|------|")
        out(f"| 当前 USD/ILS | {cur:.4f} |")
        out(f"| 5 日变化 | {chg5d:+.2f}% ({'谢克尔贬值' if chg5d > 0 else '谢克尔升值'}) |")
        out(f"| vs 60 日均 | {vs60:+.2f}% |")

        # 谢克尔贬值（USD/ILS 上涨）= 冲突风险上升
        if chg5d >= 3 or vs60 >= 5:
            sc = 80; lv = "RED 显著贬值，冲突风险高"
        elif chg5d >= 1.5 or vs60 >= 2.5:
            sc = 55; lv = "YELLOW 温和贬值"
        elif chg5d <= -2:
            sc = 10; lv = "GREEN 升值，风险下降"
        else:
            sc = 25; lv = "GREEN 平稳"
        out(f"| 风险信号 | {lv} |")
        risk_scores["ils"] = (sc, 2.0, f"USD/ILS 5日 {chg5d:+.2f}%")
    else:
        out("数据不足\n")
        risk_scores["ils"] = (50.0, 2.0, "ILS 数据不足")
except Exception as e:
    out(f"获取失败: {e}\n")
    risk_scores["ils"] = (50.0, 2.0, f"ILS 异常")
out()

# ── 4.2 LNG 价格代理 (权重 1.0) ─────────────────────────────────────────────
out("### 4.2 LNG 价格代理（韩国能源结构直接相关）\n")
out("> 真实指标: JKM (Japan-Korea Marker)，需付费数据。以下为免费代理\n")
out("| 代理 | 含义 | 当前 | 5日变化 | 状态 |")
out("|------|------|------|---------|------|")

lng_scores: list[float] = []
for ticker, label, note in [
    ("NG=F",  "Henry Hub 天然气期货", "美国基准，全球联动"),
    ("LNG",   "Cheniere Energy 股票", "美国最大 LNG 出口商"),
]:
    try:
        h = fetch_ticker(ticker, "2mo")
        if h is not None and len(h) > 5:
            cur  = h["Close"].iloc[-1]
            prev = h["Close"].iloc[-5]
            chg  = (cur / prev - 1) * 100
            if ticker == "NG=F":
                if chg >= 15:
                    sc = 75; lv = "YELLOW 急涨"
                elif chg >= 8:
                    sc = 45; lv = "ORANGE 上涨"
                else:
                    sc = 20; lv = "GREEN"
                lng_scores.append(sc)
            out(f"| {ticker} | {label} ({note}) | {cur:.2f} | {chg:+.1f}% | {lv if ticker=='NG=F' else '—'} |")
        else:
            out(f"| {ticker} | {label} | N/A | — | — |")
    except:
        out(f"| {ticker} | {label} | 失败 | — | — |")
out()

sc_lng = np.mean(lng_scores) if lng_scores else 35.0
risk_scores["lng"] = (sc_lng, 1.0, f"LNG代理综合={sc_lng:.0f}")

# ── 4.3 OVX — 原油隐含波动率 (权重 0.8, 低权重因情绪性) ──────────────────
out("### 4.3 OVX — 原油隐含波动率\n")
out("> 低权重 (×0.8)：OVX 含有大量市场情绪噪音，仅作辅助参考\n")
try:
    ovx = fetch_ticker("^OVX", "6mo")
    if ovx is not None and len(ovx) > 60:
        cur    = ovx["Close"].iloc[-1]
        avg60  = ovx["Close"].iloc[-60:].mean()
        vs_avg = (cur / avg60 - 1) * 100

        if cur > 50:
            sc = 85; lv = "RED 极端恐慌"
        elif cur > 40:
            sc = 65; lv = "YELLOW 偏高"
        elif vs_avg > 30:
            sc = 50; lv = "YELLOW 高于均值 30%+"
        elif vs_avg > 15:
            sc = 35; lv = "ORANGE 略高"
        else:
            sc = 15; lv = "GREEN 正常"

        risk_scores["ovx"] = (sc, 0.8, f"OVX={cur:.1f}, vs60日均{vs_avg:+.1f}%")
        out(f"| OVX | {cur:.1f} | 60日均 {avg60:.1f} | 偏离 {vs_avg:+.1f}% | {lv} |")
    else:
        out("数据不足\n")
        risk_scores["ovx"] = (35.0, 0.8, "OVX 数据不足")
except Exception as e:
    out(f"获取失败: {e}\n")
    risk_scores["ovx"] = (35.0, 0.8, "OVX 异常")
out()


# ══════════════════════════════════════════════════════════════════════════════
# 五、综合风险评分
# ══════════════════════════════════════════════════════════════════════════════
out("## 五、伊朗冲突综合风险评分\n")

COMPONENT_LABELS = {
    "acled":            "ACLED 攻击事件",
    "opensky_gulf":     "海湾航班数量",
    "opensky_iran_fir": "伊朗领空航班",
    "gdelt":            "GDELT 新闻加速",
    "ils":              "以色列谢克尔",
    "lng":              "LNG 价格代理",
    "ovx":              "OVX 原油波动率",
}

total_weight    = sum(w for _, w, _ in risk_scores.values())
weighted_score  = sum(s * w for s, w, _ in risk_scores.values()) / total_weight if total_weight else 50

out("### 5.1 分项明细\n")
out("| 指标 | 评分 (0-100) | 权重 | 描述 |")
out("|------|-------------|------|------|")
for key, (sc, wt, desc) in risk_scores.items():
    label = COMPONENT_LABELS.get(key, key)
    bar   = "█" * int(sc / 10) + "░" * (10 - int(sc / 10))
    out(f"| {label} | {bar} {sc:.0f} | ×{wt:.1f} | {desc} |")
out()

out(f"### 5.2 综合评分: **{weighted_score:.0f} / 100**\n")

if weighted_score >= 70:
    level  = "🔴 RED — 高风险"
    advice = ("伊朗冲突对 EWY 构成重大下行威胁。\n"
              "建议: 暂停新开仓，已有头寸加紧止损，等待 ACLED 事件数回落至平均水平。")
elif weighted_score >= 45:
    level  = "🟡 YELLOW — 中等风险"
    advice = ("冲突信号明显但未达极端。\n"
              "建议: 缩小单次仓位至正常的 50%，日内盯盘 OpenSky 航班变化。")
elif weighted_score >= 25:
    level  = "🟠 ORANGE — 低中风险"
    advice = ("地缘风险存在但可控。\n"
              "建议: 正常执行策略，每日更新本报告，设置 ACLED 邮件提醒。")
else:
    level  = "🟢 GREEN — 低风险"
    advice = "冲突直接指标平稳，正常执行 EWY 策略。"

out(f"**风险等级**: {level}\n")
out(f"**策略建议**: {advice}\n")

# ══════════════════════════════════════════════════════════════════════════════
# 六、手动补充项（需商业 API 或人工查询）
# ══════════════════════════════════════════════════════════════════════════════
out("## 六、手动补充项\n")
out("> 以下指标因需商业 API 或人工查询无法自动获取，请每周核对\n")

if not ACLED_EMAIL or not ACLED_KEY:
    out("### ⚠️ ACLED 注册（最优先）\n")
    out("1. 访问 https://acleddata.com/register/ 免费注册")
    out("2. 获得 API Key 后设置环境变量:")
    out("   ```bash")
    out("   export ACLED_EMAIL='your@email.com'")
    out("   export ACLED_API_KEY='your_key_here'")
    out("   ```")
    out("3. 重新运行本脚本（ACLED 权重最高，当前用中性值 50 代替）\n")

out("""
| 指标 | 数据源 | 正常范围 | 警戒线 | 频率 |
|------|--------|----------|--------|------|
| **BDTI 波罗的海脏油轮指数** | balticexchange.com | 600–800 点 | 单周 +20% | 每日 |
| **霍尔木兹日均 VLCC 过境** | marinetraffic.com → Strait of Hormuz | 18–22 艘/天 | 连续 3 天低 15%+ | 每日 |
| **Lloyd's JWC 战争险区域** | lloydsmarketassociation.org | 未列入 | 新增海湾/伊朗 | 事件驱动 |
| **NOTAM 伊朗领空 (OIIX)** | notams.faa.gov / skyvector.com | 无限飞禁令 | 发布闭空 NOTAM | 事件驱动 |
| **GPR 地缘政治风险指数** | matteoiacoviello.com/gpr.htm | ~100 | > 200 | 月度 |
| **韩国对中东原油依存度** | energy.korea.kr / 韩国能源公单 | 中东 ~70% | 份额骤降 | 月度 |
| **JKM LNG 现货价** | platts.com / icis.com | 历史均值 | 单月 +30% | 每日（需付费）|

### 指标体系完整分级图

```
第一层（直接）   ACLED 攻击事件数  →  OpenSky 航班数  →  NOTAM 闭空令
                      ↓ 延迟 0–6 小时
第二层（结构）   ILS/USD 谢克尔  →  BDTI 油轮运费  →  Lloyd's 战争险
                      ↓ 延迟 6–24 小时
第三层（金融）   JKM LNG  →  OVX 原油波动率  →  GDELT 新闻密度
                      ↓ 延迟 1–3 天
第四层（结果）   WTI/Brent 油价  →  EWY 价格  →  韩国出口数据
```
""")

# ══════════════════════════════════════════════════════════════════════════════
# 保存报告
# ══════════════════════════════════════════════════════════════════════════════
if SAVE_FILE:
    os.makedirs(REPORT_DIR, exist_ok=True)
    content = "\n".join(_lines)
    Path(REPORT_PATH).write_text(content, encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"  报告已保存: {REPORT_PATH}")
    print(f"  综合评分:   {weighted_score:.0f}/100 — {level}")
    print(f"{'='*60}")
