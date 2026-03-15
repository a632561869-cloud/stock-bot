import requests
import os
import datetime
import time
import json
from duckduckgo_search import DDGS

def get_beijing_time():
    """获取标准的北京时间 (UTC+8)"""
    utc_now = datetime.datetime.utcnow()
    beijing_now = utc_now + datetime.timedelta(hours=8)
    return beijing_now.strftime("%Y-%m-%d %H:%M:%S")

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

def get_batch_ai_analysis(all_stocks_context, api_key):
    """核心升级：一次性把所有数据丢给 AI，要求返回 JSON 格式"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 精心设计的批量处理提示词
    prompt = f"""你是专业的金融分析师。请结合以下多只股票的盘面数据和资讯，为每只股票写一段100字左右的客观点评（语气沉稳，无投资建议）。

【极其重要的格式指令】
你必须且只能返回一个合法的 JSON 对象，绝对不要包含任何 Markdown 标记（如 ```json），不要包含任何额外的解释文本。
返回格式示例：
{{
    "sz000001": "平安银行今日走势...",
    "sh600519": "贵州茅台受新闻影响..."
}}

【今日股票数据与新闻】
{all_stocks_context}
"""
    
    payload = {
        "model": "qwen/qwen3-next-80b-a3b-instruct:free", # 沿用你指定的千问最新免费模型
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        print("🧠 正在向 Qwen3 发送单次大批量请求...")
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            # 清理 AI 可能不听话带上的 markdown 标记
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content) # 将字符串转化为 Python 字典
        else:
            print(f"❌ AI 接口异常，状态码: {response.status_code}")
            return {}
    except Exception as e:
        print(f"❌ 解析 AI 返回的 JSON 失败: {e}")
        return {}

def send_to_wechat(webhook_url, stock_data, ai_comment, news_titles_display):
    """发送单张 Markdown 卡片到企业微信"""
    color = "warning" if float(stock_data['change_percent']) > 0 else "info"
    current_time = get_beijing_time() # 使用北京时间
    
    content = f"""**📈 股票深度监控 (AI 驱动版)**
> 股票名称：<font color="comment">{stock_data['name']} ({stock_data['code']})</font>
> 当前价格：**{stock_data['current_price']}**
> 今日涨跌：<font color="{color}">{stock_data['change_percent']}%</font>
> 
> 📰 **最新资讯速览**：
> <font color="comment">{news_titles_display}</font>
> 
> 🤖 **Qwen3 综合点评**：
> <font color="info">{ai_comment}</font>
> 
> 更新时间：<font color="comment">{current_time} (北京时间)</font>"""

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content}
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
        print(f"✅ [{stock_data['name']}] 卡片推送成功！")
    except:
        print(f"❌ [{stock_data['name']}] 推送失败。")

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") 
    
    STOCK_CODES = [
        "sz000001",  # 平安银行
        "sh600519",  # 贵州茅台
        "hk00700"    # 腾讯控股
    ]
    
    if WEBHOOK_URL and OPENROUTER_KEY:
        # 第一阶段：收集所有股票的数据和新闻（不请求 AI）
        print("🔄 [阶段一] 开始拉取所有股票数据和新闻...")
        collected_data = {}
        all_stocks_context = "" # 用于拼接给 AI 的长文本
        
        for code in STOCK_CODES:
            data = get_stock_data(code)
            if data:
                news_full, news_titles = get_latest_news(data['name'])
                # 保存结构化数据以便最后发送卡片
                collected_data[code] = {
                    "data": data,
                    "news_titles": news_titles
                }
                # 拼接给 AI 看的文本
                all_stocks_context += f"\n--- 股票：{data['name']} ({code}) ---\n"
                all_stocks_context += f"当前价：{data['current_price']}，涨跌幅：{data['change_percent']}%\n"
                all_stocks_context += f"相关新闻：\n{news_full}\n"
                
                time.sleep(2) # 仅暂停2秒，防止 DuckDuckGo 搜索限流
                
        # 第二阶段：一次性发送给 Qwen3
        if collected_data:
            print("\n🚀 [阶段二] 数据收集完毕，呼叫 Qwen3 进行全局分析...")
            ai_comments_dict = get_batch_ai_analysis(all_stocks_context, OPENROUTER_KEY)
            
            # 第三阶段：分发结果并独立推送卡片
            print("\n📤 [阶段三] 解析完毕，开始分发微信卡片...")
            for code, info in collected_data.items():
                # 从字典中提取对应代码的点评，如果 AI 漏掉了，就给个默认提示
                comment = ai_comments_dict.get(code, "AI 暂时未能生成针对该股票的点评。")
                send_to_wechat(WEBHOOK_URL, info['data'], comment, info['news_titles'])
                
            print("\n🎉 全部流程执行完毕！")
