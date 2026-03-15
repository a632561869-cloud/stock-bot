def get_ai_analysis(stock_data, news_text, api_key):
    """3. 调用 OpenRouter (Qwen3) 进行综合分析 (增强报错版)"""
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
        
        # 🌟 核心拦截机制：如果 HTTP 状态码不是 200 (成功)，直接打印官方报错并终止解析
        if response.status_code != 200:
            error_msg = response.text
            print(f"❌ OpenRouter API 拒绝了请求！状态码: {response.status_code}")
            print(f"❌ 官方详细报错: {error_msg}")
            return f"AI 接口报错 ({response.status_code})，请查看 GitHub 运行日志。"
            
        result = response.json()
        
        # 🌟 安全解析机制：先检查数据结构对不对，再提取
        if 'choices' in result and len(result['choices']) > 0:
            return result['choices'][0]['message']['content']
        elif 'error' in result:
            print(f"❌ OpenRouter 返回了错误 JSON: {result['error']}")
            return f"AI 返回错误: {result['error'].get('message', '未知')}"
        else:
            print(f"❌ 无法识别的返回格式: {result}")
            return "AI 返回了无法解析的数据。"
            
    except Exception as e:
        # 这里捕获的是真正的网络断开或代码语法崩溃
        print(f"❌ Python 代码执行出现严重异常: {e}")
        return f"系统级异常: {str(e)}"
