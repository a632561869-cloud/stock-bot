import warnings
warnings.filterwarnings("ignore")
import requests, os, datetime, time, json, re
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
    rs = bs.query_stock_basic(code_name="")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    df = df[(df["type"] == "1") & (df["status"] == "1")]
    codes = df["code"].str.split(".").str[1]
    return codes[codes.str.startswith(prefix)].tolist()

# ── 步骤 2：实时行情获取 ───────────────────────────
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

# ── 步骤 3：核心策略计算逻辑 (指标深度提取) ─────────────────────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    bj_now = get_beijing_time()
    end_dt = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
    
    try:
        rs = bs.query_history_k_data_plus(to_bs_code(code), "date,close,high,low,volume,amount,pctChg,turn,peTTM,pbMRQ",
                                          start_date=start_dt, end_date=end_dt, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
        if len(rows) < 65: return False, "", {}
        df = pd.DataFrame(rows, columns=["date","close","high","low","volume","amount","pctChg","turn","peTTM","pbMRQ"])
        df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")

        # 1. 基本面硬过滤
        pe, pb = df["peTTM"].iloc[-1], df["pbMRQ"].iloc[-1]
        circ_mv = (df["amount"].iloc[-1] / (df["turn"].iloc[-1] / 100)) if df["turn"].iloc[-1] > 0 else 0
        if not (10 <= pe <= 30) or not (0 < pb <= 6) or circ_mv < 50_0000_0000:
            return False, "", {}

        # 2. 技术细节提取
        df["MA10"] = df["close"].rolling(10).mean()
        df["MA20"] = df["close"].rolling(20).mean()
        df["MA60"] = df["close"].rolling(60).mean()
        slope_60 = round(df["MA60"].iloc[-1] / df["MA60"].iloc[-6], 4)
        dist_ma20 = round((df["close"].iloc[-1] / df["MA20"].iloc[-1] - 1) * 100, 2)

        # KDJ & MACD
        min_9, max_9 = df['low'].rolling(9).min(), df['high'].rolling(9).max()
        rsv = (df['close'] - min_9) / (max_9 - min_9) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        exp1, exp2 = df['close'].ewm(span=12, adjust=False).mean(), df['close'].ewm(span=26, adjust=False).mean()
        dif, dea = exp1 - exp2, (exp1 - exp2).ewm(span=9, adjust=False).mean()
        macd_hist = 2 * (dif - dea)
        
        # 量比
        vma5 = df["volume"].rolling(5).mean().iloc[-1]
        vol_ratio = round(df["volume"].iloc[-1] / vma5, 2) if vma5 > 0 else 0

        # 3. 信号判定逻辑
        if df["close"].iloc[-1] < df["MA10"].iloc[-1] or slope_60 < 0.995:
            return False, "", {}
        if j.iloc[-1] > 90: return False, "", {}

        # 🚀 深度喂料包 (发送给 AI)
        ai_payload = (
            f"股票:{stock_name}, 涨幅:{df['pctChg'].iloc[-1]}%, 量比:{vol_ratio}, 换手:{round(df['turn'].iloc[-1],2)}%, "
            f"PE:{round(pe,1)}, MA60斜率:{slope_60}, 20日乖离率:{dist_ma20}%, MACD:{'动能增强' if macd_hist.iloc[-1]>macd_hist.iloc[-2] else '动能减弱'}"
        )

        # 💡 原始信号详情栏 (展示在消息中)
        detail_tag = f"💡 信号: 多周期共振突破 | 换手: {round(df['turn'].iloc[-1],2)}% | PE: {round(pe,1)}"

        return True, detail_tag, {"name": stock_name, "code": code, "price": df["close"].iloc[-1], "pct": df["pctChg"].iloc[-1], "tag": detail_tag, "ai_desc": ai_payload}
    except: return False, "", {}

# ── 步骤 4：AI 研判与推送 ────────────────────────────────
def get_ai_commentary(context: str, api_key: str) -> dict:
    if not context: return {}
    prompt = (
        "你是一个专业的私募基金首席策略师。我会为你提供一组个股的深度量化指标包。\n"
        "请结合量比、20日乖离率、MA60斜率判断趋势可持续性及短线风险。\n"
        "点评要求：专业、干练、多用数据说话，120字左右。必须严格返回 JSON 格式，Key 为板块名。\n\n" + context
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=50)
        res_text = r.json()["choices"][0]["message"]["content"].strip()
        clean_text = re.sub(r'^```json\s*|```$', '', res_text, flags=re.MULTILINE).strip()
        match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        return json.loads(match.group(0)) if match else {"RAW_ERROR": res_text}
    except: return {"RAW_ERROR": "AI 接口连接超时，技术形态维持多周期看多信号。"}

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
                # 筛选条件：成交额 > 1亿 且 涨幅 > 0%
                candidates = snapshot[(snapshot["amount"] >= 100000000) & (snapshot["pct_chg"] > 0)].sort_values("amount", ascending=False).head(40)
                for _, row in candidates.iterrows():
                    is_hit, tag, data = analyze_stock_strategies(row['code'], row['name'])
                    if is_hit:
                        triggered.append(data)
                    if len(triggered) >= 5: break
            
            if triggered:
                collected_data[pool_name] = triggered
                ai_context += f"\n【{pool_name}板块指标包】:\n" + "\n".join([s["ai_desc"] for s in triggered])
    finally:
        bs.logout()

    ai_comments = get_ai_commentary(ai_context, API_KEY)
    msg = f"**🎯 硬核量化深度监控 (AI点评版)**\n> 扫描时间：{get_current_time()}\n\n"
    
    if not collected_data:
        msg += "> 💤 今日暂无满足量化共振且估值合理的标的。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            sec_comment = ai_comments.get(sec, ai_comments.get("RAW_ERROR", "个股技术形态共振向上。"))
            msg += f"> 🤖 **AI 深度研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"
            for s in stocks:
                color = "warning" if s["pct"] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | {s['price']} (<font color='{color}'>{s['pct']}%</font>)\n"
                msg += f"  <font color=\"comment\">{s['tag']}</font>\n"
            msg += "\n---\n"

    requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
    print("✅ 任务完成")
