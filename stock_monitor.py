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
    """600519 → sh.600519"""
    return f"sh.{code}" if code.startswith(("60", "68")) else f"sz.{code}"

def to_sina_code(code: str) -> str:
    """600519 → sh600519（新浪格式）"""
    return f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"

# ── 步骤 1：baostock 获取全量股票代码 ────────────────────
def get_stock_list(prefix: str) -> list:
    """返回指定前缀的 6 位纯数字代码列表"""
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
    """带重试的 HTTP GET，失败返回 None"""
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
    """
    新浪行情接口，每批 60 只。
    返回格式（逗号分割）：
      [0] 名称  [1] 开盘  [2] 昨收  [3] 现价
      [4] 最高  [5] 最低  [8] 成交量(手)  [9] 成交额(元)
    注意：新浪接口无换手率，换手率由 baostock 技术分析函数提供前一日数据
    涨跌幅 = (现价 - 昨收) / 昨收 * 100
    """
    BATCH = 60
    all_rows = []
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    for i in range(0, len(code_list), BATCH):
        batch      = code_list[i: i + BATCH]
        sina_param = ",".join(to_sina_code(c) for c in batch)
        url        = f"https://hq.sinajs.cn/list={sina_param}"

        resp = fetch_with_retry(url, headers)
        if resp is None:
            print(f"⚠️  新浪接口第 {i // BATCH + 1} 批跳过（重试均失败）")
            continue

        for line in resp.text.strip().split("\n"):
            if "=" not in line or '"' not in line:
                continue
            try:
                # 解析股票代码：var hq_str_sh600519="..."
                raw_code  = line.split("hq_str_")[1].split("=")[0].strip()  # sh600519
                pure_code = raw_code[2:]  # 600519

                content = line.split('"')[1]
                if not content:
                    continue
                parts = content.split(",")
                if len(parts) < 10:
                    continue

                yesterday_close = float(parts[2]) if parts[2] else 0.0
                current_price   = float(parts[3]) if parts[3] else 0.0
                volume          = float(parts[8]) if parts[8] else 0.0   # 手
                amount_yuan     = float(parts[9]) if parts[9] else 0.0   # 元
                amount_wan      = round(amount_yuan / 10000, 2)           # 转万元

                # 涨跌幅自行计算
                pct_chg = 0.0
                if yesterday_close > 0:
                    pct_chg = round((current_price - yesterday_close) / yesterday_close * 100, 2)

                all_rows.append({
                    "name":    parts[0],
                    "code":    pure_code,
                    "price":   current_price,
                    "pct_chg": pct_chg,
                    "volume":  volume,
                    "amount":  amount_wan,
                })
            except (ValueError, IndexError):
                continue

        time.sleep(0.5)  # 批次间隔，降低被限流概率

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

# ── 步骤 3：baostock 计算 MA + MACD + 换手率 ─────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    """
    量化策略引擎：扫描个股是否符合战法
    换手率使用 baostock 前一交易日 turn 字段
    """
    bj_now   = get_beijing_time()
    end_dt   = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

    try:
        rs = bs.query_history_k_data_plus(
            to_bs_code(code),
            "date,close,pctChg,turn",   # turn = 换手率(%)
            start_date=start_dt,
            end_date=end_dt,
            frequency="d",
            adjustflag="2"
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())

        if len(rows) < 30:
            return False, "", {}

        df = pd.DataFrame(rows, columns=["date", "close", "pctChg", "turn"])
        df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
        df["pctChg"] = pd.to_numeric(df["pctChg"], errors="coerce")
        df["turn"]   = pd.to_numeric(df["turn"],   errors="coerce")
        df.dropna(inplace=True)

        if df.empty:
            return False, "", {}

        current_price = df["close"].iloc[-1]
        change_pct    = df["pctChg"].iloc[-1]
        turnover      = df["turn"].iloc[-1]   # 前一交易日换手率（%）

        # ── MA 均线 ──
        df["MA5"]  = df["close"].rolling(5).mean()
        df["MA10"] = df["close"].rolling(10).mean()

        # ── MACD ──
        exp1      = df["close"].ewm(span=12, adjust=False).mean()
        exp2      = df["close"].ewm(span=26, adjust=False).mean()
        df["DIF"] = exp1 - exp2
        df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()

        # ── 策略 A：右侧突破 ──
        # MA 多头排列 + MACD 金叉 + 换手率 >= 3.5%（前一日）
        cond_ma       = (df["MA5"].iloc[-1]  > df["MA10"].iloc[-1]) and \
                        (df["MA5"].iloc[-1]  > df["MA5"].iloc[-2])
        cond_macd     = (df["DIF"].iloc[-1]  > df["DEA"].iloc[-1]) and \
                        (df["DIF"].iloc[-2] <= df["DEA"].iloc[-2])
        cond_turnover = turnover >= 3.5

        # ── 策略 B：左侧连续超跌 ──
        down_days = 0
        for i in range(1, 10):
            if i <= len(df) and df["pctChg"].iloc[-i] < 0:
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
                "name":           stock_name,
                "code":           code,
                "current_price":  round(current_price, 2),
                "change_percent": change_pct,
                "turnover":       round(turnover, 2),
            }
        return False, "", {}

    except Exception as e:
        print(f"⚠️  技术指标计算失败 {code}: {e}")
        return False, "", {}

# ── 新闻获取 ─────────────────────────────────────────────
def fetch_news(stock_name: str) -> str:
    """获取最新的一条相关新闻"""
    keyword = quote(stock_name)
    url = (f"https://search.sina.com.cn/news/search"
           f"?key={keyword}&range=title&channel=finance&num=1&format=json")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        item  = r.get("result", {}).get("list", [])[0]
        title = re.sub(r'<[^>]+>', '', item.get("headline", ""))
        link  = item.get("url", "")
        return f"> - [{title}]({link})"
    except:
        return "> - 暂无实时相关资讯"

# ── AI 总结 ───────────────────────────────────────────────
def get_ai_commentary(context: str, api_key: str) -> dict:
    """调用 AI 总结板块整体情绪"""
    if not context:
        return {}
    prompt = (
        "你是硬核科技金融分析师。请对以下板块今日异动个股做150字左右的深度点评，"
        "分析板块是处于主升浪还是超跌反弹。请严格返回JSON格式，Key为板块名：\n\n"
        + context
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model":       "openrouter/free",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        r        = requests.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers=headers, json=payload, timeout=40)
        res_text = r.json()["choices"][0]["message"]["content"]
        match    = re.search(r'\{.*\}', res_text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}
    except:
        return {}

# ── 主流程 ────────────────────────────────────────────────
if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY     = os.environ.get("OPENROUTER_API_KEY")

    if not WEBHOOK_URL or not API_KEY:
        print("❌ 环境变量未配置：需要 WECHAT_WEBHOOK 和 OPENROUTER_API_KEY")
        exit(1)

    SCAN_POOLS = {
        "科创板": "688",
        "创业板": "300",
        "中小板": "002",
    }

    # ── baostock 登录 ──
    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        exit(1)
    print("✅ baostock 登录成功")

    collected_data = {}
    ai_context     = ""

    try:
        for pool_name, prefix in SCAN_POOLS.items():
            print(f"\n🔎 扫描 {pool_name}（{prefix}xxx）...")

            # 1. 获取全量代码
            code_list = get_stock_list(prefix)
            print(f"   股票数量: {len(code_list)}")
            if not code_list:
                continue

            # 2. 新浪实时快照（名称/价格/成交量/成交额/涨跌幅）
            snapshot = fetch_realtime_sina(code_list)
            if snapshot.empty:
                print(f"   ⚠️  实时数据为空，跳过")
                continue

            # 3. 初筛：排除涨跌停、价格无效，按成交额降序取前 50 进入深度扫描
            #    换手率初筛在 analyze_stock_strategies 内用 baostock 前一日数据过滤
            candidates = (
                snapshot[
                    (snapshot["pct_chg"] > -9.9) &
                    (snapshot["pct_chg"] <  9.9) &
                    (snapshot["price"]   >  0)
                ]
                .sort_values("amount", ascending=False)
                .head(50)
            )
            print(f"   初筛通过: {len(candidates)} 只，开始深度扫描...")

            # 4. 深度技术指标扫描（含 baostock 换手率判断）
            triggered_list = []
            for _, row in candidates.iterrows():
                is_hit, tag, data = analyze_stock_strategies(
                    row["code"], row["name"]
                )
                if is_hit:
                    data["tag"]  = tag
                    data["news"] = fetch_news(row["name"])
                    triggered_list.append(data)
                if len(triggered_list) >= 5:
                    break
                time.sleep(0.2)

            if triggered_list:
                collected_data[pool_name] = triggered_list
                ai_context += f"\n【{pool_name}】: " + ",".join([s["name"] for s in triggered_list])

    finally:
        bs.logout()
        print("\n👋 baostock 已登出")

    # 5. AI 研判
    ai_comments = get_ai_commentary(ai_context, API_KEY)

    # 6. 消息封装
    msg = f"**🎯 硬核科技量化监控 (新浪版)**\n> 扫描时间：{get_current_time()}\n\n"

    if not collected_data:
        msg += "> 💤 今日暂无个股触发筛选条件。"
    else:
        for sec, stocks in collected_data.items():
            msg += f"### 📊 {sec}\n"
            sec_comment = ai_comments.get(sec, "板块情绪博弈中，建议关注核心龙头。")
            msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{sec_comment}</font>\n\n"

            for s in stocks:
                color = "warning" if s["change_percent"] > 0 else "info"
                msg += (f"- **{s['name']}** ({s['code']}) | "
                        f"现价 {s['current_price']} "
                        f"(<font color='{color}'>{s['change_percent']}%</font>) | "
                        f"换手率(前一日) {s['turnover']}%\n")
                msg += f"  > 💡 战法信号：**<font color=\"warning\">{s['tag']}</font>**\n"
                msg += f"  {s['news']}\n"
            msg += "\n---\n"

    # 7. 发送 Webhook
    requests.post(
        WEBHOOK_URL,
        json={"msgtype": "markdown", "markdown": {"content": msg.strip()}}
    )
    print(f"✅ 处理完成，扫描到 {len(collected_data)} 个板块有信号")
