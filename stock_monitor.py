import warnings
warnings.filterwarnings("ignore")
import requests, os, datetime, time, json, re
from urllib.parse import quote
import pandas as pd
import baostock as bs
from datetime import timezone, timedelta

# ── 基础工具 ──────────────────────────────────────────────
def get_beijing_time():
    return datetime.datetime.now(timezone(timedelta(hours=8)))

def get_current_time():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def to_bs_code(code: str) -> str:
    return f"sh.{code}" if code.startswith(("60", "68")) else f"sz.{code}"

def to_sina_code(code: str) -> str:
    return f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"

# ── 步骤 1：获取全量股票列表 ──────────────────────────────
def get_stock_list(prefix: str) -> list:
    """获取全量股票代码，增加异常兼容性"""
    rs = bs.query_stock_basic(code_name="")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    
    if not rows:
        return []
        
    df = pd.DataFrame(rows, columns=rs.fields)
    
    # 打印列名以便调试（仅在 Actions 日志中可见）
    # print(f"DEBUG: Baostock columns: {df.columns.tolist()}")

    # 兼容性处理：尝试过滤，如果字段不存在则跳过过滤直接取代码
    try:
        if 'type' in df.columns and 'status' in df.columns:
            df = df[(df["type"] == "1") & (df["status"] == "1")]
        elif 'TYPE' in df.columns and 'STATUS' in df.columns:
            df = df[(df["TYPE"] == "1") & (df["STATUS"] == "1")]
    except:
        pass

    # 提取纯数字代码 (例如从 "sh.600000" 提取 "600000")
    if "code" in df.columns:
        codes = df["code"].str.split(".").str[1]
    elif "CODE" in df.columns:
        codes = df["CODE"].str.split(".").str[1]
    else:
        # 最后的保底方案
        codes = df.iloc[:, 0].str.split(".").str[1]
        
    return codes[codes.str.startswith(prefix)].tolist()

# ── 步骤 2：实时行情与资讯获取 ───────────────────────────
def fetch_news(stock_name: str) -> str:
    """搜索该个股最新资讯标题及链接"""
    clean_name = stock_name.replace(" ", "").replace("A", "")
    keyword = quote(clean_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&channel=finance&num=3&format=json"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        news_list = r.get("result", {}).get("list", [])
        if news_list:
            item = news_list[0]
            title = re.sub(r'<[^>]+>', '', item.get('headline', ''))
            return f"{title} (链接: {item.get('url', '')})"
    except:
        pass
    return "暂无实时深度资讯"

def fetch_realtime_sina(code_list: list) -> pd.DataFrame:
    BATCH = 60
    all_rows = []
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    for i in range(0, len(code_list), BATCH):
        batch = code_list[i: i + BATCH]
        sina_param = ",".join(to_sina_code(c) for c in batch)
        url = f"https://hq.sinajs.cn/list={sina_param}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                if "=" not in line or '"' not in line: continue
                raw_code = line.split("hq_str_")[1].split("=")[0].strip()[2:]
                content = line.split('"')[1]
                if not content: continue
                parts = content.split(",")
                yesterday_close = float(parts[2]) if parts[2] else 0.0
                current_price = float(parts[3]) if parts[3] else 0.0
                amount_yuan = float(parts[9]) if parts[9] else 0.0
                pct_chg = round((current_price - yesterday_close) / yesterday_close * 100, 2) if yesterday_close > 0 else 0.0
                if "ST" in parts[0].upper() or "退" in parts[0]: continue
                all_rows.append({"name": parts[0], "code": raw_code, "price": current_price, "pct_chg": pct_chg, "amount": amount_yuan})
        except: continue
        time.sleep(0.4)
    return pd.DataFrame(all_rows)

# ── 步骤 3：核心策略计算逻辑 ─────────────────────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    bj_now = get_beijing_time()
    end_dt = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
    
    try:
        # 1. 获取日线数据（包含PE/PB）
        rs = bs.query_history_k_data_plus(to_bs_code(code), "date,close,high,low,volume,amount,pctChg,turn,peTTM,pbMRQ",
                                          start_date=start_dt, end_date=end_dt, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
        if len(rows) < 65: return False, "", {}
        df = pd.DataFrame(rows, columns=["date","close","high","low","volume","amount","pctChg","turn","peTTM","pbMRQ"])
        df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")

        # 【逻辑1：基本面筛选】 PE 10-30, PB < 5, 市值 > 50亿
        pe, pb = df["peTTM"].iloc[-1], df["pbMRQ"].iloc[-1]
        circ_mv = (df["amount"].iloc[-1] / (df["turn"].iloc[-1] / 100)) if df["turn"].iloc[-1] > 0 else 0
        if not (10 <= pe <= 30) or not (0 < pb <= 5) or circ_mv < 50_0000_0000:
            return False, "", {}

        # 【逻辑2：均线趋势】 股价 > MA10, MA20; MA60 走平或向上
        df["MA10"] = df["close"].rolling(10).mean()
        df["MA20"] = df["close"].rolling(20).mean()
        df["MA60"] = df["close"].rolling(60).mean()
        df["VMA5"] = df["volume"].rolling(5).mean()
        if df["close"].iloc[-1] < df["MA10"].iloc[-1] or df["close"].iloc[-1] < df["MA20"].iloc[-1] or df["MA60"].iloc[-1] < df["MA60"].iloc[-5]:
            return False, "", {}

        # 【逻辑3：KDJ不超买 & MACD不恶化】
        min_9, max_9 = df['low'].rolling(9).min(), df['high'].rolling(9).max()
        rsv = (df['close'] - min_9) / (max_9 - min_9) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        if j.iloc[-1] > 80: return False, "", {} # 排除超买

        exp1, exp2 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
        dif, dea = exp1 - exp2, (exp1 - exp2).ewm(span=9, adjust=False).mean()
        macd_val = 2 * (dif - dea)
        if dif.iloc[-1] < dea.iloc[-1] and macd_val.iloc[-1] < macd_val.iloc[-2]: return False, "", {}

        # 【逻辑4：放量信号】 近3天内有放量阳线
        has_vol = any((df['pctChg'].iloc[i] > 0 and df['volume'].iloc[i] > 1.5 * df['VMA5'].iloc[i]) for i in range(-3, 0))
        if not has_vol: return False, "", {}

        # 【逻辑5：周线共振】
        rs_w = bs.query_history_k_data_plus(to_bs_code(code), "date,close", start_date=(bj_now - datetime.timedelta(days=360)).strftime("%Y-%m-%d"), frequency="w", adjustflag="2")
        ws = []
        while rs_w.error_code == "0" and rs_w.next(): ws.append(rs_w.get_row_data())
        df_w = pd.DataFrame(ws, columns=["date","close"])
        df_w["close"] = pd.to_numeric(df_w["close"])
        dif_w = df_w['close'].ewm(span=12).mean() - df_w['close'].ewm(span=26).mean()
        dea_w = dif_w.ewm(span=9).mean()
        if dif_w.iloc[-1] < dea_w.iloc[-1] and (dif_w.iloc[-1] - dea_w.iloc[-1]) < (dif_w.iloc[-2] - dea_w.iloc[-2]):
            return False, "", {} # 周线不能处于死叉扩大状态

        # 组装数据流给 AI
        news_str = fetch_news(stock_name)
        ai_desc = (f"个股:{stock_name}, 涨幅:{df['pctChg'].iloc[-1]}%, 换手:{round(df['turn'].iloc[-1],2)}%, "
                   f"PE:{round(pe,1)}, 信号:多周期共振突破, 最新资讯:{news_str}")

        return True, "🚀 多周期共振", {"name": stock_name, "code": code, "price": df["close"].iloc[-1], "pct": df["pctChg"].iloc[-1], "news": news_str, "ai_desc": ai_desc}
    except: return False, "", {}

# ── 步骤 4：AI 研判与推送 ────────────────────────────────
def get_ai_commentary(context: str, api_key: str) -> dict:
    if not context: return {}
    prompt = (
        "你是一个专业的首席策略分析师。我会为你提供一组异动个股的量化数据（涨幅、换手率、PE）及相关新闻。\n"
        "请结合这些数据，给出150字左右的深度点评。重点分析：资金介入程度、估值安全性及后续趋势。\n"
        "必须严格返回 JSON 格式，Key 为板块名，内容为点评文字。\n\n" + context
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=50)
        res_text = r.json()["choices"][0]["message"]["content"].strip()
        clean_text = re.sub(r'^```json\s*|```$', '', res_text, flags=re.MULTILINE).strip()
        match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        return json.loads(match.group(0)) if match else {"RAW_ERROR": res_text}
    except: return {"RAW_ERROR": "AI 分析暂时缺席，技术形态符合多周期筛选。"}

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")
    if not WEBHOOK_URL: print("❌ 缺失环境变量"); exit(1)

    bs.login()
    SCAN_POOLS = {"科创/创业板": ["688", "300"], "主板精选": ["60", "00"]}
    collected_data, ai_context = {}, ""
    
    try:
        for pool_name, prefixes in SCAN_POOLS.items():
            triggered = []
            for prefix in prefixes:
                codes = get_stock_list(prefix)
                snapshot = fetch_realtime_sina(codes)
                if snapshot.empty: continue
                # 初筛：成交额 > 1亿
                candidates = snapshot[snapshot["amount"] >= 100000000].sort_values("amount", ascending=False).head(50)
                for _, row in candidates.iterrows():
                    is_hit, tag, data = analyze_stock_strategies(row['code'], row['name'])
                    if is_hit:
                        triggered.append(data)
                    if len(triggered) >= 5: break
            
            if triggered:
                collected_data[pool_name] = triggered
                ai_context += f"\n【{pool_name}板块详情】:\n" + "\n".join([s["ai_desc"] for s in triggered])
    finally:
        bs.logout()

    ai_comments = get_ai_commentary(ai_context, API_KEY)
    msg = f"**🎯 硬核量化监控 (价值+共振版)**\n> 扫描时间：{get_current_time()}\n\n"
    
    if not collected_data:
        msg += "> 💤 今日暂无满足 PE 10-30 & 市值 > 50亿 & 多周期共振的标的。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            sec_comment = ai_comments.get(sec, ai_comments.get("RAW_ERROR", "个股技术形态共振向上。"))
            msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"
            for s in stocks:
                color = "warning" if s["pct"] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | {s['price']} (<font color='{color}'>{s['pct']}%</font>)\n"
                msg += f"  {s['news']}\n"
            msg += "\n---\n"

    requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
    print("✅ 任务完成")
