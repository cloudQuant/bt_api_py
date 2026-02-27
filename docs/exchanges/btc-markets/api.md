# BTC Markets API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V3
- 创建日期: 2026-02-27
- 官方文档: https://docs.btcmarkets.net/v3/
- 数据来源: CCXT 源码验证

## 交易所基本信息
- 官方名称: BTC Markets
- 官网: https://btcmarkets.net
- 交易所类型: CEX (中心化交易所)
- 总部: 澳大利亚
- 支持的交易对: 30+ (AUD 计价)
- 支持的交易类型: 现货(Spot)
- 法币支持: AUD (澳元)
- 特点: 澳大利亚最大的加密货币交易所

## API基础URL

| 端点类型 | URL |
|---------|-----|
| REST API | `https://api.btcmarkets.net` |

## 认证方式

### HMAC SHA512 (Base64)

**请求头**:

| Header | 描述 |
|--------|------|
| BM-AUTH-APIKEY | API Key |
| BM-AUTH-TIMESTAMP | 毫秒时间戳 |
| BM-AUTH-SIGNATURE | HMAC-SHA512 签名 (Base64) |
| Content-Type | application/json |

**签名步骤**:
1. 拼接签名字符串: `METHOD + path + nonce` (POST 还加 body JSON)
2. Secret 先 Base64 解码
3. 使用解码后的 Secret 进行 HMAC SHA512
4. 签名转为 Base64

### Python 签名示例

```python
import hmac
import hashlib
import base64
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret_base64"
BASE_URL = "https://api.btcmarkets.net"

def btcm_request(method, path, params=None):
    nonce = str(int(time.time() * 1000))
    secret = base64.b64decode(API_SECRET)

    auth = method + path + nonce
    if method == "POST" and params:
        body = json.dumps(params)
        auth += body
    else:
        body = None
        if method == "GET" and params:
            path += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    signature = base64.b64encode(
        hmac.new(secret, auth.encode(), hashlib.sha512).digest()
    ).decode()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "BM-AUTH-APIKEY": API_KEY,
        "BM-AUTH-TIMESTAMP": nonce,
        "BM-AUTH-SIGNATURE": signature,
    }

    url = BASE_URL + path
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    return resp.json()
```

## 市场数据API (GET, 无需认证)

| 端点 | 描述 |
|------|------|
| /v3/markets | 全部市场列表 |
| /v3/markets/{marketId}/ticker | 单个 Ticker |
| /v3/markets/tickers | 全部 Tickers |
| /v3/markets/{marketId}/orderbook | 订单簿 |
| /v3/markets/orderbooks | 全部订单簿 |
| /v3/markets/{marketId}/trades | 最近成交 |
| /v3/markets/{marketId}/candles | K线数据 |
| /v3/time | 服务器时间 |

**K线周期**: `1m, 1h, 1d`

```python
# Ticker
resp = requests.get(f"{BASE_URL}/v3/markets/BTC-AUD/ticker")
t = resp.json()
print(f"BTC/AUD: last={t['lastPrice']}, bid={t['bestBid']}, ask={t['bestAsk']}, "
      f"vol={t['volume24h']}")

# 订单簿
resp = requests.get(f"{BASE_URL}/v3/markets/BTC-AUD/orderbook")
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, volume={ask[1]}")
```

## 交易API (需签名)

| 端点 | 方法 | 描述 |
|------|------|------|
| /v3/orders | POST | 下单 |
| /v3/orders/{id} | DELETE | 撤单 |
| /v3/orders | DELETE | 批量撤单 |
| /v3/orders/{id} | PUT | 修改订单 |
| /v3/orders | GET | 订单列表 |
| /v3/orders/{id} | GET | 订单详情 |
| /v3/batchorders | POST | 批量下单 |
| /v3/trades | GET | 成交记录 |

```python
# 限价买单
order = btcm_request("POST", "/v3/orders", {
    "marketId": "BTC-AUD",
    "side": "Bid",
    "type": "Limit",
    "price": "50000",
    "amount": "0.001",
    "timeInForce": "GTC"
})
print(f"Order ID: {order.get('orderId')}")

# 市价卖单
order = btcm_request("POST", "/v3/orders", {
    "marketId": "BTC-AUD",
    "side": "Ask",
    "type": "Market",
    "amount": "0.001"
})

# 撤单
btcm_request("DELETE", "/v3/orders/order_id_here")
```

**订单类型**: `Limit`, `Market`, `Stop Limit`, `Take Profit`, `Stop`
**Side**: `Bid` (买), `Ask` (卖)
**TimeInForce**: `GTC`, `IOC`, `FOK`

## 账户管理API

| 端点 | 方法 | 描述 |
|------|------|------|
| accounts/me/balances | GET | 余额 |
| accounts/me/trading-fees | GET | 费率 |
| accounts/me/withdrawal-limits | GET | 提现限额 |
| accounts/me/transactions | GET | 交易记录 |
| addresses | GET | 充值地址 |
| withdrawals | POST | 提现 |
| withdrawals | GET | 提现列表 |
| deposits | GET | 充值列表 |
| withdrawal-fees | GET | 提现手续费 |
| assets | GET | 资产列表 |

## 速率限制

| 类别 | 限制 |
|------|------|
| REST API | 1 次/秒 (市场数据缓存 1-2 秒) |

## 错误处理

失败: `{"code": "ErrorCode", "message": "error description"}`

## 变更历史

### 2026-02-27
- 基于 CCXT 源码验证完善

---

## 相关资源

- [BTC Markets API 文档](https://docs.btcmarkets.net/v3/)
- [CCXT BTC Markets 实现](https://github.com/ccxt/ccxt/blob/master/python/ccxt/btcmarkets.py)

---

*本文档由 bt_api_py 项目维护。*
