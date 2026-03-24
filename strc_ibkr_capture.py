import asyncio
import sys
import datetime
from ib_insync import *
import requests

try:
    from config import FINNHUB_KEY
except ImportError:
    print("❌ 错误: 找不到 config.py，请参考 config_example.py 配置 FINNHUB_KEY")
    sys.exit(1)

# ====== 配置参数 ======
STRC_QUANTITY = 1000         # 每次 Capture 买入的股数
MAX_BUY_PRICE = 100.10       # 绝对拒绝买入的最高限价（防追高）
MIN_BUY_PRICE = 98.00        # 绝度拒绝买入的最低限价（防闪崩接飞刀）
BTC_CRASH_THRESHOLD = -0.05  # 比特币日内跌幅超过 5% 即中止执行

# IBKR 连接配置 (TWS默认 7496/7497; Gateway默认 4001/4002)
IB_HOST = '127.0.0.1'
IB_PORT = 7497
IB_CLIENT_ID = 999
# ======================

def check_btc_safety():
    """使用 Finnhub 检查比特币真实跌幅，防止大跌接飞刀"""
    print("🔍 正在检查宏观风险 (比特币价格走势)...")
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol=BINANCE:BTCUSDT&token={FINNHUB_KEY}"
        resp = requests.get(url, timeout=5).json()
        current_price = resp.get('c', 0)
        prev_close = resp.get('pc', 0)
        
        if prev_close == 0:
            print("⚠️ 警告: 无法获取比特币昨收价，跳过检查。")
            return True
            
        drop_pct = (current_price - prev_close) / prev_close
        print(f"   当前 BTC: ${current_price:,.2f} (日内涨跌: {drop_pct*100:.2f}%)")
        
        if drop_pct < BTC_CRASH_THRESHOLD:
            print(f"❌ 触发保险丝: 比特币暴跌 {drop_pct*100:.2f}%，远超安全阈值 {BTC_CRASH_THRESHOLD*100}%。停止执行！")
            return False
        return True
    except Exception as e:
        print(f"⚠️ 警告: 检查比特币时发生网络错误 {e}，为安全起见中止执行。")
        return False

def main():
    print("=" * 60)
    print("🚀 STRC 半自动股息捕获 (Dividend Capture) 执行脚本")
    print("=" * 60)

    # 1. 宏观防暴雷检查
    if not check_btc_safety():
        sys.exit(1)

    print("\n🔌 连接到 Interactive Brokers...")
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    except Exception as e:
        print(f"❌ 连接 IBKR 失败。请确认 TWS/Gateway 已登录且开启了 API 访问 (端口 {IB_PORT})。")
        sys.exit(1)

    # 2. 获取 STRC 合约和行情
    contract = Stock('STRC', 'SMART', 'USD')
    try:
        ib.qualifyContracts(contract)
    except Exception:
        print("❌ 无法识别 STRC 合约。")
        ib.disconnect()
        sys.exit(1)

    print(f"🔍 请求 STRC 实时行情...")
    market_data = ib.reqMktData(contract, '', False, False)
    # 给 IB 几秒钟返回行情
    ib.sleep(2)
    
    bid = market_data.bid
    ask = market_data.ask
    last = market_data.last
    
    # 简单的市价估算
    current_px = ask if (ask and ask > 0) else last
    if not current_px or current_px <= 0:
        print("❌ 无法获取 STRC 有效市价，请检查交易时间是否在盘中。")
        ib.disconnect()
        sys.exit(1)
        
    print(f"   当前买一: ${bid} | 卖一: ${ask} | 最新价: ${last}")
    
    # 3. 计算微量滑点买入价 (市价之上 +$0.02 保证成交)
    target_buy_price = round(current_px + 0.02, 2)
    print(f"\n🧠 算法计算目标限价 (Limit Buy): ${target_buy_price}")

    # 个股保险丝检查
    if target_buy_price > MAX_BUY_PRICE:
        print(f"❌ 触发保险丝: 目标价 ${target_buy_price} 超过硬顶 ${MAX_BUY_PRICE}，机构可能在砸盘拉升，拒绝高位接盘。")
        ib.disconnect()
        sys.exit(1)
    if target_buy_price < MIN_BUY_PRICE:
        print(f"❌ 触发保险丝: 目标价 ${target_buy_price} 低于铁底 ${MIN_BUY_PRICE}，MSTR 可能出了大问题，拒绝接飞刀。")
        ib.disconnect()
        sys.exit(1)

    print(f"\n⚠️ 请确认以下订单信息:")
    print(f"   ➤ [1] 立即买入: {STRC_QUANTITY} 股 STRC @ Limit ${target_buy_price}")
    # 卖出单自动设定为买入价 -$0.96(大致股息) 的附近，第二天盘前自动挂出
    target_sell_price = round(target_buy_price - 0.40, 2) 
    print(f"   ➤ [2] 明日卖出: {STRC_QUANTITY} 股 STRC @ GTC Limit ${target_sell_price} (防深跌保护单)")
    
    confirm = input("\n输入 'YES' 确认执行下单 (其他任意键取消): ")
    if confirm != 'YES':
        print("🚫 用户取消执行。")
        ib.disconnect()
        sys.exit(0)

    # 4. 执行下单
    print("\n🚀 正在发送订单...")
    
    # 买单：当日有效 (DAY)
    buy_order = LimitOrder('BUY', STRC_QUANTITY, target_buy_price, tif='DAY')
    buy_trade = ib.placeOrder(contract, buy_order)
    
    # 等待订单回报
    ib.sleep(2)
    print(f"✅ 买单已提交! 当前状态: {buy_trade.orderStatus.status}")

    # 卖单：取消前有效 (GTC)。设置 limit 价格为一个保守防守价，第二天 9:30 会自动参与竞价
    # 你可以在第二天 9:25 手动将这个单子的价格改成市价，或者就让它挂着作为底线保护。
    sell_order = LimitOrder('SELL', STRC_QUANTITY, target_sell_price, tif='GTC')
    # IB API 内部其实很难直接指定单一 Tax Lot，需要在成交后去 Tax Optimizer 调整，或者全局设置 LIFO
    sell_trade = ib.placeOrder(contract, sell_order)
    ib.sleep(1)
    print(f"✅ 明日防护卖单已提交! 当前状态: {sell_trade.orderStatus.status}")

    print("\n🎉 脚本执行完毕。")
    print("❗ 【重要税务提醒】: 请登录 IBKR 网页版 -> Tax Optimizer 或者检查全局设置，确保你的账户默认匹配方式为 [LIFO]！")
    
    ib.disconnect()

if __name__ == '__main__':
    main()
