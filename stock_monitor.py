import requests
import os
import datetime

def get_stock_data(stock_code):
    """从腾讯财经获取股票数据"""
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

def get_ai_analysis(stock_data, api_key):
    """调用 OpenRouter 的 Qwen3 免费模型进行点评"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 设计给 AI 的提示词 (Prompt)
    prompt = f"你是专业的金融分析师。今天 {stock_data['name']} ({stock_data['code']}) 的当前价格是 {stock_data['current_price']}，今日涨跌幅是 {stock_data['change_percent']}%。请用一句话客观点评今天的表现，字数控制在50字以内，语气要沉稳，绝对不要给出投资建议。"
    
    payload = {
        "model": "qwen/qwen3-next-80b-a3b-instruct:free", # 你指定的免费模型
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        return result['choices'][0]['message']['content']
    except Exception as e:
        print("AI 请求失败:", e)
        return "AI 分析加载失败，可能接口暂时拥堵。"

def send_to_wechat(webhook_url, stock_data, ai_comment):
    """发送带 AI 点评的 Markdown 消息到企业微信"""
    color = "warning" if float(stock_data['change_percent']) > 0 else "info"
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 组装包含 AI 点评的全新卡片
    content = f"""**📈 股票价格实时监控 (AI 版)**
> 股票名称：<font color="comment">{stock_data['name']} ({stock_data['code']})</font>
> 当前价格：**{stock_data['current_price']}**
> 今日涨跌：<font color="{color}">{stock_data['change_percent']}%</font>
> 
> 🤖 **AI 简评**：
> <font color="comment">{ai_comment}</font>
> 
> 更新时间：<font color="comment">{current_time}</font>"""

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content}
    }
    response = requests.post(webhook_url, json=payload)
    print("微信发送结果:", response.text)

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") # 读取 AI 密钥
    
    STOCK_CODE = "sz000001" 
    
    if not WEBHOOK_URL:
        print("未找到微信 Webhook 地址！")
    elif not OPENROUTER_KEY:
        print("未找到 OpenRouter API Key！")
    else:
        print("开始拉取股票数据...")
        data = get_stock_data(STOCK_CODE)
        if data:
            print("数据拉取成功，正在呼叫 AI 进行分析...")
            ai_comment = get_ai_analysis(data, OPENROUTER_KEY)
            print("AI 分析完成，准备发送...")
            send_to_wechat(WEBHOOK_URL, data, ai_comment)
        else:
            print("获取股票数据失败")
