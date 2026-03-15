import requests
import os
import datetime
import time
import json
from ddgs import DDGS

def get_current_time():
    """获取标准时间 (UTC+8)"""
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
    """搜索最新新闻，并提取超链接"""
    try:
        results = DDGS().text(f"{stock_name} 股票 财经 最新消息", max_results=3)
        if not results:
            return "近期暂无重大新闻。", "> - 暂无新闻数据"
        
        news_list = []
        titles_for_display = []
        for r in results:
            title = r.get('title', '未知标题')
            body = r.get('body', '')
            url = r.get('href', '')
            
            # 喂给 AI 的纯文本格式
            news_list.append(f"标题：{title} \n摘要：{body}")
            
            # 🌟 核心修改：生成 Markdown 可点击链接格式 [标题](链接)
            if url:
                titles_for_display.append(f"> - [{title}]({url})")
            else:
                titles_for_display.append(f"> - {title}")
            
        return "\n".join(news_list), "\n".join(titles_for_display)
    except Exception as e:
        print(f"新闻抓取异常: {e}")
        return "新闻抓取失败。", "> - 暂无新闻数据"

def get_batch_ai_analysis(all_stocks_context, api_key):
    """一次性请求 AI，要求返回 JSON"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""你是专业的金融分析师。请结合以下多只股票的盘面数据和资讯，为每只股票写一段100字左右的客观点评（语气沉稳，无投资建议）。

【极其重要的格式指令】
你必须且只能返回一个合法的 JSON 对象，绝对不要包含任何 Markdown 标记，不要包含任何额外的解释文本。
返回格式示例：
{{
    "sz000001": "平安银行今日走势...",
    "sh600519": "贵州茅台受新闻影响..."
}}

【今日股票数据与新闻】
{all_stocks_context}
"""
    
    payload = {
        "model": "qwen/qwen3-next-80b-a3b-instruct:free", 
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        print("🧠 正在请求 AI 深度分析...")
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content) 
        else:
            return {}
    except:
        return {}

def send_combined_to_wechat(webhook_url, collected_data, ai_comments_dict):
    """将所有股票拼接成一个长卡片发送"""
    current_time = get_current_time()
    
    # 🌟 卡片头部
    content = f"**📈 股票深度监控报告**\n> 更新时间：<font color=\"comment\">{current_time}</font>\n\n"
    
    # 🌟 循环遍历每只股票，拼接到同一个字符串中
    for index, (code, info) in enumerate(collected_data.items()):
        stock_data = info['data']
        news_titles_display = info['news_titles']
        ai_comment = ai_comments_dict.get(code, "暂无分析数据。")
        
        color = "warning" if float(stock_data['change_percent']) > 0 else "info"
        
        # 组装单只股票的区块
        content += f"""**{index + 1}. {stock_data['name']} ({stock_data['code']})**
> 当前价格：**{stock_data['current_price']}** | 今日涨跌：<font color="{color}">{stock_data['change_percent']}%</font>
> 📰 **最新资讯**：
{news_titles_display}
> 🤖 **深度点评**：
> <font color="info">{ai_comment}</font>

"""
    
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content.strip()}
    }
    
    try:
        requests.post(webhook_url, json=payload, timeout=10)
        print("✅ 整合卡片推送成功！")
    except Exception as e:
        print(f"❌ 推送失败: {e}")

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") 
    
    STOCK_CODES = [
        "sz000001",  # 平安银行
        "sh600519",  # 贵州茅台
        "hk00700"    # 腾讯控股
    ]
    
    if WEBHOOK_URL and OPENROUTER_KEY:
        print("🔄 开始拉取数据...")
        collected_data = {}
        all_stocks_context = "" 
        
        for code in STOCK_CODES:
            data = get_stock_data(code)
            if data:
                news_full, news_titles = get_latest_news(data['name'])
                collected_data[code] = {
                    "data": data,
                    "news_titles": news_titles
                }
                
                all_stocks_context += f"\n--- 股票：{data['name']} ({code}) ---\n"
                all_stocks_context += f"当前价：{data['current_price']}，涨跌幅：{data['change_percent']}%\n"
                all_stocks_context += f"相关新闻：\n{news_full}\n"
                
                # 保持原有的休眠时间，防止搜新闻被屏蔽
                time.sleep(2) 
                
        if collected_data:
            ai_comments_dict = get_batch_ai_analysis(all_stocks_context, OPENROUTER_KEY)
            
            print("📤 准备发送微信整合卡片...")
            send_combined_to_wechat(WEBHOOK_URL, collected_data, ai_comments_dict)
            
        print("🎉 执行完毕！")
    else:
        print("❌ 缺少环境变量。")
