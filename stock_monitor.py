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

                all_rows.append({
                    "name": parts[0], "code": pure_code, "price": current_price,
                    "pct_chg": pct_chg, "amount": amount_wan,
                })
            except: continue
        time.sleep(0.5)
    return pd.DataFrame(all_rows)

# ── 步骤 3：量化策略计算 ──────────────────────────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    bj_now = get_beijing_time()
    end_dt = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        rs = bs.query_history_k_data_plus(to_bs_code(code), "date,close,pctChg,turn",
                                          start_date=start_dt, end_date=end_dt, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
        if len(rows) < 30: return False, "", {}
        df = pd.DataFrame(rows, columns=["date", "close", "pctChg", "turn"])
        df[["close", "pctChg", "turn"]] = df[["close", "pctChg", "turn"]].apply(pd.to_numeric, errors="coerce")
        df.dropna(inplace=True)

        # 策略计算
        df["MA5"], df["MA10"] = df["close"].rolling(5).mean(), df["close"].rolling(10).mean()
        exp1, exp2 = df["close"].ewm(span=12).mean(), df["close"].ewm(span=26).mean()
        df["DIF"] = exp1 - exp2
        df["DEA"] = df["DIF"].ewm(span=9).mean()

        cond_ma = (df["MA5"].iloc[-1] > df["MA10"].iloc[-1]) and (df["MA5"].iloc[-1] > df["MA5"].iloc[-2])
        cond_macd = (df["DIF"].iloc[-1] > df["DEA"].iloc[-1]) and (df["DIF"].iloc[-2] <= df["DEA"].iloc[-2])
        cond_turnover = df["turn"].iloc[-1] >= 3.5

        tags = []
        if cond_ma and cond_macd and cond_turnover: tags.append("🚀 右侧突破")
        
        down_days = 0
        for i in range(1, 10):
            if i <= len(df) and df["pctChg"].iloc[-i] < 0: down_days += 1
            else: break
        if 5 <= down_days <= 9: tags.append(f"🩸 连跌{down_days}天")

        if tags:
            return True, " & ".join(tags), {
                "name": stock_name, "code": code, "current_price": round(df["close"].iloc[-1], 2),
                "change_percent": df["pctChg"].iloc[-1], "turnover": round(df["turn"].iloc[-1], 2),
            }
        return False, "", {}
    except: return False, "", {}

# ── 步骤 4：新闻与 AI 研判 ────────────────────────────────
def fetch_news(stock_name: str) -> str:
    keyword = quote(stock_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&range=title&channel=finance&num=1&format=json"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        item = r.get("result", {}).get("list", [])[0]
        return f"> - [{re.sub(r'<[^>]+>', '', item.get('headline', ''))}]({item.get('url', '')})"
    except: return "> - 暂无实时相关资讯"

def get_ai_commentary(context: str, api_key: str) -> dict:
    """调用 AI 总结，若解析失败则保留原始文字"""
    if not context: return {}
    prompt = (
        "你是一个金融分析 API。请分析以下板块个股异动，给出150字深度点评（主升浪或超跌反弹）。\n"
        "必须严格返回 JSON 格式，Key 为板块名，不要输出多余解释。\n\n" + context
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        res_text = r.json()["choices"][0]["message"]["content"].strip()
        
        # 尝试提取并解析 JSON
        clean_text = re.sub(r'^```json\s*|```$', '', res_text, flags=re.MULTILINE).strip()
        match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                return {"RAW_ERROR": match.group(0)} # 解析失败，保留括号内的文字
        return {"RAW_ERROR": res_text} # 连括号都没找着，保留全文
    except Exception as e:
        return {"RAW_ERROR": f"AI 请求异常: {str(e)}"}

# ── 步骤 5：主流程 ────────────────────────────────────────
if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")

    if not (WEBHOOK_URL and API_KEY):
        print("❌ 缺失环境变量"); exit(1)

    SCAN_POOLS = {"科创板": "688", "创业板": "300", "中小板": "002"}
    bs.login()
    
    collected_data, ai_context = {}, ""
    try:
        for pool_name, prefix in SCAN_POOLS.items():
            print(f"🔎 扫描 {pool_name}...")
            codes = get_stock_list(prefix)
            snapshot = fetch_realtime_sina(codes)
            if snapshot.empty: continue
            
            # 初筛前50只高成交额个股
            candidates = snapshot[(snapshot["pct_chg"] > -9.9) & (snapshot["pct_chg"] < 9.9)].sort_values("amount", ascending=False).head(50)
            
            triggered = []
            for _, row in candidates.iterrows():
                is_hit, tag, data = analyze_stock_strategies(row["code"], row["name"])
                if is_hit:
                    data.update({"tag": tag, "news": fetch_news(row["name"])})
                    triggered.append(data)
                if len(triggered) >= 5: break
            
            if triggered:
                collected_data[pool_name] = triggered
                ai_context += f"\n【{pool_name}】: " + ",".join([s["name"] for s in triggered])
    finally:
        bs.logout()

    ai_comments = get_ai_commentary(ai_context, API_KEY)
    msg = f"**🎯 硬核科技量化监控 (新浪版)**\n> 扫描时间：{get_current_time()}\n\n"

    if not collected_data:
        msg += "> 💤 今日暂无个股触发筛选条件。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            # 智能获取点评：优先取板块名，取不到则取原始报错信息，再取不到则兜底
            sec_comment = ai_comments.get(sec)
            if not sec_comment:
                sec_comment = ai_comments.get("RAW_ERROR", "板块情绪博弈中，建议关注核心龙头。")
            
            msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"
            for s in stocks:
                color = "warning" if s["change_percent"] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | {s['current_price']} (<font color='{color}'>{s['change_percent']}%</font>)\n"
                msg += f"  > 💡 信号：**<font color=\"warning\">{s['tag']}</font>**\n  {s['news']}\n"
            msg += "\n---\n"

    requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
    print(f"✅ 任务完成，发送成功")
