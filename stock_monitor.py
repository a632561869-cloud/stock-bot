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

# ── 步骤 1：baostock 获取全量股票代码 ────────────────────
def get_stock_list(prefix: str) -> list:
    rs = bs.query_stock_basic(code_name="")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    # 过滤掉退市股
    df = df[(df["type"] == "1") & (df["status"] == "1")]
    codes = df["code"].str.split(".").str[1]
    return codes[codes.str.startswith(prefix)].tolist()

# ── 步骤 2：新浪接口批量拉取实时快照 ─────────────────────
def fetch_with_retry(url: str, headers: dict, retries: int = 3, delay: int = 3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = "gbk"
            return resp
        except Exception as e:
            print(f"⚠️  第 {attempt + 1}/{retries} 次重试失败: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None

def fetch_realtime_sina(code_list: list) -> pd.DataFrame:
    BATCH = 60
    all_rows = []
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    for i in range(0, len(code_list), BATCH):
        batch = code_list[i: i + BATCH]
        sina_param = ",".join(to_sina_code(c) for c in batch)
        url = f"https://hq.sinajs.cn/list={sina_param}"
        resp = fetch_with_retry(url, headers)
        if resp is None: continue

        for line in resp.text.strip().split("\n"):
            if "=" not in line or '"' not in line: continue
            try:
                raw_code = line.split("hq_str_")[1].split("=")[0].strip()
                pure_code = raw_code[2:]
                content = line.split('"')[1]
                if not content: continue
                parts = content.split(",")
                if len(parts) < 10: continue

                yesterday_close = float(parts[2]) if parts[2] else 0.0
                current_price = float(parts[3]) if parts[3] else 0.0
                amount_yuan = float(parts[9]) if parts[9] else 0.0
                amount_wan = round(amount_yuan / 10000, 2)
                pct_chg = round((current_price - yesterday_close) / yesterday_close * 100, 2) if yesterday_close > 0 else 0.0

                # 基础过滤：过滤掉 ST 股票
                name = parts[0]
                if "ST" in name.upper() or "退" in name: continue

                all_rows.append({
                    "name": name, "code": pure_code, "price": current_price,
                    "pct_chg": pct_chg, "amount": amount_wan, "amount_yuan": amount_yuan
                })
            except: continue
        time.sleep(0.5)
    return pd.DataFrame(all_rows)

# ── 步骤 3：多维度量化指标漏斗筛选 ────────────────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    bj_now = get_beijing_time()
    end_dt = bj_now.strftime("%Y-%m-%d")
    # 拉长日线到150天，保证MA60能取到之前的值
    start_dt = (bj_now - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
    
    try:
        # 第一层漏斗：日线数据、估值、市值排雷
        rs = bs.query_history_k_data_plus(to_bs_code(code),
             "date,open,high,low,close,volume,amount,pctChg,turn,peTTM,pbMRQ",
             start_date=start_dt, end_date=end_dt, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
        if len(rows) < 65: return False, "", {} # 新股或复牌不久不碰
        
        df = pd.DataFrame(rows, columns=["date","open","high","low","close","volume","amount","pctChg","turn","peTTM","pbMRQ"])
        df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
        df.dropna(inplace=True)

        # 1. 估值过滤: 10<=PE<=30, 0<PB<=5
        pe = df["peTTM"].iloc[-1]
        pb = df["pbMRQ"].iloc[-1]
        if not (10 <= pe <= 30) or not (0 < pb <= 5):
            return False, "", {}

        # 2. 市值过滤: 近似流通市值计算 >= 50亿
        turnover = df["turn"].iloc[-1]
        amt = df["amount"].iloc[-1]
        if turnover > 0:
            circ_mv_yuan = amt / (turnover / 100)
            if circ_mv_yuan < 50_0000_0000: # 50亿人民币
                return False, "", {}

        # 3. 均线系统判定
        df["MA10"] = df["close"].rolling(10).mean()
        df["MA20"] = df["close"].rolling(20).mean()
        df["MA60"] = df["close"].rolling(60).mean()
        df["VMA5"] = df["volume"].rolling(5).mean()

        close_p = df["close"].iloc[-1]
        # 股价站上10日、20日均线
        if close_p < df["MA10"].iloc[-1] or close_p < df["MA20"].iloc[-1]: return False, "", {}
        # 60日均线走平或向上 (对比5个交易日前)
        if df["MA60"].iloc[-1] < df["MA60"].iloc[-5]: return False, "", {}

        # 4. KDJ 不超买 (J < 80)
        min_9 = df['low'].rolling(window=9, min_periods=9).min()
        max_9 = df['high'].rolling(window=9, min_periods=9).max()
        df['RSV'] = (df['close'] - min_9) / (max_9 - min_9) * 100
        df['K'] = df['RSV'].ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        if df["J"].iloc[-1] > 80: return False, "", {}

        # 5. MACD 不恶化 (过滤死叉且绿柱放大)
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp1 - exp2
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])
        
        # 拒绝：DIF 在 DEA 下方且绿柱变长
        if df['DIF'].iloc[-1] < df['DEA'].iloc[-1] and df['MACD'].iloc[-1] <= df['MACD'].iloc[-2]:
            return False, "", {}

        # 6. 量价形态: 近3天内有放量阳线 (涨幅>0 且 成交量>前5日均量1.5倍)
        has_breakout = False
        for i in range(-3, 0):
            if df['pctChg'].iloc[i] > 0 and df['volume'].iloc[i] > 1.5 * df['VMA5'].iloc[i]:
                has_breakout = True
                break
        if not has_breakout: return False, "", {}

        # 第二层漏斗：周线级别共振（日线过关才查周线，节省请求）
        start_dt_w = (bj_now - datetime.timedelta(days=400)).strftime("%Y-%m-%d")
        rs_w = bs.query_history_k_data_plus(to_bs_code(code), "date,close", 
                                            start_date=start_dt_w, end_date=end_dt, frequency="w", adjustflag="2")
        rows_w = []
        while rs_w.error_code == "0" and rs_w.next(): rows_w.append(rs_w.get_row_data())
        if len(rows_w) < 26: return False, "", {}
        
        df_w = pd.DataFrame(rows_w, columns=["date", "close"])
        df_w["close"] = pd.to_numeric(df_w["close"])
        
        exp1_w = df_w['close'].ewm(span=12, adjust=False).mean()
        exp2_w = df_w['close'].ewm(span=26, adjust=False).mean()
        df_w['DIF_W'] = exp1_w - exp2_w
        df_w['DEA_W'] = df_w['DIF_W'].ewm(span=9, adjust=False).mean()
        df_w['MACD_W'] = 2 * (df_w['DIF_W'] - df_w['DEA_W'])
        
        # 周线金叉或即将金叉 (不接受周线明确死叉向下)
        if df_w['DIF_W'].iloc[-1] < df_w['DEA_W'].iloc[-1] and df_w['MACD_W'].iloc[-1] < df_w['MACD_W'].iloc[-2]:
            return False, "", {}

        tags = ["🎯 估值安全", "📈 多周期共振", f"PE:{round(pe,1)}"]
        if df_w['DIF_W'].iloc[-1] > df_w['DEA_W'].iloc[-1]: tags.append("🟢 周线金叉")
        
        return True, " | ".join(tags), {
            "name": stock_name, "code": code, "current_price": round(df["close"].iloc[-1], 2),
            "change_percent": df["pctChg"].iloc[-1], "turnover": round(df["turn"].iloc[-1], 2),
        }

    except Exception as e:
        print(f"Error processing {code}: {e}")
        return False, "", {}

# ── 步骤 4：新闻与 AI 研判 (原封不动保留) ─────────────────
def fetch_news(stock_name: str) -> str:
    keyword = quote(stock_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&range=title&channel=finance&num=1&format=json"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        item = r.get("result", {}).get("list", [])[0]
        return f"> - [{re.sub(r'<[^>]+>', '', item.get('headline', ''))}]({item.get('url', '')})"
    except: return "> - 暂无实时相关资讯"

def get_ai_commentary(context: str, api_key: str) -> dict:
    if not context: return {}
    prompt = (
        "你是一个金融分析 API。请结合资金面和基本面，分析以下板块个股异动，给出150字深度点评。\n"
        "必须严格返回 JSON 格式，Key 为板块名，不要输出多余解释。\n\n" + context
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        res_text = r.json()["choices"][0]["message"]["content"].strip()
        clean_text = re.sub(r'^```json\s*|```$', '', res_text, flags=re.MULTILINE).strip()
        match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if match:
            try: return json.loads(match.group(0))
            except: return {"RAW_ERROR": match.group(0)}
        return {"RAW_ERROR": res_text}
    except Exception as e:
        return {"RAW_ERROR": f"AI 请求异常: {str(e)}"}

# ── 步骤 5：主流程 ────────────────────────────────────────
if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")

    if not (WEBHOOK_URL and API_KEY):
        print("❌ 缺失环境变量"); exit(1)

    SCAN_POOLS = {"科创板": "688", "创业板": "300", "中小板": "002", "深主板": "000"} 
    bs.login()
    
    collected_data, ai_context = {}, ""
    try:
        for pool_name, prefix in SCAN_POOLS.items():
            print(f"🔎 扫描 {pool_name}...")
            codes = get_stock_list(prefix)
            snapshot = fetch_realtime_sina(codes)
            if snapshot.empty: continue
            
            # 【核心过滤第一关】剔除僵尸股：单日成交额大于 1 亿人民币 (10000万)
            # 并且剔除当日跌停股，保留有活力的标的
            candidates = snapshot[
                (snapshot["amount"] >= 10000) & 
                (snapshot["pct_chg"] > -9.0)
            ].sort_values("amount", ascending=False).head(100) # 取成交额前100进行深度检查
            
            triggered = []
            for _, row in candidates.iterrows():
                is_hit, tag, data = analyze_stock_strategies(row["code"], row["name"])
                if is_hit:
                    data.update({"tag": tag, "news": fetch_news(row["name"])})
                    triggered.append(data)
                # 每个板块最多推送 5 只满足极其严苛条件的票
                if len(triggered) >= 5: break
            
            if triggered:
                collected_data[pool_name] = triggered
                ai_context += f"\n【{pool_name}】: " + ",".join([s["name"] for s in triggered])
    finally:
        bs.logout()

    ai_comments = get_ai_commentary(ai_context, API_KEY)
    msg = f"**🎯 硬核科技量化监控 (共振过滤版)**\n> 扫描时间：{get_current_time()}\n\n"

    if not collected_data:
        msg += "> 💤 今日暂无个股满足：估值优+多周期共振+放量 的严苛条件。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            sec_comment = ai_comments.get(sec)
            if not sec_comment: sec_comment = ai_comments.get("RAW_ERROR", "该板块资金异动明显，技术面呈现多周期共振向上，具备估值安全垫。")
            
            msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"
            for s in stocks:
                color = "warning" if s["change_percent"] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | 价格: {s['current_price']} (<font color='{color}'>{s['change_percent']}%</font>)\n"
                msg += f"  > 💡 信号：**<font color=\"warning\">{s['tag']}</font>**\n  {s['news']}\n"
            msg += "\n---\n"

    requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
    print("✅ 任务完成，发送成功")
