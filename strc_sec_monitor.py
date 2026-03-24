import feedparser
import requests
import datetime
import time

# Strategy (MicroStrategy) CIK number on SEC EDGAR is 0001050446
CIK = "0001050446"
RSS_URL = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={CIK}&type=8-K&dateb=&owner=exclude&start=0&count=10&output=atom"

# SEC requires a user-agent in the format "Company Name Email"
HEADERS = {
    'User-Agent': 'STRC-Dividend-Monitor bot@example.com'
}

def check_sec_filings():
    print("=" * 60)
    print("📡 正在轮询 SEC EDGAR 数据库: 获取 Strategy Inc. 8-K 月度分红公告...")
    print("=" * 60)
    
    try:
        response = requests.get(RSS_URL, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"❌ 请求 SEC API 失败: HTTP {response.status_code}")
            return
            
        feed = feedparser.parse(response.content)
        
        if not feed.entries:
            print("没有找到近期的 8-K 报告。")
            return
            
        found_dividend = False
        print(f"📄 找到 {len(feed.entries)} 份最近的 8-K 报告：\n")
        
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            published = entry.published
            
            # 判断标题是否与分红/优先股相关 (SEC的标题有时较简略，通常包含 'Declaration of Dividend' 或类似字眼)
            is_dividend_related = 'dividend' in title.lower() or 'preferred' in title.lower() or 'series a' in title.lower()
            
            flag = "⭐ [高度疑似分红公告]" if is_dividend_related else ""
            
            print(f"📅 发布时间: {published}")
            print(f"📰 文件标题: {title} {flag}")
            print(f"🔗 原文链接: {link}\n")
            
            if is_dividend_related:
                found_dividend = True
                
        if found_dividend:
            print("\n💡 建议：请点击上方带有 ⭐ 的原文链接，查看具体的除息日 (Ex-Date) 和每股派息金额 (Dividend Rate)。")
        else:
            print("\n⏳ 尚未在最近的公告中发现本月的 STRC 股息宣告，请过几天再试（通常在月末/月初发布）。")
            
    except Exception as e:
        print(f"❌ 运行报错: {e}")

if __name__ == '__main__':
    check_sec_filings()
