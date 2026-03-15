import warnings
warnings.filterwarnings("ignore")

import requests
import os
import datetime
import time
import json
import re
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

# ── 新闻方案 ─────────────────────────────────────────────────────────────
def fetch_news_from_tencent_rss(stock_name):
    keyword = quote(stock_name)
    url = f"https://finance.qq.com/hqdata/search?pn=1&rn=5&query={keyword}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.encoding = 'utf-8'
        data = r.json()
        items = data.get("data", {}).get("list", [])
        if not items: return None, None
        news_list, titles_display = [], []
        for item in items[:2]: # 取前2条保持卡片紧凑
            title = item.get("title", "")
            summary = item.get("summary", item.get("abstract", ""))
            link = item.get("url", "")
            if title:
                news_list.append(f"标题：{title}\n摘要：{summary}")
                titles_display.append(f"> - [{title}]({link})" if link else f"> - {title}")
        if news_list: return "\n".join(news_list), "\n".join(titles_display)
    except Exception as e:
        pass
    return None, None

def fetch_news_from_sina_rss(stock_name):
    keyword = quote(stock_name)
    url = f"https://search.sina.com.cn/news/search?key={keyword}&range=title&channel=finance&num=3&format=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.encoding = 'utf-8'
        data = r.json()
        items = data.get("result", {}).get("list", [])
        if not items: return None, None
        news_list, titles_display = [], []
        for item in items[:2]:
            title = re.sub(r'<[^>]+>', '', item.get("headline", ""))
            intro = re.sub(r'<[^>]+>', '', item.get("intro", ""))
            link = item.get("url", "")
            if title:
                news_list.append(f"标题：{title}\n摘要：{intro}")
                titles_display.append(f"> - [{title}]({link})" if link else f"> - {title}")
        if news_list: return "\n".join(news_list), "\n".join(titles_display)
    except:
        pass
    return None, None

def get_latest_news(stock_name):
    sources = [
        ("腾讯财经", fetch_news_from_tencent_rss),
        ("新浪财经", fetch_news_from_sina_rss)
    ]
    for name, fn in sources:
        news_full, titles_display = fn(stock_name)
        if news_full: return news_full, titles_display
        time.sleep(0.5)
    return "近期暂无重大新闻。", "> - 暂无资讯"

# ── AI 分析：支持多模型路由 ─────────────────────────────────────────────────
FREE_MODELS = [
    "openrouter/free", 
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free"
]
MAX_RETRIES = 3
RETRY_DELAY = 8

def call_openrouter(api_key, model, prompt, timeout=60):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4, # 稍微提高一点温度，让点评更灵动
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content'], None
        return None, f"HTTP {r.status_code}"
    except Exception as e:
        return None, str(e)

def get_batch_ai_analysis(all_sectors_context, api_key):
    # 【核心微调：针对 20% 涨跌幅和高科技情绪的专属 Prompt】
    prompt = f"""你是专业的硬核科技金融分析师。请结合以下按【板块】分类的成分股盘面数据和最新资讯，为每个板块写一段150字左右的深度点评。

【特别关注指令】
1. 这些标的（创业板30开头、科创板688开头）具有 **20%的单日涨跌幅限制**，请在点评中体现对其高波动性、资金博弈或溢价情绪的观察。
2. 重点挖掘“高科技板块情绪”（如光刻机自主可控、射频芯片国产替代等）与盘面资金共振的情况。
3. 归纳该板块是普涨/普跌，还是内部分化，并指出突发新闻对整个产业链逻辑有何潜在影响。

【极其重要的格式指令】
你必须且只能返回一个合法的 JSON 对象，绝对不要包含任何 Markdown 标记（如 ```json），不要解释，不要前缀！
返回格式示例：
{{
    "光刻机与核心设备": "今日光刻机板块受外围限制传闻影响，资金博弈激烈。在20%涨跌幅限制下，中微公司等核心标的呈现...",
    "无线电与射频通信": "无线电板块整体走势平稳，国产替代情绪带动下..."
}}

【今日板块数据与新闻】
{all_sectors_context}
"""

    for model in FREE_MODELS:
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"🧠 尝试模型 [{model}]，第 {attempt} 次...")
            content, err = call_openrouter(api_key, model, prompt)

            if err:
                if "429" in err or "rate" in err.lower():
                    time.sleep(RETRY_DELAY)
                    continue
                break

            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except:
                    break
            break
        time.sleep(2)
    return {}

# ── 微信推送：按板块聚合排版 ──────────────────────────────────────────────────
def send_combined_to_wechat(webhook_url, collected_data, ai_comments_dict):
    current_time = get_current_time()
    content = f"**🚀 硬核科技板块监控报告**\n> 更新时间：<font color=\"comment\">{current_time}</font>\n\n"

    for sector_name, info in collected_data.items():
        avg_pct = info['avg_pct']
        # 绿色字体(info)表示下跌，红色字体(warning)表示上涨
        color = "warning" if avg_pct > 0 else ("info" if avg_pct < 0 else "comment")
        
        content += f"### 📊 【{sector_name}】 | 板块情绪：<font color=\"{color}\">{avg_pct}%</font>\n"
        
        ai_comment = ai_comments_dict.get(sector_name, "暂无分析数据。")
        content += f"> 🤖 **AI 逻辑梳理**：\n> <font color=\"comment\">{ai_comment}</font>\n\n"
        
        content += "**[ 核心成分股异动 ]**\n"
        for stock in info['stocks']:
            sd = stock['data']
            try:
                pct = float(sd['change_percent'])
            except ValueError:
                pct = 0
            s_color = "warning" if pct > 0 else "info"
            
            # 个股摘要
            content += f"- **{sd['name']}** ({sd['code']})：价 **{sd['current_price']}** | 涨跌 <font color=\"{s_color}\">{sd['change_percent']}%</font>\n"
            
            # 只取第一条新闻展示，避免卡片过长导致微信截断
            first_news = stock['news_titles'].split('\n')[0] if stock['news_titles'] else "> - 暂无相关资讯"
            content += f"  {first_news}\n"
        
        content += "\n---\n"

    payload = {"msgtype": "markdown", "markdown": {"content": content.strip()}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("✅ 微信推送成功！")
        else:
            print(f"⚠️ 推送返回非 200: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"❌ 推送微信失败: {e}")

# ── 主流程 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")

    if not WEBHOOK_URL or not OPENROUTER_KEY:
        print("❌ 缺少环境变量 WECHAT_WEBHOOK 或 OPENROUTER_API_KEY")
        exit(1)

    # 【全新定义：基于板块的字典结构】
    SECTORS = {
        "光刻机与核心设备": [
            "sz300346",  # 南大光电
            "sh688012",  # 中微公司
            "sh688037",  # 芯源微
        ],
        "无线电与射频芯片": [
            "sz300136",  # 信维通信
            "sh688270",  # 臻镭科技
        ]
    }

    print("🔄 开始拉取板块数据...")
    collected_data = {}
    all_sectors_context = ""

    for sector_name, codes in SECTORS.items():
        print(f"\n📁 处理板块: {sector_name}")
        sector_stocks_info = []
        sector_total_pct = 0.0
        valid_count = 0

        for code in codes:
            data = get_stock_data(code)
            if not data: continue
            
            print(f"  ✅ {data['name']}: {data['change_percent']}%")
            news_full, news_titles = get_latest_news(data['name'])
            
            sector_stocks_info.append({
                "code": code,
                "data": data,
                "news_titles": news_titles,
                "news_full": news_full # 留给 AI 分析用
            })
            
            try:
                sector_total_pct += float(data['change_percent'])
                valid_count += 1
            except ValueError:
                pass
            time.sleep(1)

        sector_avg_pct = round(sector_total_pct / valid_count, 2) if valid_count > 0 else 0
        
        collected_data[sector_name] = {
            "avg_pct": sector_avg_pct,
            "stocks": sector_stocks_info
        }

        # 拼接提供给 AI 的上下文
        all_sectors_context += f"\n=== 【{sector_name}】 板块总体涨跌幅: {sector_avg_pct}% ===\n"
        for stock in sector_stocks_info:
            d = stock['data']
            all_sectors_context += f"成分股 {d['name']} ({stock['code']}): 现价 {d['current_price']}, 涨跌幅 {d['change_percent']}%\n新闻: {stock['news_full']}\n"

    if not collected_data:
        print("❌ 所有股票数据均获取失败，退出")
        exit(1)

    print("\n" + "="*50)
    ai_comments_dict = get_batch_ai_analysis(all_sectors_context, OPENROUTER_KEY)

    print("\n📤 准备发送微信整合卡片...")
    send_combined_to_wechat(WEBHOOK_URL, collected_data, ai_comments_dict)
    print("🎉 执行完毕！")
