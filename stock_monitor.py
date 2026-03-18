import warnings
warnings.filterwarnings("ignore")
import requests, os, datetime, time, re
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
    print(f"🔍 [Baostock] 正在获取前缀 '{prefix}' 的股票基础列表...")
    rs = bs.query_stock_basic(code_name="")
    if rs.error_code != "0":
        print(f"❌ [Baostock] 获取基础列表失败, 错误码: {rs.error_code}, 错误信息: {rs.error_msg}")
        return []
        
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    df = df[(df["type"] == "1") & (df["status"] == "1")]
    codes = df["code"].str.split(".").str[1]
    result_list = codes[codes.str.startswith(prefix)].tolist()
    print(f"✅ [Baostock] 前缀 '{prefix}' 获取成功，共计 {len(result_list)} 只标的。")
    return result_list

# ── 步骤 2：实时行情获取 ───────────────────────────
def fetch_realtime_sina(code_list: list) -> pd.DataFrame:
    total_codes = len(code_list)
    print(f"📡 [Sina] 准备通过新浪接口获取 {total_codes} 只股票的实时行情...")
    BATCH = 60
    all_rows = []
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    
    for i in range(0, total_codes, BATCH):
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
                
                # 过滤 ST 和退市股
                if "ST" in parts[0].upper() or "退" in parts[0]: continue
                all_rows.append({"name": parts[0], "code": raw_code, "price": current_price, "pct_chg": pct_chg, "amount": amount_yuan})
        except Exception as e:
            print(f"⚠️ [Sina] 批次 {i}-{i+BATCH} 请求出现异常: {e}")
            continue
        time.sleep(0.4) # 防止被新浪封 IP
        
    print(f"✅ [Sina] 实时行情获取完成，成功解析 {len(all_rows)} 条有效数据。")
    return pd.DataFrame(all_rows)

# ── 步骤 3：核心策略计算逻辑 (指标深度提取) ─────────────────────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    print(f"  ⚙️ 正在计算指标: {stock_name} ({code})...", end="")
    bj_now = get_beijing_time()
    end_dt = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
    
    try:
        rs = bs.query_history_k_data_plus(to_bs_code(code), "date,close,high,low,volume,amount,pctChg,turn,peTTM,pbMRQ",
                                          start_date=start_dt, end_date=end_dt, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
        if len(rows) < 65: 
            print(" [剔除] 数据不足65天")
            return False, "", {}
            
        df = pd.DataFrame(rows, columns=["date","close","high","low","volume","amount","pctChg","turn","peTTM","pbMRQ"])
        df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")

        # 1. 基本面硬过滤
        pe, pb = df["peTTM"].iloc[-1], df["pbMRQ"].iloc[-1]
        circ_mv = (df["amount"].iloc[-1] / (df["turn"].iloc[-1] / 100)) if df["turn"].iloc[-1] > 0 else 0
        if not (10 <= pe <= 30) or not (0 < pb <= 6) or circ_mv < 50_0000_0000:
            print(f" [剔除] 基本面不符 (PE:{round(pe,1)}, PB:{round(pb,1)}, 市值:{round(circ_mv/100000000,1)}亿)")
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
            print(" [剔除] 跌破MA10 或 MA60趋势向下")
            return False, "", {}
        if j.iloc[-1] > 90: 
            print(" [剔除] KDJ超买(J>90)")
            return False, "", {}

        print(" [⭐ 命中条件]")
        # 🚀 深度喂料包 (发送给 AI)
        ai_payload = (
            f"- **{stock_name}**: 涨幅{df['pctChg'].iloc[-1]}%, 量比{vol_ratio}, 换手{round(df['turn'].iloc[-1],2)}%, "
            f"PE {round(pe,1)}, MA60斜率 {slope_60}, 20日乖离 {dist_ma20}%, MACD{'动能增强' if macd_hist.iloc[-1]>macd_hist.iloc[-2] else '动能减弱'}"
        )

        detail_tag = f"💡 信号: 多周期共振突破 | 换手: {round(df['turn'].iloc[-1],2)}% | PE: {round(pe,1)}"

        return True, detail_tag, {"name": stock_name, "code": code, "price": df["close"].iloc[-1], "pct": df["pctChg"].iloc[-1], "tag": detail_tag, "ai_desc": ai_payload}
    except Exception as e: 
        print(f" [计算异常] {e}")
        return False, "", {}

# ── 步骤 4：AI 研判与推送 (企业微信 Markdown 版) ───────────────────────────────
def get_ai_commentary(context: str, api_key: str) -> str:
    if not context: 
        print("⚠️ [AI] 传入的上下文为空，跳过请求。")
        return "> 暂无满足条件的标的，AI 无需点评。"
        
    prompt = (
        "你是一个专业的私募基金首席策略师。我会为你提供一个板块内触发量化共振的个股指标包。\n"
        "请结合量比、20日乖离率、MA60斜率判断该板块目前的趋势可持续性及短线风险。\n"
        "【排版要求】：\n"
        "1. 直接输出结构化的 Markdown 格式（不可用 ```markdown 代码块包裹），150字左右。\n"
        "2. 灵活使用加粗(**重点**)和列表(- )。\n"
        "3. **必须使用企业微信专属颜色标签**来高亮情绪结论：<font color=\"info\">代表积极/安全</font>，<font color=\"warning\">代表风险/警示</font>，<font color=\"comment\">代表中性/数据</font>。\n"
        "例如：短期面临 <font color=\"warning\">回调风险</font>，但中期趋势 <font color=\"info\">依然向好</font>。\n"
        "不要任何多余的寒暄，直接给出策略研判。\n\n"
        f"【量化指标包】:\n{context}"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=50)
        
        if r.status_code != 200:
            print(f"❌ [AI] 接口报错: HTTP {r.status_code}\n{r.text}")
            return f"<font color=\"warning\">API请求失败 (HTTP {r.status_code})，请检查日志。</font>"

        res_json = r.json()
        if "choices" not in res_json or not res_json["choices"]:
            print(f"❌ [AI] 返回体异常:\n{res_json}")
            return "<font color=\"warning\">AI 接口返回格式异常。</font>"
            
        res_text = res_json["choices"][0]["message"]["content"].strip()
        print(f"✅ [AI] 成功获取点评 (长度: {len(res_text)}字)")
        
        # 强力清洗大模型有时不听话自带的 ```markdown 和 ``` 标记
        clean_text = re.sub(r'^```[a-zA-Z]*\n', '', res_text)
        clean_text = re.sub(r'\n```$', '', clean_text).strip()
        
        return clean_text
            
    except Exception as e: 
        print(f"💥 [AI] 致命异常: {e}")
        return f"<font color=\"warning\">代码执行异常: {str(e)}</font>"

# ── 主程序入口 ────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🕒 任务启动时间: {get_current_time()}")
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")
    
    if not WEBHOOK_URL: 
        print("❌ 缺失环境变量: WECHAT_WEBHOOK")
        exit(1)
    if not API_KEY:
        print("⚠️ 警告: 缺失环境变量 OPENROUTER_API_KEY，AI 分析可能失败。")

    print("🔑 正在登录 Baostock...")
    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ Baostock 登录失败: {lg.error_msg}")
    else:
        print("✅ Baostock 登录成功")

    SCAN_POOLS = {"科创/创业板": ["688", "300"], "主板精选": ["60", "00"]}
    collected_data = {}
    
    try:
        for pool_name, prefixes in SCAN_POOLS.items():
            print(f"\n📂 开始扫描板块: {pool_name}")
            triggered = []
            for prefix in prefixes:
                codes = get_stock_list(prefix)
                snapshot = fetch_realtime_sina(codes)
                if snapshot.empty: continue
                
                candidates = snapshot[(snapshot["amount"] >= 100000000) & (snapshot["pct_chg"] > 0)].sort_values("amount", ascending=False).head(40)
                print(f"📊 {prefix} 筛选出高成交额正收益候选股 {len(candidates)} 只，准备计算深度指标...")
                
                for _, row in candidates.iterrows():
                    is_hit, tag, data = analyze_stock_strategies(row['code'], row['name'])
                    if is_hit:
                        triggered.append(data)
                    if len(triggered) >= 5: 
                        print(f"🛑 该板块触发标的已达5只上限，停止深度计算。")
                        break
            
            if triggered:
                collected_data[pool_name] = triggered
    finally:
        print("🔒 正在退出 Baostock...")
        bs.logout()

    print("\n" + "="*50)
    print("💌 准备组装 Markdown 消息推送到企业微信...")
    msg = f"**🎯 硬核量化深度监控**\n> 扫描时间：<font color=\"comment\">{get_current_time()}</font>\n\n"
    
    if not collected_data:
        print("💤 今日无标的满足条件。")
        msg += "> 💤 今日暂无满足量化共振且估值合理的标的。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            
            # 拼接板块 AI 点评
            print(f"\n🧠 正在请求【{sec}】板块的 AI 点评...")
            sec_context = "\n".join([s["ai_desc"] for s in stocks])
            ai_commentary = get_ai_commentary(sec_context, API_KEY)
            
            # 直接嵌入 AI 的 Markdown 内容，不强制加引用 >，以防破坏列表格式
            msg += f"🤖 **AI 深度研判**：\n{ai_commentary}\n\n"
            
            msg += f"📌 **触发标的详情**：\n"
            for s in stocks:
                color = "warning" if s["pct"] > 0 else "info"
                msg += f"- **{s['name']}** ({s['code']}) | 当前价:{s['price']} (<font color='{color}'>{s['pct']}%</font>)\n"
                msg += f"  > <font color=\"comment\">{s['tag']}</font>\n"
            msg += "\n---\n"

    print("\n✉️ 发送 Webhook 请求...")
    try:
        resp = requests.post(WEBHOOK_URL, json={"msgtype": "markdown", "markdown": {"content": msg.strip()}})
        print(f"✅ Webhook 发送完成，企业微信响应码: {resp.status_code}, 内容: {resp.text}")
    except Exception as e:
        print(f"❌ Webhook 推送失败: {e}")
        
    print("🎉 整个量化监控任务圆满结束。")
