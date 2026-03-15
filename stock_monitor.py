import warnings
# 🤫 暴力魔法：屏蔽全局所有烦人的黄色警告
warnings.filterwarnings("ignore")

import requests
import os
import datetime
import time
import json
import re  # 新增：用于强大的正则表达式提取
from duckduckgo_search import DDGS

def get_current_time():
    utc_now = datetime.datetime.utcnow()
    beijing_now = utc_now + datetime.timedelta(hours=8)
    return beijing_now.strftime("%Y-%m-%d %H:%M:%S")

def get_stock_data(stock_code):
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
            
            news_list.append(f"标题：{title} \n摘要：{body}")
            if url:
                titles_for_display.append(f"> - [{title}]({url})")
            else:
                titles_for_display.append(f"> - {title}")
            
        return "\n".join(news_list), "\n".join(titles_for_display)
    except Exception as e:
        # 🔍 透视眼：打印新闻失败的真正原因
        print(f"❌ 抓取 [{stock_name}] 新闻被拦截或报错: {e}")
        return "新闻抓取失败。", "> - 暂无新闻数据"

def get_batch_ai_analysis(all_stocks_context, api_key):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""你是专业的金融分析师。请结合以下多只股票的盘面数据和资讯，为每只股票写一段100字左右的客观点评。

【极其重要的格式指令】
你必须且只能返回一个合法的 JSON 对象，绝对不要包含任何 Markdown 标记，不要解释！
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
            
            # 🔍 透视眼：看看 AI 到底回复了什么鬼东西
            print(f"💡 AI 原始回复内容:\n{content}\n")
            
            # 🛡️ 强力装甲：使用正则表达式强行抠出 JSON 字典
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception as e:
                    print(f"❌ 正则提取到了字典，但 JSON 依然无法解析: {e}")
                    return {}
            else:
                print("❌ AI 的回复中根本找不到 JSON 结构！")
                return {}
        else:
            print(f"❌ AI 接口再次拒绝请求！状态码: {response.status_code}")
            print(f"❌ 官方报错信息: {response.text}")
            return {}
    except Exception as e:
        print(f"❌ AI 网络请求彻底崩溃: {e}")
        return {}

def send_combined_to_wechat(webhook_url, collected_data, ai_comments_dict):
    current_time = get_current_time()
    
    content = f"**📈 股票深度监控报告**\n> 更新时间：<font color=\"comment\">{current_time}</font>\n\n"
    
    for index, (code, info) in enumerate(collected_data.items()):
        stock_data = info['data']
        news_titles_display = info['news_titles']
        ai_comment = ai_comments_dict.get(code, "暂无分析数据。")
        
        color = "warning" if float(stock_data['change_percent']) > 0 else "info"
        
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
        print(f"❌ 推送微信失败: {e}")

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
                
                time.sleep(2) 
                
        if collected_data:
            ai_comments_dict = get_batch_ai_analysis(all_stocks_context, OPENROUTER_KEY)
            
            print("📤 准备发送微信整合卡片...")
            send_combined_to_wechat(WEBHOOK_URL, collected_data, ai_comments_dict)
            
        print("🎉 执行完毕！")
    else:
        print("❌ 缺少环境变量。")
