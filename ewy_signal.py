"""
EWY 每日交易信号生成器

策略：
  主策略 — IBS (Internal Bar Strength)
    买入: IBS < 0.2 且 Close > MA200 → 收盘买入
    卖出: IBS > 0.8 → 收盘卖出
    最大持有: 10 个交易日

  辅助策略 — 跌幅触发反弹
    买入: 盘中跌幅 > 3.5% (vs 前日收盘)
    卖出: 反弹 +2.0% 或持有 5 天
    (盘中监控，需配合 realtime_ewy.py)

用法:
  uv run python ewy_signal.py           # 每日收盘后运行
  uv run python ewy_signal.py --update  # 先更新数据再出信号

熔断条件:
  - 连续 3 笔到期亏损 → 暂停
  - EWY < MA200 → 暂停
"""

import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import warnings
from ewy_market_data import build_daily_bars, load_minute_data, load_regular_session_data

warnings.filterwarnings('ignore')

# ---- 配置 ----
DATA_CSV = "ewy_minute_data.csv"
STATE_FILE = "ewy_signal_state.json"
MA_PERIOD = 200
IBS_BUY = 0.2
IBS_SELL = 0.8
MAX_HOLD = 10
DROP_ENTRY = -0.035
DROP_EXIT = 0.02
DROP_MAX_HOLD = 5
CIRCUIT_BREAKER_LOSSES = 3  # 连续到期亏损次数触发熔断


def load_state():
    """加载持仓状态"""
    p = Path(STATE_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return {
        "ibs_position": None,      # {"buy_date": "2026-03-11", "buy_price": 130.30, "days_held": 0}
        "drop_position": None,     # {"buy_date": ..., "buy_price": ..., "days_held": 0}
        "trade_log": [],           # [{"date": ..., "buy": ..., "sell": ..., "ret": ..., "reason": ...}]
        "consecutive_exp_losses": 0,
        "circuit_breaker": False
    }


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False))


def build_daily(csv_path):
    """从分钟数据构建日线 + 技术指标"""
    df = load_regular_session_data(csv_path)
    daily = build_daily_bars(df)

    daily['ma200'] = daily['Close'].rolling(MA_PERIOD).mean()
    daily['IBS'] = (daily['Close'] - daily['Low']) / (daily['High'] - daily['Low'])
    daily['ret'] = daily['Close'].pct_change()
    daily['prev_close'] = daily['Close'].shift(1)
    daily['day_change'] = (daily['Close'] / daily['prev_close'] - 1)

    return daily


def check_circuit_breaker(state, daily):
    """检查熔断条件"""
    alerts = []
    latest = daily.iloc[-1]

    # 条件1: 连续到期亏损
    if state["consecutive_exp_losses"] >= CIRCUIT_BREAKER_LOSSES:
        state["circuit_breaker"] = True
        alerts.append(f"!! 熔断: 连续 {state['consecutive_exp_losses']} 笔到期亏损")

    # 条件2: EWY < MA200
    if pd.notna(latest['ma200']) and latest['Close'] < latest['ma200']:
        state["circuit_breaker"] = True
        alerts.append(f"!! 熔断: EWY ${latest['Close']:.2f} < MA200 ${latest['ma200']:.2f}")

    # 如果之前熔断，检查是否可以恢复
    if state["circuit_breaker"] and state["consecutive_exp_losses"] < CIRCUIT_BREAKER_LOSSES:
        if pd.notna(latest['ma200']) and latest['Close'] > latest['ma200']:
            state["circuit_breaker"] = False
            alerts.append(">> 熔断解除: EWY 回到 MA200 之上且亏损链断裂")

    return alerts


def generate_signal(daily, state):
    """生成今日交易信号"""
    today = daily.iloc[-1]
    yesterday = daily.iloc[-2] if len(daily) > 1 else None
    signals = []
    alerts = []

    date_str = str(today['date'].date())
    close = today['Close']
    ibs = today['IBS']
    ma200 = today['ma200']
    day_ret = today['day_change']

    # ---- 状态打印 ----
    print(f"\n{'='*60}")
    print(f"  EWY 交易信号 | {date_str}")
    print(f"{'='*60}")
    print(f"  收盘: ${close:.2f}  |  MA200: ${ma200:.2f}" if pd.notna(ma200) else f"  收盘: ${close:.2f}  |  MA200: N/A")
    print(f"  IBS:  {ibs:.3f}   |  日涨跌: {day_ret*100:+.2f}%")
    print(f"  开盘: ${today['Open']:.2f}  最高: ${today['High']:.2f}  最低: ${today['Low']:.2f}")
    print(f"  成交量: {today['Vol']:,.0f}")

    # ---- 熔断检查 ----
    cb_alerts = check_circuit_breaker(state, daily)
    alerts.extend(cb_alerts)

    if state["circuit_breaker"]:
        print(f"\n  {'!'*40}")
        print(f"  策略已熔断 — 不接受新信号")
        for a in cb_alerts:
            print(f"  {a}")
        print(f"  {'!'*40}")

    # ---- IBS 策略信号 ----
    print(f"\n  --- IBS 策略 ---")

    if state["ibs_position"]:
        pos = state["ibs_position"]
        pos["days_held"] += 1
        hold_ret = (close / pos["buy_price"] - 1) * 100
        print(f"  持仓中: 买入 {pos['buy_date']} @ ${pos['buy_price']:.2f}")
        print(f"  持有 {pos['days_held']} 天 | 浮盈: {hold_ret:+.2f}%")

        # 卖出信号
        if ibs > IBS_SELL:
            signals.append({
                "strategy": "IBS",
                "action": "SELL",
                "reason": f"IBS={ibs:.3f} > {IBS_SELL}",
                "price": close
            })
            # 记录交易
            ret = close / pos["buy_price"] - 1
            trade = {
                "strategy": "IBS",
                "buy_date": pos["buy_date"],
                "sell_date": date_str,
                "buy_price": pos["buy_price"],
                "sell_price": round(close, 2),
                "days": pos["days_held"],
                "ret": round(ret * 100, 2),
                "reason": "IBS>0.8"
            }
            state["trade_log"].append(trade)
            state["ibs_position"] = None
            # IBS>0.8 退出不是"到期亏损"，重置连亏计数
            if ret > 0:
                state["consecutive_exp_losses"] = 0
            print(f"  >> 卖出信号: IBS={ibs:.3f} | 收益 {ret*100:+.2f}%")

        elif pos["days_held"] >= MAX_HOLD:
            signals.append({
                "strategy": "IBS",
                "action": "SELL",
                "reason": f"持有到期 ({MAX_HOLD}天)",
                "price": close
            })
            ret = close / pos["buy_price"] - 1
            trade = {
                "strategy": "IBS",
                "buy_date": pos["buy_date"],
                "sell_date": date_str,
                "buy_price": pos["buy_price"],
                "sell_price": round(close, 2),
                "days": pos["days_held"],
                "ret": round(ret * 100, 2),
                "reason": "EXP"
            }
            state["trade_log"].append(trade)
            state["ibs_position"] = None
            if ret < 0:
                state["consecutive_exp_losses"] += 1
            else:
                state["consecutive_exp_losses"] = 0
            print(f"  >> 到期卖出: {MAX_HOLD}天 | 收益 {ret*100:+.2f}%")

        else:
            print(f"  -- 继续持有")

    else:
        # 买入信号
        above_ma = pd.notna(ma200) and close > ma200
        ibs_low = ibs < IBS_BUY

        if ibs_low and above_ma and not state["circuit_breaker"]:
            signals.append({
                "strategy": "IBS",
                "action": "BUY",
                "reason": f"IBS={ibs:.3f} < {IBS_BUY}, Close > MA200",
                "price": close
            })
            state["ibs_position"] = {
                "buy_date": date_str,
                "buy_price": round(close, 2),
                "days_held": 0
            }
            print(f"  >> 买入信号: IBS={ibs:.3f} | 价格 ${close:.2f}")
        else:
            reasons = []
            if not ibs_low:
                reasons.append(f"IBS={ibs:.3f} >= {IBS_BUY}")
            if not above_ma:
                reasons.append("Close < MA200" if pd.notna(ma200) else "MA200 不可用")
            if state["circuit_breaker"]:
                reasons.append("熔断中")
            print(f"  -- 无信号 ({', '.join(reasons)})")

    # ---- 跌幅策略信号 ----
    print(f"\n  --- 跌幅策略 ---")

    if state["drop_position"]:
        pos = state["drop_position"]
        pos["days_held"] += 1
        hold_ret = (close / pos["buy_price"] - 1) * 100

        if hold_ret >= DROP_EXIT * 100:
            signals.append({
                "strategy": "DROP",
                "action": "SELL",
                "reason": f"反弹达标 +{DROP_EXIT*100:.1f}%",
                "price": pos["buy_price"] * (1 + DROP_EXIT)
            })
            ret = DROP_EXIT
            trade = {
                "strategy": "DROP",
                "buy_date": pos["buy_date"],
                "sell_date": date_str,
                "buy_price": pos["buy_price"],
                "sell_price": round(pos["buy_price"] * (1 + DROP_EXIT), 2),
                "days": pos["days_held"],
                "ret": round(ret * 100, 2),
                "reason": "TP"
            }
            state["trade_log"].append(trade)
            state["drop_position"] = None
            state["consecutive_exp_losses"] = 0
            print(f"  >> 反弹达标卖出: +{DROP_EXIT*100:.1f}% | 收益 {ret*100:+.2f}%")

        elif pos["days_held"] >= DROP_MAX_HOLD:
            signals.append({
                "strategy": "DROP",
                "action": "SELL",
                "reason": f"持有到期 ({DROP_MAX_HOLD}天)",
                "price": close
            })
            ret = close / pos["buy_price"] - 1
            trade = {
                "strategy": "DROP",
                "buy_date": pos["buy_date"],
                "sell_date": date_str,
                "buy_price": pos["buy_price"],
                "sell_price": round(close, 2),
                "days": pos["days_held"],
                "ret": round(ret * 100, 2),
                "reason": "EXP"
            }
            state["trade_log"].append(trade)
            state["drop_position"] = None
            if ret < 0:
                state["consecutive_exp_losses"] += 1
            else:
                state["consecutive_exp_losses"] = 0
            print(f"  >> 到期卖出: {DROP_MAX_HOLD}天 | 收益 {ret*100:+.2f}%")

        else:
            print(f"  持仓中: 买入 {pos['buy_date']} @ ${pos['buy_price']:.2f} | {pos['days_held']}天 | 浮盈 {hold_ret:+.2f}%")

    else:
        # 跌幅触发检查（用日线近似，精确版在 realtime_ewy.py）
        if yesterday is not None:
            intraday_drop = (today['Low'] / yesterday['Close'] - 1)
            if intraday_drop <= DROP_ENTRY and not state["circuit_breaker"]:
                buy_price = yesterday['Close'] * (1 + DROP_ENTRY)
                signals.append({
                    "strategy": "DROP",
                    "action": "BUY",
                    "reason": f"盘中跌幅 {intraday_drop*100:.1f}% > {DROP_ENTRY*100:.0f}%",
                    "price": buy_price
                })
                state["drop_position"] = {
                    "buy_date": date_str,
                    "buy_price": round(buy_price, 2),
                    "days_held": 0
                }
                print(f"  >> 买入信号: 盘中最低 ${today['Low']:.2f}, 跌幅 {intraday_drop*100:.1f}%")
                print(f"     入场价 ${buy_price:.2f} (前日收盘 × {1+DROP_ENTRY:.2f})")
            else:
                if state["circuit_breaker"]:
                    print(f"  -- 熔断中，跳过")
                else:
                    intraday_str = f"{intraday_drop*100:+.1f}%" if yesterday is not None else "N/A"
                    print(f"  -- 无信号 (盘中最大跌幅 {intraday_str}, 阈值 {DROP_ENTRY*100:.0f}%)")
        else:
            print(f"  -- 数据不足")

    # ---- 交易历史 ----
    if state["trade_log"]:
        recent = state["trade_log"][-5:]
        print(f"\n  --- 最近交易 ---")
        print(f"  {'策略':>4} | {'买入日':>10} | {'卖出日':>10} | {'买入':>7} | {'卖出':>7} | {'天数':>2} | {'收益':>7} | 原因")
        print(f"  {'-'*75}")
        for t in recent:
            print(f"  {t['strategy']:>4} | {t['buy_date']:>10} | {t['sell_date']:>10} | "
                  f"${t['buy_price']:>6.2f} | ${t['sell_price']:>6.2f} | {t['days']:>2} | "
                  f"{t['ret']:>+6.2f}% | {t['reason']}")

        all_trades = state["trade_log"]
        total_ret = sum(t["ret"] for t in all_trades)
        wins = sum(1 for t in all_trades if t["ret"] > 0)
        print(f"\n  累计: {len(all_trades)} 笔, 胜率 {wins/len(all_trades)*100:.0f}%, 总收益 {total_ret:+.1f}%")

    if state["consecutive_exp_losses"] > 0:
        print(f"\n  连续到期亏损: {state['consecutive_exp_losses']} 笔 (熔断线: {CIRCUIT_BREAKER_LOSSES})")

    # ---- 信号汇总 ----
    if signals:
        print(f"\n  {'*'*40}")
        for s in signals:
            print(f"  * {s['strategy']} {s['action']} @ ${s['price']:.2f} — {s['reason']}")
        print(f"  {'*'*40}")
    else:
        print(f"\n  今日无交易信号")

    print()
    return signals, alerts


def update_data():
    """更新今日数据"""
    print("更新今日数据...")
    import yfinance as yf
    data = yf.download("EWY", period="5d", interval="1m", progress=False)
    if data.empty:
        print("未获取到数据")
        return False

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    data.index = data.index.tz_convert('US/Eastern').tz_localize(None)
    data.index.name = 'timestamp'
    data = data[['Open', 'High', 'Low', 'Close', 'Volume']]

    hist = load_minute_data(DATA_CSV).set_index('timestamp')
    combined = pd.concat([hist, data])
    combined = combined[~combined.index.duplicated(keep='last')]
    combined.sort_index(inplace=True)
    combined.to_csv(DATA_CSV)
    print(f"数据更新完成: {len(combined)} 条")
    return True


def main():
    if "--update" in sys.argv:
        update_data()

    daily = build_daily(DATA_CSV)
    state = load_state()

    signals, alerts = generate_signal(daily, state)

    save_state(state)


if __name__ == "__main__":
    main()
