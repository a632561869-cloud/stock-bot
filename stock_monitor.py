import requests
import os
import datetime

def get_stock_data(stock_code):
    """从腾讯财经获取股票数据"""
    url = f"http://qt.gtimg.cn/q={stock_code}"
    response = requests.get(url)
    data_str = response.text
    
    if "=" not in data_str or len(data_str.split('~')) < 33:
        return None
    
    info_list = data_str.split('"')[1].split('~')
    return {
        "name": info_list[1],           # 名称
        "code": info_list[2],           # 代码
        "current_price": info_list[3],  # 当前价
        "change_percent": info_list[32] # 涨跌幅(%)
    }

def send_to_wechat(webhook_url, stock_data):
    """发送到企业微信"""
    color = "warning" if float(stock_data['change_percent']) > 0 else "info"
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    content = f"""**📈 股票价格实时监控**
> 股票名称：<font color="comment">{stock_data['name']} ({stock_data['code']})</font>
> 当前价格：**{stock_data['current_price']}**
> 今日涨跌：<font color="{color}">{stock_data['change_percent']}%</font>
> 更新时间：<font color="comment">{current_time}</font>"""

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content}
    }
    response = requests.post(webhook_url, json=payload)
    print("发送结果:", response.text)

if __name__ == "__main__":
    # 读取我们在 GitHub 配置的密钥
    WEBHOOK_URL = os.environ.get("WECHAT_WEBHOOK")
    
    # 填入你要监控的股票代码 (比如 sz000001 是平安银行，sh600519 是贵州茅台)
    STOCK_CODE = "sh600519" 
    
    if not WEBHOOK_URL:
        print("未找到 Webhook 地址！")
    else:
        data = get_stock_data(STOCK_CODE)
        if data:
            send_to_wechat(WEBHOOK_URL, data)
        else:
            print("获取数据失败")
