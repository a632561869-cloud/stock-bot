import warnings
warnings.filterwarnings("ignore")
import requests, os, datetime, time, json, re
from urllib.parse import quote
import pandas as pd
import akshare as ak
from datetime import timezone, timedelta

# --- 基础配置与时区处理 ---
def get_beijing_time():
    return datetime.datetime.now(timezone(timedelta(hours=8)))

def get_current_time():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def analyze_stock_strategies(symbol, stock_name):
    """
    量化策略引擎：扫描个股是否符合战法
    """
    bj_now = get_beijing_time()
    start_date = (bj_now - datetime.timedelta(days=60)).strftime("%Y%m%d")
    try:
        # 获取 K 线数据
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, adjust="qfq")
        if df is None or len(df) < 30: return False, "", {}
        
        current_price = df['收盘'].iloc[-1]
        change_pct = df['涨跌幅'].iloc[-1]

        # --- 策略 A: 右侧多头突破 ---
        # 均线
        df['MA5'] = df['收盘'].rolling(window=5).mean()
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        # MACD 计算
        exp1 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp2 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['MACD_DIF'] = exp1 - exp2
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()
        
        cond_ma = (df['MA5'].iloc[-1] > df['MA10'].iloc[-1]) and (df['MA5'].iloc[-1] > df['MA5'].iloc[-2])
        cond_macd = (df['MACD_DIF'].iloc[-1] > df['MACD_DEA'].iloc[-1]) and (df['MACD_DIF'].iloc[-2] <= df['MACD_DEA'].iloc[-2])
        cond_turnover = df['换手率'].iloc[-1] >= 3.5 # 换手率门槛

        # --- 策略 B: 左侧连续超跌 ---
        down_days = 0
        for i in range(1, 10):
            if i <= len(df) and df['涨跌幅'].iloc[-i] < 0:
                down_days += 1
            else:
                break

        tags = []
        if cond_ma and cond_macd and cond_turnover: 
            tags.append("🚀 右侧突破")
        if 5 <= down_days <= 9: 
            tags.append(f"🩸 连跌{down_days}天")

        if tags:
            return True, " & ".join(tags), {
                "name": stock_name, 
                "code": symbol, 
                "current_price": round(current_price, 2), 
                "change_percent": change_pct
            }
        return False, "", {}
    except:
        return False, "", {}

def fetch_news(stock_name):
    """获取最新的一条相关新闻"""
    keyword = quote(stock_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&range=title&channel=finance&num=1&format=json"
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=5).json()
        item = r.get("result", {}).get("list", [])[0]
        title = re.sub(r'<[^>]+>', '', item.get("headline", ""))
        link = item.get("url", "")
        return f"> - [{title}]({link})"
    except:
        return "> - 暂无实时相关资讯"

def get_ai_commentary(context, api_key):
    """调用 AI 总结板块整体情绪"""
    if not context: return {}
    prompt = f"你是硬核科技金融分析师。请对以下板块今日异动个股做150字左右的深度点评，分析板块是处于主升浪还是超跌反弹。请严格返回JSON格式，Key为板块名：\n\n{context}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "openrouter/free", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        res_text = r.json()['choices'][0]['message']['content']
        match = re.search(r'\{.*\}', res_text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}
    except:
        return {}

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")
    
    if not WEBHOOK_URL or not API_KEY:
        print("❌ 环境变量未配置")
        exit(1)

    # 1. 行业板块名称 (匹配东财标准)
    TARGET_SECTORS = ["半导体", "人工智能", "机器人"]
    # 2. 市场池分类 (68开头科创板, 30开头创业板)
    MARKET_POOLS = {"科创板": "68", "创业板": "30"}
    
    collected_data = {}
    ai_context = ""

    # 获取一份当前全市场快照，用于科创/创业板筛选
    try:
        all_spot = ak.stock_zh_a_spot_em()
    except:
        all_spot = pd.DataFrame()

    # 遍历所有目标监控池
    for name in TARGET_SECTORS + list(MARKET_POOLS.keys()):
        print(f"🔎 正在扫描: {name}...")
        
        # 获取基础股票列表
        if name in TARGET_SECTORS:
            try:
                stocks = ak.stock_board_industry_cons_em(symbol=name)[['代码', '名称']].values.tolist()
            except: continue
        else:
            if all_spot.empty: continue
            # 筛选对应代码开头，并按涨跌幅排个序，取前100只活跃的扫，避免超时
            prefix = MARKET_POOLS[name]
            mask = all_spot['代码'].str.startswith(prefix)
            stocks = all_spot[mask].sort_values(by="成交额", ascending=False).head(100)[['代码', '名称']].values.tolist()

        triggered_list = []
        for code, s_name in stocks:
            is_hit, tag, data = analyze_stock_strategies(code, s_name)
            if is_hit:
                data['tag'] = tag
                data['news'] = fetch_news(s_name)
                triggered_list.append(data)
            
            # 每个板块达到5个就停止扫描，提高效率
            if len(triggered_list) >= 5:
                break
            # 适当微调爬虫间隔
            time.sleep(0.2)

        if triggered_list:
            collected_data[name] = triggered_list
            ai_context += f"\n【{name}】: " + ",".join([s['name'] for s in triggered_list])

    # 3. AI 研判与消息封装
    ai_comments = get_ai_commentary(ai_context, API_KEY)
    
    msg = f"**🎯 硬核科技量化监控 (动态版)**\n> 扫描时间：{get_current_time()}\n\n"
    
    if not collected_data:
        msg += "> 💤 今日暂无个股触发筛选条件。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            sec_comment = ai_comments.get(sec, "板块情绪博弈中，建议关注核心龙头。")
            msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"
            
            for s in stocks:
                color = "warning" if s['change_percent'] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | 现价 {s['current_price']} (<font color='{color}'>{s['change_percent']}%</font>)\n"
                msg += f"  > 💡 战法信号：**<font color=\"warning\">{s['tag']}</font>**\n"
                msg += f"  {s['news']}\n"
            msg += "\n---\n"

    # 4. 发送
    requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
    print(f"✅ 处理完成，扫描到 {len(collected_data)} 个板块有信号")
