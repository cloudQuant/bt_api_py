# Bitstamp API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://www.bitstamp.net/api/
- Sandbox: https://sandbox.bitstamp.net/api/v2

## 交易所基本信息
- 官方名称: Bitstamp
- 官网: https://www.bitstamp.net
- 交易所类型: CEX (中心化交易所)
- 总部: 卢森堡（欧洲最老牌加密货币交易所之一，成立于2011年）
- 支持的交易对: 80+ (USD, EUR, GBP 等法币计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.30%, Taker 0.40% (阶梯费率，最低 0.00%/0.03%)
- 法币支持: USD, EUR, GBP
- 合规: 卢森堡金融监管委员会 (CSSF) 持牌

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://www.bitstamp.net/api/v2` | 主端点 |
| REST API (Sandbox) | `https://sandbox.bitstamp.net/api/v2` | 沙盒测试 |
| WebSocket v2 | `wss://ws.bitstamp.net` | 实时数据 |

## 认证方式

### API密钥获取

1. 登录 Bitstamp 账户
2. 进入 设置 -> API 访问
3. 创建 API Key 并设置权限（账户余额、交易、提现等）
4. 设置 IP 白名单（推荐）
5. 保存 API Key 和 Secret

### HMAC SHA256 签名 (Header Auth)

**请求头**:

| Header | 描述 |
|--------|------|
| X-Auth | `BITSTAMP {api_key}` |
| X-Auth-Signature | HMAC SHA256 签名 |
| X-Auth-Nonce | 36位唯一字符串（UUID，150秒内不可复用） |
| X-Auth-Timestamp | 毫秒级时间戳 |
| X-Auth-Version | `v2` |
| Content-Type | `application/x-www-form-urlencoded`（有 body 时） |

**签名步骤**:
1. 生成 nonce（36位 UUID）
2. 获取毫秒时间戳
3. 拼接签名字符串: `BITSTAMP {api_key}` + `{METHOD}` + `{host}` + `{path}` + `{query}` + `{content_type}` + `{nonce}` + `{timestamp}` + `v2` + `{body}`
4. 使用 Secret 进行 HMAC SHA256 签名

> 若请求无 body，请移除 Content-Type 头并从签名字符串中移除

### Python 签名示例

```python
import hmac
import hashlib
import time
import uuid
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://www.bitstamp.net"

def bitstamp_request(method, path, params=None):
    """发送 Bitstamp 签名请求"""
    timestamp = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    content_type = ""
    body = ""

    if params and method == "POST":
        content_type = "application/x-www-form-urlencoded"
        body = "&".join(f"{k}={v}" for k, v in params.items())

    # 构建签名字符串
    message = (
        f"BITSTAMP {API_KEY}"
        f"{method}"
        f"www.bitstamp.net"
        f"{path}"
        f""  # query string (empty for POST)
        f"{content_type}"
        f"{nonce}"
        f"{timestamp}"
        f"v2"
        f"{body}"
    )

    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-Auth": f"BITSTAMP {API_KEY}",
        "X-Auth-Signature": signature,
        "X-Auth-Nonce": nonce,
        "X-Auth-Timestamp": timestamp,
        "X-Auth-Version": "v2",
    }

    if content_type:
        headers["Content-Type"] = content_type

    url = f"{BASE_URL}{path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    else:
        resp = requests.post(url, headers=headers, data=body)

    return resp.json()
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取 Ticker

**端点**: `GET /api/v2/ticker/{currency_pair}/`

```python
resp = requests.get("https://www.bitstamp.net/api/v2/ticker/btcusd/")
ticker = resp.json()
print(f"BTC/USD: Last={ticker['last']}, Bid={ticker['bid']}, Ask={ticker['ask']}")
print(f"High={ticker['high']}, Low={ticker['low']}, Volume={ticker['volume']}")
print(f"VWAP={ticker['vwap']}, Open={ticker['open']}")
```

**响应示例**:
```json
{
  "high": "45000.00",
  "last": "44500.50",
  "timestamp": "1640000000",
  "bid": "44500.00",
  "vwap": "44200.00",
  "volume": "1234.56789",
  "low": "43000.00",
  "ask": "44501.00",
  "open": "44000.00",
  "open_24": "43500.00",
  "percent_change_24": "2.30"
}
```

### 2. 获取每小时 Ticker

**端点**: `GET /api/v2/ticker_hour/{currency_pair}/`

### 3. 获取订单簿

**端点**: `GET /api/v2/order_book/{currency_pair}/`

**参数**: `group` (可选, 0=不聚合, 1=聚合, 2=按订单)

```python
resp = requests.get("https://www.bitstamp.net/api/v2/order_book/btcusd/")
book = resp.json()
print(f"Timestamp: {book['timestamp']}")
for ask in book['asks'][:5]:
    print(f"ASK: price={ask[0]}, qty={ask[1]}")
for bid in book['bids'][:5]:
    print(f"BID: price={bid[0]}, qty={bid[1]}")
```

**响应示例**:
```json
{
  "timestamp": "1640000000",
  "microtimestamp": "1640000000000000",
  "bids": [["44500.00", "0.50000000"], ["44499.00", "1.20000000"]],
  "asks": [["44501.00", "0.30000000"], ["44502.00", "0.80000000"]]
}
```

### 4. 获取最近成交

**端点**: `GET /api/v2/transactions/{currency_pair}/`

**参数**: `time` (可选, `minute`/`hour`/`day`, 默认 `hour`)

```python
resp = requests.get("https://www.bitstamp.net/api/v2/transactions/btcusd/", params={"time": "hour"})
for trade in resp.json()[:5]:
    side = "buy" if trade['type'] == '0' else "sell"
    print(f"{side}: price={trade['price']}, amount={trade['amount']}, tid={trade['tid']}")
```

### 5. 获取交易对信息

**端点**: `GET /api/v2/trading-pairs-info/`

```python
resp = requests.get("https://www.bitstamp.net/api/v2/trading-pairs-info/")
for pair in resp.json()[:5]:
    print(f"{pair['name']}: min_order={pair['minimum_order']}, trading={pair['trading']}")
```

### 6. 获取OHLC数据

**端点**: `GET /api/v2/ohlc/{currency_pair}/`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| step | INT | 是 | 时间步长(秒): 60,180,300,900,1800,3600,7200,14400,21600,43200,86400,259200 |
| limit | INT | 是 | 返回数量(最大1000) |
| start | INT | 否 | 开始时间戳 |
| end | INT | 否 | 结束时间戳 |

```python
resp = requests.get("https://www.bitstamp.net/api/v2/ohlc/btcusd/", params={
    "step": 3600,  # 1小时
    "limit": 24
})
data = resp.json()
for candle in data['data']['ohlc']:
    print(f"T={candle['timestamp']} O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']} V={candle['volume']}")
```

### 7. 获取市场列表

**端点**: `GET /api/v2/markets/`

### 8. 获取币种信息

**端点**: `GET /api/v2/currencies/`

## 交易API

> 以下端点均需签名认证。

### 1. 查询余额

**端点**: `POST /api/v2/balance/`

```python
balances = bitstamp_request("POST", "/api/v2/balance/")
for key, value in balances.items():
    if key.endswith("_balance") and float(value) > 0:
        currency = key.replace("_balance", "").upper()
        available = balances.get(f"{key.replace('_balance', '')}_available", "0")
        print(f"{currency}: balance={value}, available={available}")
```

### 2. 限价买单

**端点**: `POST /api/v2/buy/{currency_pair}/`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| amount | DECIMAL | 是 | 数量 |
| price | DECIMAL | 是 | 价格 |
| limit_price | DECIMAL | 否 | 限价（止损用） |
| daily_order | BOOL | 否 | 是否为当日订单 |
| ioc_order | BOOL | 否 | IOC 订单 |
| fok_order | BOOL | 否 | FOK 订单 |
| client_order_id | STRING | 否 | 客户端订单ID |

```python
order = bitstamp_request("POST", "/api/v2/buy/btcusd/", {
    "amount": "0.001",
    "price": "40000"
})
print(f"Order ID: {order.get('id')}, Status: {order.get('status')}")
```

### 3. 限价卖单

**端点**: `POST /api/v2/sell/{currency_pair}/`

```python
order = bitstamp_request("POST", "/api/v2/sell/btcusd/", {
    "amount": "0.001",
    "price": "50000"
})
```

### 4. 市价买单

**端点**: `POST /api/v2/buy/market/{currency_pair}/`

```python
order = bitstamp_request("POST", "/api/v2/buy/market/btcusd/", {
    "amount": "0.001"
})
```

### 5. 市价卖单

**端点**: `POST /api/v2/sell/market/{currency_pair}/`

### 6. 即时买单（按金额）

**端点**: `POST /api/v2/buy/instant/{currency_pair}/`

```python
# 用 100 USD 市价买入 BTC
order = bitstamp_request("POST", "/api/v2/buy/instant/btcusd/", {
    "amount": "100"  # USD 金额
})
```

### 7. 撤单

**端点**: `POST /api/v2/cancel_order/`

```python
result = bitstamp_request("POST", "/api/v2/cancel_order/", {
    "id": "12345678"
})
```

### 8. 全部撤单

**端点**: `POST /api/v2/cancel_all_orders/`

### 9. 查询订单状态

**端点**: `POST /api/v2/order_status/`

```python
status = bitstamp_request("POST", "/api/v2/order_status/", {
    "id": "12345678"
})
print(f"Status: {status.get('status')}, Transactions: {len(status.get('transactions', []))}")
```

### 10. 查询未成交订单

**端点**: `POST /api/v2/open_orders/all/` 或 `POST /api/v2/open_orders/{currency_pair}/`

```python
orders = bitstamp_request("POST", "/api/v2/open_orders/all/")
for o in orders:
    print(f"ID:{o['id']} {o['type_str']} price={o['price']} amount={o['amount']}")
```

### 11. 查询成交记录

**端点**: `POST /api/v2/user_transactions/{currency_pair}/`

**参数**: `offset`, `limit` (最大1000), `sort` (`asc`/`desc`)

## 账户管理API

### 1. 获取充值地址

**端点**: `POST /api/v2/{currency}_address/`（如 `/api/v2/btc_address/`）

### 2. 提现

**端点**: `POST /api/v2/{currency}_withdrawal/`

### 3. 提现状态

**端点**: `POST /api/v2/withdrawal-requests/`

### 4. 转账历史

**端点**: `POST /api/v2/crypto-transactions/`

### 5. 获取 WebSocket Token

**端点**: `POST /api/v2/websockets_token/`

```python
token_data = bitstamp_request("POST", "/api/v2/websockets_token/")
ws_token = token_data.get("token")
print(f"WS Token: {ws_token}")
```

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 全局 | 400 次/秒 | 按 IP |
| 滑动窗口 | 10,000 次/10分钟 | 按 IP |

### 最佳实践

- 使用 WebSocket 获取实时行情，减少 REST 轮询
- 生产环境设置 IP 白名单
- 使用沙盒环境进行测试: `https://sandbox.bitstamp.net`
- nonce 使用 UUID v4，保证唯一性

## WebSocket支持

### 连接信息

**WebSocket URL**: `wss://ws.bitstamp.net`

### 公共频道

| 频道 | 说明 |
|------|------|
| `live_trades_{pair}` | 实时成交 |
| `order_book_{pair}` | 订单簿快照 |
| `diff_order_book_{pair}` | 订单簿增量 |
| `live_orders_{pair}` | 实时订单（需认证） |
| `detail_order_book_{pair}` | 详细订单簿 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    event = data.get("event", "")
    channel = data.get("channel", "")

    if event == "trade":
        trade = json.loads(data["data"])
        side = "buy" if trade["type"] == 0 else "sell"
        print(f"Trade: {side} price={trade['price']} amount={trade['amount']}")
    elif event == "data" and "order_book" in channel:
        book = json.loads(data["data"])
        print(f"OrderBook: bids={len(book['bids'])}, asks={len(book['asks'])}")

def on_open(ws):
    # 订阅成交
    ws.send(json.dumps({
        "event": "bts:subscribe",
        "data": {"channel": "live_trades_btcusd"}
    }))
    # 订阅订单簿
    ws.send(json.dumps({
        "event": "bts:subscribe",
        "data": {"channel": "order_book_btcusd"}
    }))

ws = websocket.WebSocketApp(
    "wss://ws.bitstamp.net",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()
```

### 私有频道 (需认证)

```python
# 先获取 WebSocket token
token_data = bitstamp_request("POST", "/api/v2/websockets_token/")
ws_token = token_data["token"]
user_id = token_data["user_id"]

def on_open_private(ws):
    # 订阅私有订单频道
    ws.send(json.dumps({
        "event": "bts:subscribe",
        "data": {
            "channel": f"private-my_orders_btcusd-{user_id}",
            "auth": ws_token
        }
    }))
```

## 错误代码

### HTTP 错误

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 403 | 认证失败/权限不足 |
| 404 | 端点不存在 |
| 429 | 速率限制 |

### API 错误

| 错误码 | 描述 |
|--------|------|
| API0001 | 参数缺失 |
| API0002 | 参数无效 |
| API0004 | 交易对无效 |
| API0005 | 金额低于最小值 |
| API0006 | 余额不足 |
| API0010 | 权限不足 |
| API0011 | API Key 未找到 |
| API0013 | Nonce 已使用 |
| API0015 | 签名无效 |
| API0021 | 时间戳过期 |

### Python 错误处理

```python
def safe_bitstamp_request(method, path, params=None):
    """带错误处理的 Bitstamp 请求"""
    try:
        result = bitstamp_request(method, path, params)
        if isinstance(result, dict) and "error" in result:
            print(f"API Error: {result['error']}")
            return None
        if isinstance(result, dict) and "status" in result and result["status"] == "error":
            print(f"Error: {result.get('reason', {})}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 代码示例

### Python 完整交易示例

```python
import hmac
import hashlib
import time
import uuid
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://www.bitstamp.net"

def bitstamp_auth_request(method, path, params=None):
    timestamp = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    content_type = ""
    body = ""
    if params and method == "POST":
        content_type = "application/x-www-form-urlencoded"
        body = "&".join(f"{k}={v}" for k, v in params.items())
    msg = f"BITSTAMP {API_KEY}{method}www.bitstamp.net{path}{content_type}{nonce}{timestamp}v2{body}"
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Auth": f"BITSTAMP {API_KEY}", "X-Auth-Signature": sig,
               "X-Auth-Nonce": nonce, "X-Auth-Timestamp": timestamp, "X-Auth-Version": "v2"}
    if content_type:
        headers["Content-Type"] = content_type
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, headers=headers).json()
    return requests.post(url, headers=headers, data=body).json()

# ===== 公共接口 =====

# Ticker
ticker = requests.get(f"{BASE_URL}/api/v2/ticker/btcusd/").json()
print(f"BTC/USD: Last={ticker['last']}, Bid={ticker['bid']}, Ask={ticker['ask']}")

# 订单簿
book = requests.get(f"{BASE_URL}/api/v2/order_book/btcusd/").json()
print(f"Bids: {len(book['bids'])}, Asks: {len(book['asks'])}")

# 最近成交
trades = requests.get(f"{BASE_URL}/api/v2/transactions/btcusd/", params={"time": "minute"}).json()
for t in trades[:3]:
    print(f"Trade: price={t['price']}, amount={t['amount']}")

# OHLC
ohlc = requests.get(f"{BASE_URL}/api/v2/ohlc/btcusd/", params={"step": 3600, "limit": 5}).json()
for c in ohlc['data']['ohlc']:
    print(f"O={c['open']} H={c['high']} L={c['low']} C={c['close']}")

# ===== 私有接口 =====

# 查询余额
balances = bitstamp_auth_request("POST", "/api/v2/balance/")
for k, v in balances.items():
    if k.endswith("_available") and float(v) > 0:
        print(f"{k}: {v}")

# 限价买单
order = bitstamp_auth_request("POST", "/api/v2/buy/btcusd/", {
    "amount": "0.001", "price": "40000"
})
if "id" in order:
    print(f"Order placed: {order['id']}")

    # 查询订单
    status = bitstamp_auth_request("POST", "/api/v2/order_status/", {"id": order["id"]})
    print(f"Status: {status.get('status')}")

    # 撤单
    bitstamp_auth_request("POST", "/api/v2/cancel_order/", {"id": order["id"]})
    print("Order cancelled")

# 查询所有挂单
open_orders = bitstamp_auth_request("POST", "/api/v2/open_orders/all/")
print(f"Open orders: {len(open_orders)}")
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC SHA256 Header Auth 签名完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、OHLC）详细说明和响应示例
- 添加交易 API（限价、市价、即时、撤单、查询）完整示例
- 添加 WebSocket v2 公共和私有频道订阅示例
- 添加错误代码表和错误处理
- 添加 Sandbox 测试环境信息

---

## 相关资源

- [Bitstamp API 文档](https://www.bitstamp.net/api/)
- [Bitstamp WebSocket 文档](https://www.bitstamp.net/websocket/v2/)
- [Bitstamp 官网](https://www.bitstamp.net)
- [CCXT Bitstamp 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Bitstamp 官方 API 文档整理。*
