import requests
import os
import datetime
import time  # 新增：引入时间模块，用于添加延时
from duckduckgo_search import DDGS

def get_stock_data(stock_code):
    """1. 从腾讯财经获取基础股票数据"""
    url = f"http://qt.gtimg.cn/q={stock_code}"
    response = requests.get(url)
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

def get_latest_news(stock_name):
    """2. 自动搜索全网关于该股票的最新新闻"""
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
    except Exception as e:
        print("新闻抓取失败:", e)
        return "新闻抓取失败。", "- 暂无新闻数据"

def get_ai_analysis(stock_data, news_text, api_key):
    """3. 调用 OpenRouter (Qwen3) 进行综合分析"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""你是专业的金融分析师。
【今日盘面】股票：{stock_data['name']}，当前价格：{stock_data['current_price']}，今日涨跌幅：{stock_data['change_percent']}%。
【最新资讯】
{news_text}

请结合上述盘面数据和资讯，用一段话（100字左右）客观点评今天的市场情绪或异动原因。
要求：语气沉稳专业，语言精炼，绝对不要给出任何买卖投资建议。"""
    
    payload = {
        "model": "qwen/qwen3-next-80b-a3b-instruct:free",
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        return result['choices'][0]['message']['content']
    except Exception as e:
        print("AI 请求失败:", e)
        return "AI 分析生成失败，可能由于网络波动或免费接口限流。"

def send_to_wechat(webhook_url, stock_data, ai_comment, news_titles_display):
    """4. 发送终极版 Markdown 消息到企业微信"""
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
    response = requests.post(webhook_url, json=payload)
    print(f"[{stock_data['name']}] 微信发送结果:", response.text)

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") 
    
    # 🌟 核心修改点：将单个股票改为股票数组（列表）
    # 格式要求：sz开头为深市，sh开头为沪市，hk开头为港股
    STOCK_CODES = [
        "sz000001",  # 平安银行
        "sh600519",  # 贵州茅台
        "hk00700"    # 腾讯控股
    ]
    
    if not WEBHOOK_URL or not OPENROUTER_KEY:
        print("缺少环境变量配置 (Webhook 或 API Key)！")
    else:
        # 遍历你的股票池
        for index, code in enumerate(STOCK_CODES):
            print(f"\n========== 开始处理第 {index + 1} 只股票: {code} ==========")
            
            data = get_stock_data(code)
            
            if data:
                print(f"拉取 [{data['name']}] 基础数据成功，正在搜索新闻...")
                news_full_text, news_titles = get_latest_news(data['name'])
                
                print(f"新闻抓取完毕，呼叫 AI 分析 [{data['name']}]...")
                ai_comment = get_ai_analysis(data, news_full_text, OPENROUTER_KEY)
                
                print(f"推送到微信...")
                send_to_wechat(WEBHOOK_URL, data, ai_comment, news_titles)
            else:
                print(f"获取股票 {code} 数据失败，请检查代码是否正确。")
            
            # 🌟 核心修改点：如果这不是最后一只股票，就休息 5 秒钟，防止触发防爬虫机制或 API 限流
            if index < len(STOCK_CODES) - 1:
                print("休眠 5 秒，准备处理下一只股票...")
                time.sleep(5)
                
        print("\n🎉 所有股票处理完毕！")
