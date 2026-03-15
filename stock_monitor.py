import requests
import os
import datetime
import time
from duckduckgo_search import DDGS

def get_stock_data(stock_code):
    """获取基础股票数据"""
    url = f"http://qt.gtimg.cn/q={stock_code}"
    try:
        response = requests.get(url, timeout=10)
        data_str = response.text
        if "=" not in data_str or len(data_str.split('~')) < 33:
            return None
        info_list = data_str.split('"')[1].split('~')
        return {
            "name": info_list[1],           
            "code": info_list[2],           
            "current_price": info_list[3],  
            "change_percent": info_list[32] 
        }
    except:
        return None

def get_latest_news(stock_name):
    """搜索最新新闻"""
    try:
        results = DDGS().text(f"{stock_name} 股票 财经 最新消息", max_results=3)
        if not results:
            return "近期暂无重大新闻。", "- 暂无新闻数据"
        
        news_list = []
        titles_for_display = []
        for r in results:
            news_list.append(f"标题：{r['title']} \n摘要：{r['body']}")
            titles_for_display.append(f"- {r['title']}")
            
        return "\n".join(news_list), "\n".join(titles_for_display)
    except:
        return "新闻抓取失败。", "- 暂无新闻数据"

def get_ai_analysis(stock_data, news_text, api_key):
    """调用 AI 进行分析 (静默版)"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""你是专业的金融分析师。
【今日盘面】股票：{stock_data['name']}，当前价格：{stock_data['current_price']}，今日涨跌幅：{stock_data['change_percent']}%。
【最新资讯】
{news_text}

请结合上述盘面数据和资讯，用一段话（100字左右）客观点评今天的市场情绪或异动原因。语气沉稳专业，语言精炼，绝对不要给出任何买卖投资建议。"""
    
    payload = {
        # 换成了并发更高、极其稳定的免费大模型
        "model": "google/gemini-2.0-flash-lite-preview-02-05:free",
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return "AI 接口当前拥堵，分析生成失败。"
    except:
        return "AI 请求超时或网络异常。"

def send_to_wechat(webhook_url, stock_data, ai_comment, news_titles_display):
    """发送 Markdown 消息到企业微信"""
    color = "warning" if float(stock_data['change_percent']) > 0 else "info"
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    content = f"""**📈 股票深度监控 (AI 驱动版)**
> 股票名称：<font color="comment">{stock_data['name']} ({stock_data['code']})</font>
> 当前价格：**{stock_data['current_price']}**
> 今日涨跌：<font color="{color}">{stock_data['change_percent']}%</font>
> 
> 📰 **最新资讯速览**：
> <font color="comment">{news_titles_display}</font>
> 
> 🤖 **AI 综合点评**：
> <font color="info">{ai_comment}</font>
> 
> 更新时间：<font color="comment">{current_time}</font>"""

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content}
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except:
        pass

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") 
    
    # 股票池
    STOCK_CODES = [
        "sz000001",  # 平安银行
        "sh600519",  # 贵州茅台
        "hk00700"    # 腾讯控股
    ]
    
    if WEBHOOK_URL and OPENROUTER_KEY:
        for index, code in enumerate(STOCK_CODES):
            data = get_stock_data(code)
            
            if data:
                news_full_text, news_titles = get_latest_news(data['name'])
                ai_comment = get_ai_analysis(data, news_full_text, OPENROUTER_KEY)
                send_to_wechat(WEBHOOK_URL, data, ai_comment, news_titles)
            
            # 保持 15 秒的休眠，保护免费接口额度
            if index < len(STOCK_CODES) - 1:
                time.sleep(15)
