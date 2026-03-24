import requests
import subprocess
import json
import os

def check_and_alert():
    polygon_key = os.environ.get('POLYGON_KEY')
    if not polygon_key:
        print("POLYGON_KEY environment variable is missing. Cannot fetch dividend data.")
        return

    print("Fetching recent dividends from Polygon API...")
    url = f"https://api.polygon.io/v3/reference/dividends?ticker=STRC&limit=5&apiKey={polygon_key}"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        results = response.json().get('results', [])
        if len(results) < 2:
            print("Not enough dividend history to compare.")
            return
            
        latest_div = results[0]
        prev_div = results[1]
        
        ex_date = latest_div.get('ex_dividend_date')
        current_amount = latest_div.get('cash_amount', 0)
        prev_amount = prev_div.get('cash_amount', 0)
        diff = current_amount - prev_amount
        
        # We use the exactly unique ex_date as our search key to avoid duplicate alerts
        search_key = f"STRC 股息公告: {ex_date}"
        
        print(f"Latest Ex-Date: {ex_date} | Amount: ${current_amount} | Prev: ${prev_amount}")
        
        # Check if we already alerted for this specific ex-date
        check_cmd = ['gh', 'issue', 'list', '--search', search_key, '--state', 'all', '--json', 'title']
        result = subprocess.run(check_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"GH CLI error: {result.stderr}")
            return
            
        issues = json.loads(result.stdout)
        
        if len(issues) == 0:
            print(f"New dividend cycle detected! Creating GitHub Issue for {ex_date}")
            
            # Determine Actionable Text based on The Payout Rule
            if diff >= 0.005: # Hike (tolerating minor float issues)
                strategy_action = "🟢 **加息 (Hike)**：护城河极厚，买盘托底溢价极强，**本月准许满 Margin 杠杆执行 Capture！**"
            elif diff <= -0.005: # Cut
                strategy_action = "🔴 **降息 (Cut)**：【系统熔断】估值溢价盘被击碎，跳空跌幅必将大于股息，**本月绝对禁止执行 Overnight Capture！**"
            else: # Unchanged
                strategy_action = "🟡 **平息 (Unchanged)**：正常出击。尽管有机械买盘托底，但 Cushion 利润垫会变薄，需调低净利预期。"
            
            diff_str = f"+${diff:.3f}" if diff > 0 else f"-${abs(diff):.3f}" if diff < 0 else "$0.00"
                
            issue_title = f"🚨 {search_key}"
            issue_body = f"### STRC 智能股息监控哨兵\n\n" \
                       f"Polygon API 刚刚捕捉到了 STRC 即将到来的新一轮派息除权数据！\n\n" \
                       f"📊 **硬核分红数据：**\n" \
                       f"- **即将除权日 (Ex-Date)**: `{ex_date}`\n" \
                       f"- **本月派息金额**: `${current_amount:.3f}`\n" \
                       f"- **对比上月金额**: `{diff_str}` (上月为 ${prev_amount:.3f})\n\n" \
                       f"---\n" \
                       f"### 🧠 风控引擎执行判定 (The Payout Rule)：\n\n" \
                       f"{strategy_action}\n\n" \
                       f"请前往 IBKR 或调用 `strc_ibkr_capture.py` 在 T-1 ({ex_date} 的前一个工作日) 提前部署。\n" \
                       f"@szkangjian"
            
            create_cmd = ['gh', 'issue', 'create', '--title', issue_title, '--body', issue_body]
            subprocess.run(create_cmd)
            print("Issue successfully created.")
        else:
            print(f"Announcement for ex-date {ex_date} has already been alerted. Skipping.")
                
    except Exception as e:
        print(f"Error during execution: {e}")

if __name__ == '__main__':
    check_and_alert()
