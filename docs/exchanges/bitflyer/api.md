# bitFlyer API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v1 (bitFlyer Lightning)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://lightning.bitflyer.com/docs
- 官方文档(EN): https://lightning.bitflyer.com/docs?lang=en

## 交易所基本信息
- 官方名称: bitFlyer
- 官网: https://bitflyer.com
- 交易所类型: CEX (中心化交易所)
- 总部: 日本（东京），同时运营欧洲和美国市场
- 支持的交易对: 10+ (以 JPY 计价为主，也支持 USD/EUR)
- 支持的交易类型: 现货(Spot)、期货(Futures)、FX (Lightning FX)
- 手续费: Maker 0.15%, Taker 0.15% (Lightning 现货); Lightning FX 免手续费
- 法币支持: JPY, USD, EUR
- 合规: 日本金融厅 (FSA) 持牌交易所
- Python SDK: `pip install pybitflyer`

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.bitflyer.com` | 主端点（日本） |
| REST API (US) | `https://api.bitflyer.com` | 美国市场共用 |
| REST API (EU) | `https://api.bitflyer.com` | 欧洲市场共用 |
| WebSocket (Realtime) | `wss://ws.lightstream.bitflyer.com/json-rpc` | JSON-RPC 2.0 |

## 认证方式

### API密钥获取

1. 注册 bitFlyer 账户并完成身份认证
2. 登录 bitFlyer Lightning
3. 进入 API 页面创建新的 API Key
4. 设置权限（资产信息、交易、提现等）
5. 保存 API Key 和 API Secret

### HMAC-SHA256 签名

**签名步骤**:
1. 获取当前 UNIX 时间戳（秒）
2. 拼接签名字符串: `timestamp + HTTP_METHOD + request_path + request_body`
3. 使用 API Secret 对签名字符串进行 HMAC-SHA256 哈希
4. 将结果转为十六进制字符串

**请求头**:

| Header | 描述 |
|--------|------|
| ACCESS-KEY | API Key |
| ACCESS-TIMESTAMP | UNIX 时间戳（秒） |
| ACCESS-SIGN | HMAC-SHA256 签名（十六进制） |
| Content-Type | application/json（POST 请求） |

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://api.bitflyer.com"

def signed_request(method, path, body=None):
    """发送签名请求"""
    timestamp = str(time.time())
    body_str = ""
    if body is not None:
        body_str = json.dumps(body)

    # 签名: timestamp + method + path + body
    text = timestamp + method + path + body_str
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        text.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-SIGN": signature,
        "Content-Type": "application/json",
    }

    url = BASE_URL + path
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    else:
        raise ValueError(f"Unsupported method: {method}")

    return resp.json()

# 测试：查询余额
result = signed_request("GET", "/v1/me/getbalance")
print(result)
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取市场列表

**端点**: `GET /v1/getmarkets`

**描述**: 获取所有可交易的市场（产品）列表。

```python
resp = requests.get(f"{BASE_URL}/v1/getmarkets")
markets = resp.json()
for market in markets:
    print(f"{market['product_code']}: alias={market.get('alias')}")
```

**响应示例**:
```json
[
  {"product_code": "BTC_JPY", "market_type": "Spot"},
  {"product_code": "ETH_JPY", "market_type": "Spot"},
  {"product_code": "FX_BTC_JPY", "market_type": "FX"},
  {"product_code": "ETH_BTC", "market_type": "Spot"},
  {"product_code": "BCH_BTC", "market_type": "Spot"},
  {"product_code": "BTCJPY_MAT3M", "alias": "BTCJPY_MAT3M", "market_type": "Futures"}
]
```

**其他市场端点**:
- `GET /v1/getmarkets/usa` - 美国市场
- `GET /v1/getmarkets/eu` - 欧洲市场

### 2. 获取 Ticker

**端点**: `GET /v1/ticker`

**参数**: `product_code` (必需) - 如 `BTC_JPY`, `FX_BTC_JPY`

```python
resp = requests.get(f"{BASE_URL}/v1/ticker", params={"product_code": "BTC_JPY"})
ticker = resp.json()
print(f"BTC/JPY: Last={ticker['ltp']}, Bid={ticker['best_bid']}, Ask={ticker['best_ask']}")
print(f"Volume={ticker['volume']}, Volume by product={ticker['volume_by_product']}")
```

**响应示例**:
```json
{
  "product_code": "BTC_JPY",
  "state": "RUNNING",
  "timestamp": "2024-01-01T00:00:00.000",
  "tick_id": 12345678,
  "best_bid": 9750000,
  "best_ask": 9750100,
  "best_bid_size": 0.5,
  "best_ask_size": 0.3,
  "total_bid_depth": 1234.56,
  "total_ask_depth": 1234.56,
  "market_bid_size": 0,
  "market_ask_size": 0,
  "ltp": 9750050,
  "volume": 12345.678,
  "volume_by_product": 5678.901
}
```

### 3. 获取订单簿

**端点**: `GET /v1/board`

**参数**: `product_code` (必需)

```python
resp = requests.get(f"{BASE_URL}/v1/board", params={"product_code": "BTC_JPY"})
board = resp.json()
print(f"Mid price: {board['mid_price']}")
for ask in board['asks'][:5]:
    print(f"ASK: price={ask['price']}, size={ask['size']}")
for bid in board['bids'][:5]:
    print(f"BID: price={bid['price']}, size={bid['size']}")
```

**响应示例**:
```json
{
  "mid_price": 9750050,
  "bids": [
    {"price": 9750000, "size": 0.5},
    {"price": 9749900, "size": 1.2}
  ],
  "asks": [
    {"price": 9750100, "size": 0.3},
    {"price": 9750200, "size": 0.8}
  ]
}
```

### 4. 获取成交记录

**端点**: `GET /v1/executions`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_code | STRING | 是 | 交易对 |
| count | INT | 否 | 返回数量（默认100，最大500） |
| before | INT | 否 | 返回 ID 小于此值的记录 |
| after | INT | 否 | 返回 ID 大于此值的记录 |

```python
resp = requests.get(f"{BASE_URL}/v1/executions", params={
    "product_code": "BTC_JPY",
    "count": 10
})
for trade in resp.json():
    print(f"ID:{trade['id']} {trade['side']} price={trade['price']} size={trade['size']} time={trade['exec_date']}")
```

### 5. 获取板状态

**端点**: `GET /v1/getboardstate`

**参数**: `product_code` (必需)

```python
resp = requests.get(f"{BASE_URL}/v1/getboardstate", params={"product_code": "BTC_JPY"})
state = resp.json()
print(f"Health: {state['health']}, State: {state['state']}")
# health: NORMAL, BUSY, VERY BUSY, SUPER BUSY, NO ORDER, STOP
# state: RUNNING, CLOSED, STARTING, PREOPEN, CIRCUIT BREAK, AWAITING SQ, MATURED
```

### 6. 获取交易所健康状态

**端点**: `GET /v1/gethealth`

**参数**: `product_code` (可选)

## 交易API

> 以下端点均需 HMAC-SHA256 签名认证。

### 1. 查询余额

**端点**: `GET /v1/me/getbalance`

```python
balances = signed_request("GET", "/v1/me/getbalance")
for b in balances:
    if b['amount'] > 0:
        print(f"{b['currency_code']}: amount={b['amount']}, available={b['available']}")
```

**响应示例**:
```json
[
  {"currency_code": "JPY", "amount": 1000000, "available": 900000},
  {"currency_code": "BTC", "amount": 0.5, "available": 0.45},
  {"currency_code": "ETH", "amount": 10.0, "available": 10.0}
]
```

### 2. 下单 (Child Order)

**端点**: `POST /v1/me/sendchildorder`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_code | STRING | 是 | 交易对，如 BTC_JPY |
| child_order_type | ENUM | 是 | LIMIT / MARKET |
| side | ENUM | 是 | BUY / SELL |
| price | NUMBER | 条件 | 价格（LIMIT 必需） |
| size | NUMBER | 是 | 数量 |
| minute_to_expire | INT | 否 | 订单有效分钟数（默认43200=30天） |
| time_in_force | ENUM | 否 | GTC (默认) / IOC / FOK |

**Python示例**:
```python
# 限价买单
order = signed_request("POST", "/v1/me/sendchildorder", {
    "product_code": "BTC_JPY",
    "child_order_type": "LIMIT",
    "side": "BUY",
    "price": 9500000,
    "size": 0.001,
    "time_in_force": "GTC"
})
print(f"Order ID: {order['child_order_acceptance_id']}")

# 市价卖单
order = signed_request("POST", "/v1/me/sendchildorder", {
    "product_code": "BTC_JPY",
    "child_order_type": "MARKET",
    "side": "SELL",
    "size": 0.001
})
print(f"Market order: {order['child_order_acceptance_id']}")
```

**响应示例**:
```json
{
  "child_order_acceptance_id": "JRF20240101-000001-123456"
}
```

### 3. 撤单

**端点**: `POST /v1/me/cancelchildorder`

**参数**: `product_code` (必需), `child_order_id` 或 `child_order_acceptance_id` (二选一)

```python
result = signed_request("POST", "/v1/me/cancelchildorder", {
    "product_code": "BTC_JPY",
    "child_order_acceptance_id": "JRF20240101-000001-123456"
})
# 成功返回空响应 (HTTP 200)
```

### 4. 特殊订单 (Parent Order)

**端点**: `POST /v1/me/sendparentorder`

**描述**: 支持 IFD (If Done)、OCO、IFD-OCO 等复合订单。

**order_method 枚举**:
- `SIMPLE` - 普通单条件订单
- `IFD` - If Done (成交后触发)
- `OCO` - One Cancels the Other
- `IFDOCO` - IFD + OCO 组合

```python
# OCO 订单示例：止盈 + 止损
parent_order = signed_request("POST", "/v1/me/sendparentorder", {
    "order_method": "OCO",
    "minute_to_expire": 10080,
    "parameters": [
        {
            "product_code": "BTC_JPY",
            "condition_type": "LIMIT",
            "side": "SELL",
            "price": 10000000,
            "size": 0.001
        },
        {
            "product_code": "BTC_JPY",
            "condition_type": "STOP",
            "side": "SELL",
            "trigger_price": 9000000,
            "size": 0.001
        }
    ]
})
print(f"Parent order: {parent_order['parent_order_acceptance_id']}")
```

### 5. 全部撤单

**端点**: `POST /v1/me/cancelallchildorders`

**参数**: `product_code` (必需)

```python
signed_request("POST", "/v1/me/cancelallchildorders", {
    "product_code": "BTC_JPY"
})
```

### 6. 查询活跃订单

**端点**: `GET /v1/me/getchildorders`

**参数**: `product_code` (必需), `child_order_state` (可选: ACTIVE/COMPLETED/CANCELED/EXPIRED/REJECTED), `count`, `before`, `after`

```python
orders = signed_request("GET", "/v1/me/getchildorders?product_code=BTC_JPY&child_order_state=ACTIVE")
for o in orders:
    print(f"ID:{o['child_order_id']} {o['side']} {o['child_order_type']} "
          f"price={o['price']} size={o['size']} status={o['child_order_state']}")
```

### 7. 查询成交记录

**端点**: `GET /v1/me/getexecutions`

**参数**: `product_code` (必需), `child_order_id` 或 `child_order_acceptance_id` (可选), `count`, `before`, `after`

### 8. 查询持仓 (FX/Futures)

**端点**: `GET /v1/me/getpositions`

**参数**: `product_code` (必需, 如 `FX_BTC_JPY`)

```python
positions = signed_request("GET", "/v1/me/getpositions?product_code=FX_BTC_JPY")
for pos in positions:
    print(f"{pos['side']} size={pos['size']} price={pos['price']} pnl={pos['pnl']}")
```

## 账户管理API

### 1. 查询权限

**端点**: `GET /v1/me/getpermissions`

```python
perms = signed_request("GET", "/v1/me/getpermissions")
print(f"Permissions: {perms}")
# 例如: ["/v1/me/getbalance", "/v1/me/sendchildorder", ...]
```

### 2. 查询保证金状态

**端点**: `GET /v1/me/getcollateral`

```python
collateral = signed_request("GET", "/v1/me/getcollateral")
print(f"Collateral: {collateral['collateral']}")
print(f"Open PL: {collateral['open_position_pnl']}")
print(f"Required: {collateral['require_collateral']}")
print(f"Keep rate: {collateral['keep_rate']}")
```

### 3. 充值地址

**端点**: `GET /v1/me/getaddresses`

### 4. 充值历史

**端点**: `GET /v1/me/getcoinins`

### 5. 提币历史

**端点**: `GET /v1/me/getcoinouts`

### 6. 银行账户信息

**端点**: `GET /v1/me/getbankaccounts`

### 7. 日元充值历史

**端点**: `GET /v1/me/getdeposits`

### 8. 日元提现历史

**端点**: `GET /v1/me/getwithdrawals`

### 9. 发起提现

**端点**: `POST /v1/me/sendcoin`

**参数**: `currency_code`, `amount`, `address`, `additional_fee` (可选)

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| Private API | 约 500 次/分钟 | 按 IP 地址限制 |
| Private API (交易) | 约 300 次/分钟 | 下单/撤单 |
| Public API | 约 500 次/分钟 | 行情数据 |
| HTTP API 整体 | 最大并发连接有限 | 建议使用连接池 |

### 最佳实践

- 使用 WebSocket 获取实时行情，减少 REST 轮询
- 实现指数退避重试策略
- 监控 `gethealth` 端点判断市场状态
- 市场状态为 `SUPER BUSY` 或 `STOP` 时避免发送交易请求

```python
import time

class BitflyerRateLimiter:
    def __init__(self, max_per_minute=450):
        self.interval = 60.0 / max_per_minute
        self.last_request = 0

    def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_request = time.time()
```

## WebSocket支持

### 连接信息

**WebSocket URL**: `wss://ws.lightstream.bitflyer.com/json-rpc`

**协议**: JSON-RPC 2.0

### 支持的频道

| 频道 | 说明 |
|------|------|
| `lightning_board_snapshot_{product_code}` | 订单簿快照 |
| `lightning_board_{product_code}` | 订单簿增量更新 |
| `lightning_ticker_{product_code}` | Ticker |
| `lightning_executions_{product_code}` | 成交 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    method = data.get("method")
    params = data.get("params", {})
    channel = params.get("channel", "")
    msg = params.get("message", "")

    if "ticker" in channel:
        print(f"Ticker: ltp={msg.get('ltp')}, bid={msg.get('best_bid')}, ask={msg.get('best_ask')}")
    elif "executions" in channel:
        for trade in msg:
            print(f"Trade: {trade['side']} price={trade['price']} size={trade['size']}")
    elif "board_snapshot" in channel:
        print(f"Board snapshot: mid={msg.get('mid_price')}, asks={len(msg.get('asks', []))}, bids={len(msg.get('bids', []))}")

def on_open(ws):
    # 订阅 Ticker
    ws.send(json.dumps({
        "method": "subscribe",
        "params": {"channel": "lightning_ticker_BTC_JPY"}
    }))
    # 订阅成交
    ws.send(json.dumps({
        "method": "subscribe",
        "params": {"channel": "lightning_executions_BTC_JPY"}
    }))
    # 订阅订单簿快照
    ws.send(json.dumps({
        "method": "subscribe",
        "params": {"channel": "lightning_board_snapshot_BTC_JPY"}
    }))

ws = websocket.WebSocketApp(
    "wss://ws.lightstream.bitflyer.com/json-rpc",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()
```

## 错误代码

### HTTP 错误码

| HTTP 状态码 | 描述 |
|------------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 认证失败 |
| 403 | 权限不足 |
| 404 | 端点不存在 |
| 429 | 请求过于频繁 |
| 500 | 服务器内部错误 |

### 订单相关错误

| 错误描述 | 说明 |
|---------|------|
| Insufficient funds | 余额不足 |
| Invalid order | 订单参数无效 |
| Order not found | 订单不存在 |
| Market is closed | 市场已关闭 |
| Circuit break | 熔断中 |

### 市场状态说明

| 状态 | 描述 |
|------|------|
| NORMAL | 正常 |
| BUSY | 繁忙 |
| VERY BUSY | 非常繁忙 |
| SUPER BUSY | 极度繁忙 |
| NO ORDER | 无法下单 |
| STOP | 停止交易 |

### Python 错误处理

```python
def safe_request(method, path, body=None):
    """带错误处理的请求"""
    try:
        timestamp = str(time.time())
        body_str = json.dumps(body) if body else ""
        text = timestamp + method + path + body_str
        signature = hmac.new(API_SECRET.encode(), text.encode(), hashlib.sha256).hexdigest()

        headers = {
            "ACCESS-KEY": API_KEY,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-SIGN": signature,
            "Content-Type": "application/json",
        }

        url = BASE_URL + path
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=10)
        else:
            resp = requests.post(url, headers=headers, data=body_str, timeout=10)

        if resp.status_code == 200:
            return resp.json() if resp.text else None
        elif resp.status_code == 429:
            print("Rate limited, waiting 5s...")
            time.sleep(5)
            return safe_request(method, path, body)
        elif resp.status_code == 401:
            print("Authentication failed, check API key/secret")
        else:
            print(f"HTTP {resp.status_code}: {resp.text}")

        return None

    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        return None
```

## 代码示例

### Python 完整交易示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://api.bitflyer.com"

def signed_request(method, path, body=None):
    timestamp = str(time.time())
    body_str = json.dumps(body) if body else ""
    text = timestamp + method + path + body_str
    sig = hmac.new(API_SECRET.encode(), text.encode(), hashlib.sha256).hexdigest()
    headers = {"ACCESS-KEY": API_KEY, "ACCESS-TIMESTAMP": timestamp,
               "ACCESS-SIGN": sig, "Content-Type": "application/json"}
    url = BASE_URL + path
    if method == "GET":
        return requests.get(url, headers=headers).json()
    return requests.post(url, headers=headers, data=body_str).json()

# ===== 公共接口 =====

def get_markets():
    return requests.get(f"{BASE_URL}/v1/getmarkets").json()

def get_ticker(product_code):
    return requests.get(f"{BASE_URL}/v1/ticker", params={"product_code": product_code}).json()

def get_board(product_code):
    return requests.get(f"{BASE_URL}/v1/board", params={"product_code": product_code}).json()

def get_executions(product_code, count=100):
    return requests.get(f"{BASE_URL}/v1/executions",
                       params={"product_code": product_code, "count": count}).json()

def get_health(product_code=None):
    params = {"product_code": product_code} if product_code else {}
    return requests.get(f"{BASE_URL}/v1/gethealth", params=params).json()

# ===== 私有接口 =====

def get_balance():
    return signed_request("GET", "/v1/me/getbalance")

def send_order(product_code, side, order_type, size, price=None):
    body = {"product_code": product_code, "child_order_type": order_type,
            "side": side, "size": size}
    if price:
        body["price"] = price
    return signed_request("POST", "/v1/me/sendchildorder", body)

def cancel_order(product_code, acceptance_id):
    return signed_request("POST", "/v1/me/cancelchildorder",
                         {"product_code": product_code,
                          "child_order_acceptance_id": acceptance_id})

def cancel_all(product_code):
    return signed_request("POST", "/v1/me/cancelallchildorders",
                         {"product_code": product_code})

def get_orders(product_code, state="ACTIVE"):
    return signed_request("GET",
        f"/v1/me/getchildorders?product_code={product_code}&child_order_state={state}")

# ===== 使用示例 =====

# 获取市场列表
markets = get_markets()
for m in markets:
    print(f"{m['product_code']} ({m.get('market_type', 'N/A')})")

# 获取 BTC/JPY Ticker
ticker = get_ticker("BTC_JPY")
print(f"\nBTC/JPY: Last={ticker['ltp']}, Bid={ticker['best_bid']}, Ask={ticker['best_ask']}")

# 获取订单簿
board = get_board("BTC_JPY")
print(f"Mid: {board['mid_price']}")
print(f"Best ask: {board['asks'][0] if board['asks'] else 'N/A'}")
print(f"Best bid: {board['bids'][0] if board['bids'] else 'N/A'}")

# 检查市场健康
health = get_health("BTC_JPY")
print(f"Health: {health['status']}")

# 查询余额
balances = get_balance()
for b in balances:
    if b['amount'] > 0:
        print(f"{b['currency_code']}: {b['amount']} (avail: {b['available']})")

# 限价买单
order = send_order("BTC_JPY", "BUY", "LIMIT", 0.001, price=9500000)
print(f"Order: {order}")

# 查询活跃订单
active = get_orders("BTC_JPY", "ACTIVE")
for o in active:
    print(f"Order: {o['child_order_id']} {o['side']} {o['price']} x {o['size']}")

# 全部撤单
cancel_all("BTC_JPY")
```

### pybitflyer SDK 使用

```python
# pip install pybitflyer
import pybitflyer

# 公共接口
api = pybitflyer.API()
ticker = api.ticker(product_code="BTC_JPY")
print(f"BTC/JPY: {ticker['ltp']}")

board = api.board(product_code="BTC_JPY")
print(f"Mid: {board['mid_price']}")

# 私有接口
api = pybitflyer.API(api_key="your_key", api_secret="your_secret")
balance = api.getbalance()
for b in balance:
    print(f"{b['currency_code']}: {b['amount']}")

# 下单
order = api.sendchildorder(
    product_code="BTC_JPY",
    child_order_type="LIMIT",
    side="BUY",
    price=9500000,
    size=0.001
)
print(f"Order: {order}")
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC-SHA256 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、健康状态）详细说明
- 添加交易 API（普通订单、特殊订单 IFD/OCO/IFDOCO）完整示例
- 添加 WebSocket (JSON-RPC 2.0) 订阅示例
- 添加账户管理、保证金、充提端点
- 添加 pybitflyer SDK 使用示例
- 添加市场健康状态说明和错误处理

---

## 相关资源

- [bitFlyer Lightning API 文档](https://lightning.bitflyer.com/docs)
- [bitFlyer 官网](https://bitflyer.com)
- [pybitflyer SDK](https://github.com/yagays/pybitflyer)
- [CCXT bitFlyer 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 bitFlyer Lightning 官方 API 文档整理。*
