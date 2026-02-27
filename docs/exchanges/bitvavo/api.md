# Bitvavo API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.bitvavo.com

## 交易所基本信息
- 官方名称: Bitvavo
- 官网: https://www.bitvavo.com
- 交易所类型: CEX (中心化交易所)
- 总部: 荷兰阿姆斯特丹
- 支持的交易对: 200+ (EUR 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.15%, Taker 0.25% (基础费率，阶梯递减)
- 法币支持: EUR
- 合规: 荷兰央行 (DNB) 注册
- 特点: 欧洲合规交易所，低费率，支持 iDEAL/SEPA 充值
- Python SDK: `pip install python-bitvavo-api`

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.bitvavo.com/v2` | 主端点 |
| WebSocket | `wss://ws.bitvavo.com/v2/` | 实时数据 |

## 认证方式

### API密钥获取

1. 登录 Bitvavo 账户
2. 进入 设置 -> API
3. 创建 API Key 并设置权限与 IP 白名单
4. 保存 API Key 和 Secret

### HMAC SHA256 签名

**签名步骤**:
1. 获取毫秒时间戳
2. 拼接签名字符串: `timestamp + method + url + body`
3. 使用 Secret 进行 HMAC SHA256 签名
4. 将签名转为十六进制

**请求头**:

| Header | 描述 |
|--------|------|
| Bitvavo-Access-Key | API Key |
| Bitvavo-Access-Signature | HMAC SHA256 签名 |
| Bitvavo-Access-Timestamp | 毫秒时间戳 |
| Bitvavo-Access-Window | 时间窗口（毫秒，可选，默认10000） |
| Content-Type | application/json |

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://api.bitvavo.com/v2"

def bitvavo_request(method, path, params=None, body=None):
    """发送 Bitvavo 签名请求"""
    timestamp = str(int(time.time() * 1000))

    # 构建 URL 路径（含查询参数）
    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url_path = f"/v2{path}?{query}"
    else:
        url_path = f"/v2{path}"

    body_str = json.dumps(body) if body else ""

    # 签名字符串: timestamp + method + url + body
    sign_str = timestamp + method.upper() + url_path + body_str
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Bitvavo-Access-Key": API_KEY,
        "Bitvavo-Access-Signature": signature,
        "Bitvavo-Access-Timestamp": timestamp,
        "Bitvavo-Access-Window": "10000",
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{path}"
    if method == "GET":
        resp = requests.get(url, headers=headers, params=params)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    elif method == "PUT":
        resp = requests.put(url, headers=headers, data=body_str)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers, params=params)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()
```

### 使用官方 Python SDK

```python
# pip install python-bitvavo-api
from python_bitvavo_api.bitvavo import Bitvavo

bitvavo = Bitvavo({
    "APIKEY": "your_api_key",
    "APISECRET": "your_api_secret",
    "RESTURL": "https://api.bitvavo.com/v2",
    "WSURL": "wss://ws.bitvavo.com/v2/",
    "ACCESSWINDOW": 10000,
})
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取市场列表

**端点**: `GET /markets`

**参数**: `market` (可选, 如 `BTC-EUR`)

```python
resp = requests.get(f"{BASE_URL}/markets")
for m in resp.json()[:5]:
    print(f"{m['market']}: status={m['status']}, "
          f"minOrderInQuoteAsset={m['minOrderInQuoteAsset']}")

# SDK
markets = bitvavo.markets({})
```

### 2. 获取 24h Ticker

**端点**: `GET /ticker/24h`

**参数**: `market` (可选)

```python
# 单个
resp = requests.get(f"{BASE_URL}/ticker/24h", params={"market": "BTC-EUR"})
t = resp.json()
print(f"BTC/EUR: last={t['last']}, high={t['high']}, low={t['low']}, "
      f"volume={t['volume']}, bid={t['bid']}, ask={t['ask']}")

# 全部
resp = requests.get(f"{BASE_URL}/ticker/24h")
print(f"Total tickers: {len(resp.json())}")

# SDK
ticker = bitvavo.tickerPrice({"market": "BTC-EUR"})
```

### 3. 获取最优报价

**端点**: `GET /ticker/book`

**参数**: `market` (可选)

```python
resp = requests.get(f"{BASE_URL}/ticker/book", params={"market": "BTC-EUR"})
bbo = resp.json()
print(f"BTC/EUR: bid={bbo['bid']}/{bbo['bidSize']}, ask={bbo['ask']}/{bbo['askSize']}")
```

### 4. 获取订单簿

**端点**: `GET /{market}/book`

**参数**: `depth` (可选, 默认全部)

```python
resp = requests.get(f"{BASE_URL}/BTC-EUR/book", params={"depth": 10})
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, qty={ask[1]}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid[0]}, qty={bid[1]}")
```

### 5. 获取最近成交

**端点**: `GET /{market}/trades`

**参数**: `limit` (可选, 默认500, 最大1000), `start`/`end` (可选, 毫秒时间戳)

```python
resp = requests.get(f"{BASE_URL}/BTC-EUR/trades", params={"limit": 10})
for t in resp.json():
    print(f"ID={t['id']} price={t['price']} amount={t['amount']} side={t['side']}")
```

### 6. 获取K线数据

**端点**: `GET /{market}/candles`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| interval | STRING | 是 | 1m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d |
| limit | INT | 否 | 数量(默认1440) |
| start | LONG | 否 | 开始时间（毫秒） |
| end | LONG | 否 | 结束时间（毫秒） |

```python
resp = requests.get(f"{BASE_URL}/BTC-EUR/candles", params={
    "interval": "1h",
    "limit": 24
})
for c in resp.json():
    # [timestamp, open, high, low, close, volume]
    print(f"T={c[0]} O={c[1]} H={c[2]} L={c[3]} C={c[4]} V={c[5]}")
```

## 交易API

> 以下端点均需签名认证。

### 1. 查询余额

**端点**: `GET /balance`

**参数**: `symbol` (可选)

```python
balances = bitvavo_request("GET", "/balance")
for b in balances:
    available = float(b["available"])
    in_order = float(b["inOrder"])
    if available > 0 or in_order > 0:
        print(f"{b['symbol']}: available={b['available']}, inOrder={b['inOrder']}")

# SDK
balance = bitvavo.balance({})
```

### 2. 下单

**端点**: `POST /order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| market | STRING | 是 | 交易对，如 BTC-EUR |
| side | STRING | 是 | buy / sell |
| orderType | STRING | 是 | limit / market / stopLoss / stopLossLimit / takeProfit / takeProfitLimit |
| amount | STRING | 条件 | 数量 |
| price | STRING | 条件 | 价格（limit 必需） |
| amountQuote | STRING | 条件 | 金额（市价买可用） |
| triggerPrice | STRING | 条件 | 触发价（止损/止盈） |
| triggerAmount | STRING | 条件 | 触发金额 |
| timeInForce | STRING | 否 | GTC / IOC / FOK |
| postOnly | BOOL | 否 | 仅 Maker |
| selfTradePrevention | STRING | 否 | decrementAndCancel / cancelOldest / cancelNewest / cancelBoth |
| responseRequired | BOOL | 否 | 是否等待完整响应 |

```python
# 限价买单
order = bitvavo_request("POST", "/order", body={
    "market": "BTC-EUR",
    "side": "buy",
    "orderType": "limit",
    "amount": "0.001",
    "price": "35000"
})
print(f"Order ID: {order.get('orderId')}")

# 市价买单（按金额）
order = bitvavo_request("POST", "/order", body={
    "market": "BTC-EUR",
    "side": "buy",
    "orderType": "market",
    "amountQuote": "100"  # 100 EUR
})

# 止损限价单
order = bitvavo_request("POST", "/order", body={
    "market": "BTC-EUR",
    "side": "sell",
    "orderType": "stopLossLimit",
    "amount": "0.001",
    "price": "33000",
    "triggerPrice": "34000"
})

# SDK
order = bitvavo.placeOrder("BTC-EUR", "buy", "limit", {
    "amount": "0.001", "price": "35000"
})
```

### 3. 修改订单

**端点**: `PUT /order`

```python
result = bitvavo_request("PUT", "/order", body={
    "market": "BTC-EUR",
    "orderId": "order_id_here",
    "amount": "0.002",
    "price": "36000"
})
```

### 4. 撤单

**端点**: `DELETE /order`

**参数**: `market` (必需), `orderId` (必需)

```python
result = bitvavo_request("DELETE", "/order", params={
    "market": "BTC-EUR",
    "orderId": "order_id_here"
})

# SDK
bitvavo.cancelOrder("BTC-EUR", "order_id_here")
```

### 5. 全部撤单

**端点**: `DELETE /orders`

**参数**: `market` (可选)

```python
result = bitvavo_request("DELETE", "/orders", params={"market": "BTC-EUR"})
```

### 6. 查询挂单

**端点**: `GET /ordersOpen`

**参数**: `market` (可选)

```python
orders = bitvavo_request("GET", "/ordersOpen", params={"market": "BTC-EUR"})
for o in orders:
    print(f"ID:{o['orderId']} {o['side']} {o['orderType']} "
          f"price={o['price']} amount={o['amount']} status={o['status']}")
```

### 7. 查询历史订单

**端点**: `GET /orders`

**参数**: `market` (必需), `limit`, `start`, `end`

### 8. 查询成交记录

**端点**: `GET /trades`

**参数**: `market` (必需), `limit`, `start`, `end`

## 账户管理API

| 端点 | 方法 | 描述 |
|------|------|------|
| /balance | GET | 查询余额 |
| /account | GET | 账户信息（费率等） |
| /deposit | GET | 充值历史 |
| /depositAddress | GET | 充值地址 |
| /withdrawal | GET | 提现历史 |
| /withdrawal | POST | 发起提现 |

```python
# 查询账户费率信息
account = bitvavo_request("GET", "/account")
print(f"Fee tier: {account}")

# 获取充值地址
addr = bitvavo_request("GET", "/depositAddress", params={"symbol": "BTC"})
print(f"BTC address: {addr.get('address')}")
```

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 权重限制 | 1000 权重/分钟 | 按 API Key（认证）或 IP（未认证） |
| WebSocket | 5000 消息/秒 | 单连接 |
| Market Data Pro WS | 50 消息/秒 | |

> 超限返回 HTTP 429，错误码 110

### 最佳实践

- 使用 WebSocket 获取实时行情
- 使用官方 Python SDK `python-bitvavo-api`
- 使用 `selfTradePrevention` 防止自成交
- 时间窗口默认 10 秒，可调整

## WebSocket支持

### 连接信息

**URL**: `wss://ws.bitvavo.com/v2/`

### 公共频道 (Channels)

| 频道 | 描述 |
|------|------|
| `ticker` | Ticker |
| `ticker24h` | 24h Ticker |
| `trades` | 实时成交 |
| `book` | 订单簿 |
| `candles` | K线 |

### 私有频道

| 频道 | 描述 |
|------|------|
| `account` | 订单和成交更新 |

### Python WebSocket 示例 (SDK)

```python
from python_bitvavo_api.bitvavo import Bitvavo

def ticker_callback(response):
    print(f"Ticker: {response}")

def trades_callback(response):
    for t in response:
        print(f"Trade: price={t['price']}, amount={t['amount']}, side={t['side']}")

def book_callback(response):
    print(f"Book: asks={len(response.get('asks', []))}, bids={len(response.get('bids', []))}")

def order_callback(response):
    print(f"Order update: {response}")

bitvavo = Bitvavo({
    "APIKEY": "your_api_key",
    "APISECRET": "your_api_secret",
})

ws = bitvavo.newWebsocket()

# 公共频道
ws.subscriptionTicker("BTC-EUR", ticker_callback)
ws.subscriptionTrades("BTC-EUR", trades_callback)
ws.subscriptionBookUpdate("BTC-EUR", book_callback)

# 私有频道（需认证）
ws.subscriptionAccount("BTC-EUR", order_callback)
```

### 原生 WebSocket 示例

```python
import websocket
import json
import hmac
import hashlib
import time

def on_message(ws, message):
    data = json.loads(message)
    event = data.get("event", "")
    if event == "trade":
        for t in data.get("data", []):
            print(f"Trade {data['market']}: price={t['price']}, amount={t['amount']}")
    elif event == "book":
        print(f"Book {data['market']}: asks={len(data.get('asks', []))}, "
              f"bids={len(data.get('bids', []))}")
    elif event == "ticker":
        print(f"Ticker {data['market']}: last={data.get('lastPrice')}")

def on_open(ws):
    # 订阅
    ws.send(json.dumps({
        "action": "subscribe",
        "channels": [
            {"name": "ticker", "markets": ["BTC-EUR"]},
            {"name": "trades", "markets": ["BTC-EUR"]},
            {"name": "book", "markets": ["BTC-EUR"]}
        ]
    }))

ws = websocket.WebSocketApp(
    "wss://ws.bitvavo.com/v2/",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)
```

## 错误代码

### 常见错误码

| 错误码 | 描述 |
|--------|------|
| 100 | 无效 JSON |
| 101 | 必填字段缺失 |
| 102 | 参数值无效 |
| 103 | 市场不存在 |
| 104 | 不支持的操作 |
| 105 | 未认证 |
| 106 | 签名无效 |
| 107 | 余额不足 |
| 108 | 订单不存在 |
| 109 | 最小下单量不满足 |
| 110 | 速率限制 |
| 200 | 内部错误 |
| 201 | 正在维护 |

### Python 错误处理

```python
def safe_bitvavo_request(method, path, **kwargs):
    try:
        result = bitvavo_request(method, path, **kwargs)
        if isinstance(result, dict) and "errorCode" in result:
            print(f"Bitvavo Error [{result['errorCode']}]: {result.get('error', 'Unknown')}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 V2 REST API 端点说明
- 添加 HMAC SHA256 签名认证完整 Python 示例
- 添加官方 Python SDK 使用示例
- 添加市场数据 API（Ticker、订单簿、成交、K线）详细说明
- 添加交易 API（下单、改单、撤单、查询）完整示例
- 添加 WebSocket (SDK + 原生) 订阅示例
- 添加错误代码表和错误处理

---

## 相关资源

- [Bitvavo API 文档](https://docs.bitvavo.com)
- [Bitvavo Python SDK](https://github.com/bitvavo/python-bitvavo-api)
- [Bitvavo 官网](https://www.bitvavo.com)
- [CCXT Bitvavo 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Bitvavo 官方 API V2 文档整理。*
