import warnings
warnings.filterwarnings("ignore")
import requests, os, datetime, time, json, re
import pandas as pd
import akshare as ak
from datetime import timezone, timedelta

# --- 基础配置 ---
def get_beijing_time():
    return datetime.datetime.now(timezone(timedelta(hours=8)))

def get_current_time():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def analyze_technical_indicators(symbol, current_spot_data):
    """
    针对初筛通过的股票，进行深度技术面计算
    """
    bj_now = get_beijing_time()
    start_date = (bj_now - datetime.timedelta(days=60)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, adjust="qfq")
        if df is None or len(df) < 20: return False, ""

        # MA计算
        df['MA5'] = df['收盘'].rolling(window=5).mean()
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        
        # MACD计算
        exp1 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp2 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['MACD_DIF'] = exp1 - exp2
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()

        # 逻辑判断
        # 1. 右侧突破：MA5 > MA10 且 DIF > DEA (持仓区间)
        cond_ma = df['MA5'].iloc[-1] > df['MA10'].iloc[-1]
        cond_macd = df['MACD_DIF'].iloc[-1] > df['MACD_DEA'].iloc[-1]
        
        # 2. 左侧超跌：统计近期连跌天数
        down_days = 0
        for i in range(1, 10):
            if df['涨跌幅'].iloc[-i] < 0: down_days += 1
            else: break

        tags = []
        if cond_ma and cond_macd: tags.append("🚀 多头区间")
        if 5 <= down_days <= 9: tags.append(f"🩸 连跌{down_days}天")

        if tags:
            return True, " & ".join(tags)
        return False, ""
    except:
        return False, ""

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    API_KEY = os.environ.get("OPENROUTER_API_KEY")
    
    # 板块定义
    TARGET_SECTORS = ["半导体", "人工智能", "机器人"]
    MARKET_POOLS = {"科创板": "688", "创业板": "300"}

    # 第一步：一次性拉取全量实时快照 (这是规避限制的关键)
    print("🚀 正在获取全市场实时快照...")
    all_spot = ak.stock_zh_a_spot_em()
    
    collected_data = {}
    ai_context = ""

    # 第二步：按板块逻辑进行内存筛选
    for sec_name in TARGET_SECTORS + list(MARKET_POOLS.keys()):
        print(f"🔎 正在扫描: {sec_name}...")
        
        if sec_name in TARGET_SECTORS:
            # 获取该行业成分股代码列表
            try:
                sector_members = ak.stock_board_industry_cons_em(symbol=sec_name)['代码'].tolist()
                subset = all_spot[all_spot['代码'].isin(sector_members)]
            except: continue
        else:
            # 筛选市场分类
            prefix = MARKET_POOLS[sec_name]
            subset = all_spot[all_spot['代码'].str.startswith(prefix)]

        # 初筛条件：换手率 > 3% (剔除僵尸股)，且今日不跌停
        # 盘中运行建议换手率门槛设低一点，比如 2.0
        potential_candidates = subset[subset['换手率'] >= 2.0].sort_values(by="成交额", ascending=False).head(30)

        triggered_list = []
        for _, row in potential_candidates.iterrows():
            # 第三步：精细化深度扫描指标
            is_hit, tag = analyze_technical_indicators(row['代码'], row)
            if is_hit:
                triggered_list.append({
                    "name": row['名称'],
                    "code": row['代码'],
                    "current_price": row['最新价'],
                    "change_percent": row['涨跌幅'],
                    "tag": tag
                })
            
            if len(triggered_list) >= 5: break
            time.sleep(0.1) # 极短延迟即可

        if triggered_list:
            collected_data[sec_name] = triggered_list
            ai_context += f"【{sec_name}】:{','.join([s['name'] for s in triggered_list])} "

    # --- 后续 AI 总结与推送逻辑与之前一致 ---
    # (此处省略发送 Webhook 的代码，保持之前逻辑即可)
