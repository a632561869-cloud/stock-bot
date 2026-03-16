import warnings
warnings.filterwarnings("ignore")

import requests
import os
import datetime
import time
import json
import re
import logging
from datetime import timezone, timedelta

import pandas as pd
import baostock as bs

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── 集中配置 ──────────────────────────────────────────────
CONFIG = {
    "min_amount":        1e8,       # 预筛最低成交额（1亿）
    "min_market_cap":    5e9,       # 最低流通市值（50亿）
    "pe_min":            10,
    "pe_max":            30,
    "pb_max":            5,
    "top_n_candidates":  40,        # 每板块取成交额前N支
    "max_hits_per_pool": 5,         # 每板块最多保留命中数
    "history_days":      150,       # 日线历史天数
    "weekly_days":       400,       # 周线历史天数
    "min_history_rows":  65,        # 最少历史行数
    "ai_timeout":        40,        # AI接口超时秒数
    "ai_max_tokens":     500,
    "sina_batch":        60,        # 新浪批量查询条数
    "sina_timeout":      10,
}

SCAN_POOLS = {
    "科创/创业板": ["688", "300"],
    "主板精选":   ["60",  "00"],
}

# ── 基础工具 ──────────────────────────────────────────────
def get_beijing_time() -> datetime.datetime:
    return datetime.datetime.now(timezone(timedelta(hours=8)))

def get_current_time() -> str:
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def to_bs_code(code: str) -> str:
    return f"sh.{code}" if code.startswith(("60", "68")) else f"sz.{code}"

def to_sina_code(code: str) -> str:
    return f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"

# ── 步骤 1：获取股票列表 ──────────────────────────────────
def get_stock_list(prefix: str) -> list:
    rs = bs.query_stock_basic(code_name="")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        logger.warning(f"[{prefix}] 获取股票列表为空")
        return []

    df = pd.DataFrame(rows, columns=rs.fields)
    df.columns = [c.lower() for c in df.columns]

    if "status" in df.columns:
        df = df[df["status"] == "1"]

    codes = df["code"].str.split(".").str[1]
    result = codes[codes.str.startswith(prefix)].tolist()
    logger.info(f"[{prefix}] 获取上市股票 {len(result)} 支")
    return result

# ── 步骤 2：极速批量快照 ──────────────────────────────────
def fetch_realtime_sina(code_list: list) -> pd.DataFrame:
    BATCH = CONFIG["sina_batch"]
    all_rows = []
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0",
    }
    for i in range(0, len(code_list), BATCH):
        batch = code_list[i: i + BATCH]
        sina_param = ",".join(to_sina_code(c) for c in batch)
        url = f"https://hq.sinajs.cn/list={sina_param}"
        try:
            resp = requests.get(url, headers=headers, timeout=CONFIG["sina_timeout"])
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                if "=" not in line or '"' not in line:
                    continue
                raw_code = line.split("hq_str_")[1].split("=")[0].strip()[2:]
                parts = line.split('"')[1].split(",")
                # 新浪快照至少32个字段，少于此说明数据不完整
                if len(parts) < 32:
                    continue
                try:
                    y_close = float(parts[2])
                    now_p   = float(parts[3])
                    amount  = float(parts[9])
                except (ValueError, IndexError):
                    continue
                if y_close <= 0:
                    continue
                pct = round((now_p - y_close) / y_close * 100, 2)
                # 过滤ST及退市股
                if "ST" in parts[0] or "退" in parts[0]:
                    continue
                all_rows.append({
                    "name":   parts[0],
                    "code":   raw_code,
                    "price":  now_p,
                    "pct":    pct,
                    "amount": amount,
                })
        except Exception as e:
            logger.warning(f"新浪快照请求失败 (batch {i}): {e}")
            continue

    df = pd.DataFrame(all_rows)
    logger.info(f"快照获取完成，有效股票 {len(df)} 支")
    return df

# ── 步骤 3：多维度量化计算（每线程独立登录） ──────────────
def analyze_stock_strategies(code: str, stock_name: str) -> tuple:
    """
    每个线程独立调用 bs.login() / bs.logout()，避免多线程共用
    同一 socket 连接导致数据错乱。
    """
    bs.login()
    try:
        return _do_analyze(code, stock_name)
    except Exception as e:
        logger.warning(f"[{code}] {stock_name} 分析异常: {e}")
        return False, "", {}
    finally:
        bs.logout()

def _do_analyze(code: str, stock_name: str) -> tuple:
    bj_now   = get_beijing_time()
    end_dt   = bj_now.strftime("%Y-%m-%d")
    start_dt = (bj_now - datetime.timedelta(days=CONFIG["history_days"])).strftime("%Y-%m-%d")

    rs = bs.query_history_k_data_plus(
        to_bs_code(code),
        "date,close,high,low,volume,amount,pctChg,turn,peTTM,pbMRQ",
        start_date=start_dt,
        end_date=end_dt,
        frequency="d",
        adjustflag="2",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())

    if len(rows) < CONFIG["min_history_rows"]:
        return False, "", {}

    df = pd.DataFrame(rows, columns=["date", "close", "high", "low",
                                      "volume", "amount", "pctChg", "turn",
                                      "peTTM", "pbMRQ"])
    df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")

    # ── 基本面过滤 ────────────────────────────────────────
    pe = df["peTTM"].iloc[-1]
    pb = df["pbMRQ"].iloc[-1]
    turn_last = df["turn"].iloc[-1]

    if pd.isna(pe) or pd.isna(pb) or pd.isna(turn_last):
        return False, "", {}

    circ_mv = (df["amount"].iloc[-1] / (turn_last / 100)) if turn_last > 0 else 0

    if not (CONFIG["pe_min"] <= pe <= CONFIG["pe_max"]):
        return False, "", {}
    if not (0 < pb <= CONFIG["pb_max"]):
        return False, "", {}
    if circ_mv < CONFIG["min_market_cap"]:
        return False, "", {}

    # ── 技术面过滤 ────────────────────────────────────────
    df["MA10"] = df["close"].rolling(10).mean()
    df["MA20"] = df["close"].rolling(20).mean()
    df["MA60"] = df["close"].rolling(60).mean()

    if df["close"].iloc[-1] < df["MA10"].iloc[-1]:
        return False, "", {}
    if df["MA60"].iloc[-1] < df["MA60"].iloc[-5]:
        return False, "", {}

    # ── 周线共振 ──────────────────────────────────────────
    start_w = (bj_now - datetime.timedelta(days=CONFIG["weekly_days"])).strftime("%Y-%m-%d")
    rs_w = bs.query_history_k_data_plus(
        to_bs_code(code), "date,close",
        start_date=start_w,
        frequency="w",
        adjustflag="2",
    )
    ws = []
    while rs_w.error_code == "0" and rs_w.next():
        ws.append(rs_w.get_row_data())

    df_w = pd.DataFrame(ws, columns=["date", "close"])
    df_w["close"] = pd.to_numeric(df_w["close"], errors="coerce")

    dif_w = df_w["close"].ewm(span=12).mean() - df_w["close"].ewm(span=26).mean()
    dea_w = dif_w.ewm(span=9).mean()

    tags = ["🎯 估值安全", "📈 多周期共振", f"PE:{round(pe, 1)}"]
    if dif_w.iloc[-1] > dea_w.iloc[-1]:
        tags.append("🟢 周线金叉")

    ai_desc = (
        f"{stock_name}("
        f"涨跌:{df['pctChg'].iloc[-1]}%,"
        f"换手:{round(turn_last, 2)}%,"
        f"PE:{round(pe, 1)})"
    )
    tag_str = " | ".join(tags)

    logger.info(f"[{code}] {stock_name} 命中 → {tag_str}")

    return True, tag_str, {
        "name":    stock_name,
        "code":    code,
        "price":   df["close"].iloc[-1],
        "pct":     df["pctChg"].iloc[-1],
        "tag":     tag_str,
        "ai_desc": ai_desc,
    }

# ── 步骤 4：AI 深度点评 ──────────────────────────────────
def get_ai_commentary(context: str, api_key: str) -> dict:
    if not context:
        return {}

    prompt = (
        "你是一个专业的A股策略分析师。我会给你提供一组量化筛选出的异动个股数据（包含涨幅、换手、PE）。\n"
        "请根据这些真实数据，分析该板块当前的资金偏好、估值安全度及后续趋势。150字左右。\n"
        "必须返回 JSON 格式，Key 为板块名，内容为点评。\n\n"
        + context
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": CONFIG["ai_max_tokens"],
    }

    for attempt in range(1, 4):  # 最多重试3次
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=CONFIG["ai_timeout"],
            )
            r.raise_for_status()
            res_text = r.json()["choices"][0]["message"]["content"].strip()
            clean = re.sub(r"^```json\s*|```$", "", res_text, flags=re.MULTILINE).strip()
            return json.loads(re.search(r"\{.*\}", clean, re.DOTALL).group(0))
        except Exception as e:
            logger.warning(f"AI 接口第 {attempt} 次调用失败: {e}")
            if attempt < 3:
                time.sleep(2 ** attempt)  # 指数退避：2s, 4s

    logger.error("AI 接口连续失败，跳过点评")
    return {}

# ── 步骤 5：串行扫描单个板块 ─────────────────────────────
def scan_pool(pool_name: str, prefixes: list) -> list:
    triggered = []

    for prefix in prefixes:
        codes    = get_stock_list(prefix)
        snapshot = fetch_realtime_sina(codes)
        if snapshot.empty:
            continue

        candidates = (
            snapshot[snapshot["amount"] >= CONFIG["min_amount"]]
            .sort_values("amount", ascending=False)
            .head(CONFIG["top_n_candidates"])
        )
        logger.info(f"[{pool_name}/{prefix}] 候选股 {len(candidates)} 支，开始串行分析...")

        for _, row in candidates.iterrows():
            is_hit, tag, data = analyze_stock_strategies(row["code"], row["name"])
            if is_hit:
                triggered.append(data)
                if len(triggered) >= CONFIG["max_hits_per_pool"]:
                    break

    logger.info(f"[{pool_name}] 扫描完成，命中 {len(triggered)} 支")
    return triggered

# ── 步骤 6：主逻辑 ────────────────────────────────────────
def build_message(collected_data: dict, ai_comments: dict) -> str:
    msg = f"**🎯 硬核量化监控 (共振过滤版)**\n> 扫描时间：{get_current_time()}\n\n"

    if not collected_data:
        msg += "> 💤 今日暂无满足 PE 10-30 & 多周期共振的标的。"
        return msg

    for sec, stocks in collected_data.items():
        msg += f"### 📊 {sec}\n"
        comment = ai_comments.get(sec, "个股技术形态呈现多周期共振向上，估值处于安全区间。")
        msg += f"> 🤖 **AI 战法研判**：\n> <font color=\"comment\">{comment}</font>\n\n"
        for s in stocks:
            color = "warning" if float(s["pct"]) > 0 else "info"
            msg += (
                f"- **{s['name']}** ({s['code']}) | "
                f"{s['price']} (<font color='{color}'>{s['pct']}%</font>)\n"
                f"  💡 信号：**<font color=\"warning\">{s['tag']}</font>**\n"
            )
        msg += "\n---\n"

    return msg.strip()

def main():
    webhook_url = os.environ.get("WECHAT_WEBHOOK")
    api_key     = os.environ.get("OPENROUTER_API_KEY")

    if not webhook_url:
        logger.error("环境变量 WECHAT_WEBHOOK 未设置，退出")
        return

    # 主进程登录一次，用于 get_stock_list
    bs.login()
    collected_data: dict = {}
    ai_context: str = ""

    try:
        for pool_name, prefixes in SCAN_POOLS.items():
            triggered = scan_pool(pool_name, prefixes)
            if triggered:
                collected_data[pool_name] = triggered
                ai_context += f"\n【{pool_name}】:" + "; ".join(s["ai_desc"] for s in triggered)
    finally:
        bs.logout()

    ai_comments = get_ai_commentary(ai_context, api_key) if api_key else {}
    msg = build_message(collected_data, ai_comments)

    try:
        resp = requests.post(
            webhook_url,
            json={"msgtype": "markdown", "markdown": {"content": msg}},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("消息推送成功")
    except Exception as e:
        logger.error(f"消息推送失败: {e}")

if __name__ == "__main__":
    main()
