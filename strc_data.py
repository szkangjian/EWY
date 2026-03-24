import yfinance as yf
import pandas as pd
import requests
from datetime import datetime, timedelta

def get_strc_data_yahoo_all_granularities():
    """
    通过 Yahoo Finance 获取不同精度的 STRC 基础数据。
    - 1m: 最近 7 天
    - 15m: 最近 60 天
    - 1h: 最近 2 年
    - 1d: 全部历史
    """
    ticker = "STRC"
    configs = [
        {"interval": "1m", "period": "7d", "name": "1minute"},
        {"interval": "15m", "period": "60d", "name": "15minute"},
        {"interval": "1h", "period": "2y", "name": "1hour"},
        {"interval": "1d", "period": "max", "name": "daily"}
    ]
    
    results = {}
    for cfg in configs:
        print(f"--- 正在获取 Yahoo {cfg['name']} 数据 (期间: {cfg['period']}) ---")
        data = yf.download(ticker, period=cfg['period'], interval=cfg['interval'])
        if not data.empty:
            filename = f"strc_{cfg['name']}_yahoo.csv"
            data.to_csv(filename)
            print(f"成功保存 {len(data)} 条数据至 {filename}")
            results[cfg['name']] = data
        else:
            print(f"未能获取到 {cfg['name']} 数据。")
    return results

def get_strc_historical_minute_polygon(api_key, date_from, date_to):
    """
    使用 Polygon.io 获取更久远的历史分钟数据。
    免费版支持长达 2 年的历史数据，但每分钟限 5 次 API 请求。
    """
    if not api_key or api_key == "YOUR_API_KEY":
        print("\n[提示] 请在代码中填入您的 Polygon.io 免费 API Key 以使用此功能。")
        return None

    print(f"--- 正在通过 Polygon.io 获取从 {date_from} 到 {date_to} 的分钟数据 ---")
    url = f"https://api.polygon.io/v2/aggs/ticker/STRC/range/1/minute/{date_from}/{date_to}?adjusted=true&sort=asc&apiKey={api_key}"
    
    response = requests.get(url)
    if response.status_code == 200:
        results = response.json().get("results", [])
        if not results:
            print("未找到该时间段内的结果。")
            return None
        
        # 转换为 DataFrame
        df = pd.DataFrame(results)
        # Polygon 返回的时间戳是毫秒，转换为可读时间
        df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
        df.set_index('timestamp', inplace=True)
        # 重命名列以提高可读性
        df.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'}, inplace=True)
        print(f"成功获取 {len(df)} 条数据。")
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    else:
        print(f"请求失败，状态码: {response.status_code}, 错误详情: {response.text}")
        return None

def get_strc_intraday_alpha_vantage(api_key):
    """
    使用 Alpha Vantage 获取最近 1-2 年的分钟数据 (TIME_SERIES_INTRADAY_EXTENDED)。
    免费 Key 限每天 25 次请求。
    注册链接: https://www.alphavantage.co/support/#api-key
    """
    if api_key == "YOUR_API_KEY":
        print("\n[提示] 请填入 Alpha Vantage API Key。")
        return None
    
    # 注意：TIME_SERIES_INTRADAY_EXTENDED 接口返回 CSV
    print("--- 正在通过 Alpha Vantage 获取历史分钟数据 (year1month1) ---")
    url = f'https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY_EXTENDED&symbol=STRC&interval=1min&slice=year1month1&adjusted=true&apikey={api_key}'
    
    response = requests.get(url)
    if response.status_code == 200:
        import io
        df = pd.read_csv(io.StringIO(response.text))
        if 'time' in df.columns:
            df.set_index('time', inplace=True)
            print(f"成功获取 {len(df)} 条数据。")
            return df
        else:
            print("未获取到有效数据，API 返回内容开头:", response.text[:100])
            return None
    return None

def get_strc_twelve_data(api_key):
    """
    使用 Twelve Data 获取数据。
    免费版限每天 800 次，每分钟 8 次。
    注册链接: https://twelvedata.com/pricing
    """
    if not api_key or api_key == "YOUR_API_KEY":
        print("\n[提示] 请填入 Twelve Data API Key。")
        return None
        
    print("--- 正在通过 Twelve Data 获取分钟数据 ---")
    url = f"https://api.twelvedata.com/time_series?symbol=STRC&interval=1min&apikey={api_key}"
    
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if "values" in data:
            df = pd.DataFrame(data["values"])
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
            print(f"成功获取 {len(df)} 条数据。")
            return df
    print("Twelve Data 获取失败。")
    return None

if __name__ == "__main__":
    # --- 1. 使用 Yahoo Finance 获取尽可能多的基础数据 ---
    results_yf = get_strc_data_yahoo_all_granularities()
    if "daily" in results_yf:
        print("\nSTRC 全部日线历史数据样例 (前 5 行):")
        print(results_yf["daily"].head())

    # --- 各平台 API Key (请在此处填入您申请到的 Key) ---
    from config import POLYGON_KEY, TWELVE_DATA_KEY
    KEYS = {
        "POLYGON": POLYGON_KEY,      # 注册: https://polygon.io/
        "ALPHA_VANTAGE": "YOUR_API_KEY",                     # 注册: https://www.alphavantage.co/support/#api-key
        "TWELVE_DATA": TWELVE_DATA_KEY,   # 注册: https://twelvedata.com/pricing
    }
    
    # --- 2. 使用 Polygon.io 获取历史数据 ---
    if KEYS["POLYGON"] != "YOUR_API_KEY":
        print("\n" + "="*50)
        today = datetime.now()
        yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        day_before = (today - timedelta(days=2)).strftime('%Y-%m-%d')
        df_poly = get_strc_historical_minute_polygon(KEYS["POLYGON"], day_before, yesterday)
        if df_poly is not None:
            print(df_poly.head())
    
    # --- 3. 使用 Alpha Vantage 获取 (默认注释掉，因为 Key 可能未填) ---
    # if KEYS["ALPHA_VANTAGE"] != "YOUR_API_KEY":
    #     df_av = get_strc_intraday_alpha_vantage(KEYS["ALPHA_VANTAGE"])
    
    # --- 4. 使用 Twelve Data 获取 ---
    # if KEYS["TWELVE_DATA"] != "YOUR_API_KEY":
    #     df_td = get_strc_twelve_data(KEYS["TWELVE_DATA"])
