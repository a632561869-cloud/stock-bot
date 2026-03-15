import warnings
warnings.filterwarnings("ignore")

import requests
import os
import datetime
import time
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

def get_current_time():
    utc_now = datetime.datetime.utcnow()
    beijing_now = utc_now + datetime.timedelta(hours=8)
    return beijing_now.strftime("%Y-%m-%d %H:%M:%S")

def get_stock_data(stock_code):
    url = f"http://qt.gtimg.cn/q={stock_code}"
    try:
        response = requests.get(url, timeout=10)
        response.encoding = 'gbk'
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
    except Exception as e:
        print(f"❌ 获取股票数据失败 [{stock_code}]: {e}")
        return None

# ── 新闻方案 1：腾讯财经 RSS ──────────────────────────────────────────────────
def fetch_news_from_tencent_rss(stock_name):
    """腾讯财经 RSS，无需第三方库，直接解析 XML"""
    keyword = quote(stock_name)
    url = f"https://finance.qq.com/hqdata/search?pn=1&rn=5&query={keyword}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.encoding = 'utf-8'
        data = r.json()
        items = data.get("data", {}).get("list", [])
        if not items:
            return None, None
        news_list, titles_display = [], []
        for item in items[:3]:
            title = item.get("title", "")
            summary = item.get("summary", item.get("abstract", ""))
            link = item.get("url", "")
            if title:
                news_list.append(f"标题：{title}\n摘要：{summary}")
                titles_display.append(f"> - [{title}]({link})" if link else f"> - {title}")
        if news_list:
            return "\n".join(news_list), "\n".join(titles_display)
    except Exception as e:
        print(f"  腾讯财经接口失败: {e}")
    return None, None

# ── 新闻方案 2：新浪财经搜索 RSS ─────────────────────────────────────────────
def fetch_news_from_sina_rss(stock_name):
    """新浪财经 RSS 搜索，解析标准 RSS XML"""
    keyword = quote(stock_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&range=title&channel=finance&num=5&format=json"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.encoding = 'utf-8'
        data = r.json()
        items = data.get("result", {}).get("list", [])
        if not items:
            return None, None
        news_list, titles_display = [], []
        for item in items[:3]:
            title = re.sub(r'<[^>]+>', '', item.get("headline", ""))  # 去掉高亮标签
            intro = re.sub(r'<[^>]+>', '', item.get("intro", ""))
            link = item.get("url", "")
            if title:
                news_list.append(f"标题：{title}\n摘要：{intro}")
                titles_display.append(f"> - [{title}]({link})" if link else f"> - {title}")
        if news_list:
            return "\n".join(news_list), "\n".join(titles_display)
    except Exception as e:
        print(f"  新浪财经接口失败: {e}")
    return None, None

# ── 新闻方案 3：东方财富 RSS（港股/A股通用）────────────────────────────────────
def fetch_news_from_eastmoney(stock_name):
    """东方财富搜索接口"""
    keyword = quote(stock_name)
    url = (f"https://search-api-web.eastmoney.com/search/jsonp"
           f"?cb=jQuery&param={{\"uid\":\"\",\"keyword\":\"{stock_name}\","
           f"\"type\":[\"cmsArticleWebOld\"],\"count\":{{\"cmsArticleWebOld\":3}},"
           f"\"pageIndex\":1}}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)",
               "Referer": "https://www.eastmoney.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.encoding = 'utf-8'
        # jsonp 格式 → 去掉 jQuery(...) 外壳
        text = re.search(r'jQuery\((.*)\)', r.text, re.DOTALL)
        if not text:
            return None, None
        data = json.loads(text.group(1))
        items = data.get("Result", {}).get("cmsArticleWebOld", [])
        if not items:
            return None, None
        news_list, titles_display = [], []
        for item in items[:3]:
            title = item.get("Title", "")
            summary = item.get("Content", "")[:80]
            link = item.get("Url", "")
            if title:
                news_list.append(f"标题：{title}\n摘要：{summary}")
                titles_display.append(f"> - [{title}]({link})" if link else f"> - {title}")
        if news_list:
            return "\n".join(news_list), "\n".join(titles_display)
    except Exception as e:
        print(f"  东方财富接口失败: {e}")
    return None, None

# ── 新闻方案 4：DuckDuckGo（原方案，保留为最终备用）─────────────────────────────
def fetch_news_from_ddg(stock_name):
    try:
        from duckduckgo_search import DDGS
        results = DDGS().text(f"{stock_name} 股票 财经 最新消息", max_results=3)
        if not results:
            return None, None
        news_list, titles_display = [], []
        for r in results:
            title = r.get('title', '')
            body = r.get('body', '')
            url_link = r.get('href', '')
            if title:
                news_list.append(f"标题：{title}\n摘要：{body}")
                titles_display.append(f"> - [{title}]({url_link})" if url_link else f"> - {title}")
        if news_list:
            return "\n".join(news_list), "\n".join(titles_display)
    except Exception as e:
        print(f"  DuckDuckGo 失败: {e}")
    return None, None

def get_latest_news(stock_name):
    """依次尝试多个新闻源，任意一个成功即返回"""
    sources = [
        ("腾讯财经", fetch_news_from_tencent_rss),
        ("新浪财经", fetch_news_from_sina_rss),
        ("东方财富", fetch_news_from_eastmoney),
        ("DuckDuckGo", fetch_news_from_ddg),
    ]
    for name, fn in sources:
        print(f"  📡 尝试 [{name}] 获取 [{stock_name}] 新闻...")
        news_full, titles_display = fn(stock_name)
        if news_full:
            print(f"  ✅ [{name}] 成功")
            return news_full, titles_display
        time.sleep(0.5)

    print(f"  ⚠️ 所有新闻源均失败，返回默认值")
    return "近期暂无重大新闻。", "> - 暂无新闻数据"


# ── AI 分析：多模型自动切换 ───────────────────────────────────────────────────
# 优先使用稳定的免费模型；全部失败时用最后一个付费回落
FREE_MODELS = [
    "google/gemini-2.0-flash-exp:free",        # Google 免费，稳定
    "google/gemini-2.5-pro-exp-03-25:free",    # Google 实验版
    "deepseek/deepseek-chat-v3-0324:free",     # DeepSeek V3
    "qwen/qwen3-235b-a22b:free",               # Qwen3 大模型
    "meta-llama/llama-4-maverick:free",        # Llama 4
    "mistralai/mistral-small-3.1-24b-instruct:free",  # Mistral
]
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

def call_openrouter(api_key, model, prompt, timeout=60):
    """单次 API 调用，返回 (content_str, error_str)"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/stock-monitor",  # 有助于 OpenRouter 识别
        "X-Title": "Stock Monitor Bot",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            content = r.json()['choices'][0]['message']['content']
            return content, None
        else:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, str(e)

def get_batch_ai_analysis(all_stocks_context, api_key):
    prompt = f"""你是专业的金融分析师。请结合以下多只股票的盘面数据和资讯，为每只股票写一段100字左右的客观点评。

【极其重要的格式指令】
你必须且只能返回一个合法的 JSON 对象，绝对不要包含任何 Markdown 标记（如 ```json），不要解释，不要前缀！
返回格式示例：
{{
    "sz000001": "平安银行今日走势...",
    "sh600519": "贵州茅台受新闻影响..."
}}

【今日股票数据与新闻】
{all_stocks_context}
"""

    for model in FREE_MODELS:
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"🧠 尝试模型 [{model}]，第 {attempt} 次...")
            content, err = call_openrouter(api_key, model, prompt)

            if err:
                if "429" in err or "rate" in err.lower():
                    print(f"  ⚠️ 限流 (429)，等待 {RETRY_DELAY}s 后重试...")
                    time.sleep(RETRY_DELAY)
                    continue  # 同模型再试
                else:
                    print(f"  ❌ 错误: {err}")
                    break  # 非限流错误，直接换下一个模型

            # 成功拿到内容
            print(f"  ✅ 成功！AI 原始回复:\n{content[:300]}...\n")
            # 抠出 JSON
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception as e:
                    print(f"  ⚠️ JSON 解析失败: {e}，尝试下一个模型")
                    break
            else:
                print("  ⚠️ 回复中找不到 JSON 结构，尝试下一个模型")
                break
        else:
            # 内层 for 循环正常结束（即重试次数耗尽，均为429）
            print(f"  ⛔ [{model}] 重试 {MAX_RETRIES} 次均被限流，切换模型")

        time.sleep(2)  # 换模型前稍作等待

    print("⛔ 所有模型均失败，返回空分析")
    return {}


# ── 微信推送 ──────────────────────────────────────────────────────────────────
def send_combined_to_wechat(webhook_url, collected_data, ai_comments_dict):
    current_time = get_current_time()
    content = f"**📈 股票深度监控报告**\n> 更新时间：<font color=\"comment\">{current_time}</font>\n\n"

    for index, (code, info) in enumerate(collected_data.items()):
        stock_data = info['data']
        news_titles_display = info['news_titles']
        ai_comment = ai_comments_dict.get(code, "暂无分析数据。")

        try:
            pct = float(stock_data['change_percent'])
        except ValueError:
            pct = 0
        color = "warning" if pct > 0 else "info"

        content += (
            f"**{index + 1}. {stock_data['name']} ({stock_data['code']})**\n"
            f"> 当前价格：**{stock_data['current_price']}** | "
            f"今日涨跌：<font color=\"{color}\">{stock_data['change_percent']}%</font>\n"
            f"> 📰 **最新资讯**：\n"
            f"{news_titles_display}\n"
            f"> 🤖 **深度点评**：\n"
            f"> <font color=\"info\">{ai_comment}</font>\n\n"
        )

    payload = {"msgtype": "markdown", "markdown": {"content": content.strip()}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("✅ 整合卡片推送成功！")
        else:
            print(f"⚠️ 推送返回非 200: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"❌ 推送微信失败: {e}")


# ── 主流程 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")

    STOCK_CODES = [
        "sz000001",  # 平安银行
        "sh600519",  # 贵州茅台
        "hk00700",   # 腾讯控股
    ]

    if not WEBHOOK_URL or not OPENROUTER_KEY:
        print("❌ 缺少环境变量 WECHAT_WEBHOOK 或 OPENROUTER_API_KEY")
        exit(1)

    print("🔄 开始拉取数据...")
    collected_data = {}
    all_stocks_context = ""

    for code in STOCK_CODES:
        print(f"\n📊 处理股票: {code}")
        data = get_stock_data(code)
        if not data:
            print(f"  ⚠️ 无法获取 [{code}] 行情，跳过")
            continue

        print(f"  ✅ 行情: {data['name']} {data['current_price']} ({data['change_percent']}%)")
        news_full, news_titles = get_latest_news(data['name'])

        collected_data[code] = {"data": data, "news_titles": news_titles}
        all_stocks_context += (
            f"\n--- 股票：{data['name']} ({code}) ---\n"
            f"当前价：{data['current_price']}，涨跌幅：{data['change_percent']}%\n"
            f"相关新闻：\n{news_full}\n"
        )
        time.sleep(1)

    if not collected_data:
        print("❌ 所有股票数据均获取失败，退出")
        exit(1)

    print("\n" + "="*50)
    ai_comments_dict = get_batch_ai_analysis(all_stocks_context, OPENROUTER_KEY)

    print("\n📤 准备发送微信整合卡片...")
    send_combined_to_wechat(WEBHOOK_URL, collected_data, ai_comments_dict)
    print("🎉 执行完毕！")
