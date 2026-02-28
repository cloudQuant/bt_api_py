# Bitunix API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: OpenAPI V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://api-doc.bitunix.com/en_us/>

## 交易所基本信息

- 官方名称: Bitunix
- 官网: <https://www.bitunix.com>
- 交易所类型: CEX (中心化交易所)
- 支持的交易对: 200+ (USDT 计价)
- 支持的交易类型: 现货(Spot)、USDT-M 永续合约(Futures)
- 手续费: Maker 0.02%, Taker 0.06% (合约基础费率)
- 特点: 专注于合约交易，支持高杠杆

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://fapi.bitunix.com`> | 主端点 |

| REST API (备用) | `<https://openapi.bitunix.com`> | 备用端点 |

| WebSocket | `wss://fapi.bitunix.com/private` | 私有 |

| WebSocket | `wss://fapi.bitunix.com/public` | 公共 |

## 认证方式

### API 密钥获取

1. 登录 Bitunix 账户
2. 进入 API 管理页面
3. 创建 API Key 和 Secret
4. 保存密钥

### 双重 SHA256 签名

Bitunix 使用双重 SHA256 签名（非 HMAC）。

- *REST 签名步骤**:
1. query 参数按 ASCII 升序拼接为字符串
2. body 参数压缩为 JSON 字符串（无多余空格）
3. 第一轮: `digest = SHA256(nonce + timestamp + apiKey + queryParams + body)`
4. 第二轮: `sign = SHA256(digest + secretKey)`

- *请求头**:

| Header | 描述 |

|--------|------|

| api-key | API Key |

| nonce | 随机字符串 |

| timestamp | 毫秒时间戳 |

| sign | 双重 SHA256 签名 |

| Content-Type | application/json |

### Python 签名示例

```python
import hashlib
import time
import uuid
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://fapi.bitunix.com">

def bitunix_sign(nonce, timestamp, query_params="", body_str=""):
    """Bitunix 双重 SHA256 签名"""

# 第一轮 SHA256
    first_input = nonce + timestamp + API_KEY + query_params + body_str
    digest = hashlib.sha256(first_input.encode('utf-8')).hexdigest()

# 第二轮 SHA256
    second_input = digest + SECRET_KEY
    return hashlib.sha256(second_input.encode('utf-8')).hexdigest()

def bitunix_request(method, path, params=None, body=None):
    """发送 Bitunix 签名请求"""
    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() *1000))

# 构建查询参数字符串（按 ASCII 排序）
    if params:
        sorted_params = sorted(params.items())
        query_str = "&".join(f"{k}={v}" for k, v in sorted_params)
    else:
        query_str = ""

    body_str = json.dumps(body, separators=(',', ':')) if body else ""

    signature = bitunix_sign(nonce, timestamp, query_str, body_str)

    headers = {
        "api-key": API_KEY,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": signature,
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{path}"
    if query_str:
        url += f"?{query_str}"

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取合约信息

- *端点**: `GET /api/v1/futures/market/trading-pairs`

```python
resp = requests.get(f"{BASE_URL}/api/v1/futures/market/trading-pairs")
data = resp.json()
if data["code"] == 0:
    for pair in data["data"][:5]:
        print(f"{pair['symbol']}: pricePrecision={pair.get('pricePrecision')}, "
              f"quantityPrecision={pair.get('quantityPrecision')}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /api/v1/futures/market/tickers`

```python
resp = requests.get(f"{BASE_URL}/api/v1/futures/market/tickers")
data = resp.json()
if data["code"] == 0:
    for t in data["data"][:5]:
        print(f"{t['symbol']}: last={t.get('lastPrice')}, "
              f"high={t.get('high24h')}, low={t.get('low24h')}, vol={t.get('volume24h')}")

```bash

### 3. 获取订单簿

- *端点**: `GET /api/v1/futures/market/depth`

- *参数**: `symbol` (必需), `limit` (可选)

```python
resp = requests.get(f"{BASE_URL}/api/v1/futures/market/depth", params={
    "symbol": "BTCUSDT", "limit": 10
})
data = resp.json()
if data["code"] == 0:
    book = data["data"]
    for ask in book.get("asks", [])[:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}")
    for bid in book.get("bids", [])[:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}")

```bash

### 4. 获取最近成交

- *端点**: `GET /api/v1/futures/market/trades`

```python
resp = requests.get(f"{BASE_URL}/api/v1/futures/market/trades", params={
    "symbol": "BTCUSDT", "limit": 10
})

```bash

### 5. 获取 K 线数据

- *端点**: `GET /api/v1/futures/market/klines`

- *参数**: `symbol` (必需), `interval` (必需: 1m/5m/15m/30m/1h/4h/1d/1w), `limit` (可选)

```python
resp = requests.get(f"{BASE_URL}/api/v1/futures/market/klines", params={
    "symbol": "BTCUSDT", "interval": "1h", "limit": 24
})
data = resp.json()
if data["code"] == 0:
    for c in data["data"]:
        print(f"T={c[0]} O={c[1]} H={c[2]} L={c[3]} C={c[4]} V={c[5]}")

```bash

## 交易 API

### 1. 查询账户信息

- *端点**: `GET /api/v1/futures/account`

```python
account = bitunix_request("GET", "/api/v1/futures/account")
if account["code"] == 0:
    data = account["data"]
    print(f"Balance: {data.get('balance')}, Available: {data.get('available')}, "
          f"Unrealized PnL: {data.get('unrealizedPnl')}")

```bash

### 2. 下单

- *端点**: `POST /api/v1/futures/trade/order`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| side | STRING | 是 | BUY / SELL |

| type | STRING | 是 | LIMIT / MARKET |

| quantity | STRING | 是 | 数量 |

| price | STRING | 条件 | 价格（LIMIT 必需） |

| leverage | STRING | 否 | 杠杆倍数 |

| clientOrderId | STRING | 否 | 客户端订单 ID |

```python

# 限价开多

order = bitunix_request("POST", "/api/v1/futures/trade/order", body={
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "quantity": "0.01",
    "price": "40000",
    "leverage": "10"
})
if order["code"] == 0:
    print(f"Order ID: {order['data'].get('orderId')}")

# 市价开空

order = bitunix_request("POST", "/api/v1/futures/trade/order", body={
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "MARKET",
    "quantity": "0.01",
    "leverage": "10"
})

```bash

### 3. 撤单

- *端点**: `POST /api/v1/futures/trade/cancel-order`

```python
result = bitunix_request("POST", "/api/v1/futures/trade/cancel-order", body={
    "symbol": "BTCUSDT",
    "orderId": "12345678"
})

```bash

### 4. 查询挂单

- *端点**: `GET /api/v1/futures/trade/open-orders`

```python
orders = bitunix_request("GET", "/api/v1/futures/trade/open-orders", params={
    "symbol": "BTCUSDT"
})
if orders["code"] == 0:
    for o in orders["data"]:
        print(f"ID:{o['orderId']} {o['side']} {o['type']} "
              f"price={o['price']} qty={o['quantity']}")

```bash

### 5. 查询持仓

- *端点**: `GET /api/v1/futures/trade/positions`

```python
positions = bitunix_request("GET", "/api/v1/futures/trade/positions")
if positions["code"] == 0:
    for p in positions["data"]:
        if float(p.get("quantity", 0)) != 0:
            print(f"{p['symbol']}: qty={p['quantity']}, entry={p['entryPrice']}, "
                  f"unrealizedPnl={p['unrealizedPnl']}, leverage={p['leverage']}")

```bash

## 速率限制

- 按端点不同配置限频（详见官方文档）

## WebSocket 支持

### 连接信息

| URL | 用途 |

|-----|------|

| `wss://fapi.bitunix.com/public` | 公共频道 |

| `wss://fapi.bitunix.com/private` | 私有频道（需认证） |

> 连接有效期 24 小时，需处理重连

### WebSocket 签名

```python
def ws_sign(nonce, timestamp, params_str=""):
    digest = hashlib.sha256((nonce + timestamp + API_KEY + params_str).encode()).hexdigest()
    return hashlib.sha256((digest + SECRET_KEY).encode()).hexdigest()

```bash

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    channel = data.get("ch", "")
    if "depth" in channel:
        print(f"Depth: {data}")
    elif "trade" in channel:
        print(f"Trade: {data}")
    elif "ticker" in channel:
        print(f"Ticker: {data}")

def on_open(ws):
    ws.send(json.dumps({
        "op": "subscribe",
        "args": [
            {"ch": "ticker", "instId": "BTCUSDT"},
            {"ch": "trade", "instId": "BTCUSDT"},
            {"ch": "depth", "instId": "BTCUSDT"}
        ]
    }))

ws = websocket.WebSocketApp(
    "wss://fapi.bitunix.com/public",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

## 错误代码

| 错误码 | 描述 |

|--------|------|

| 0 | 成功 |

| 10001 | 参数错误 |

| 10002 | 签名无效 |

| 10003 | 余额不足 |

| 10004 | 订单不存在 |

| 10005 | 超过最大持仓 |

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加双重 SHA256 签名认证 Python 示例
- 添加市场数据、交易、WebSocket 订阅示例

- --

## 相关资源

- [Bitunix API 文档](<https://api-doc.bitunix.com/en_us/)>
- [Bitunix 官网](<https://www.bitunix.com)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Bitunix 官方 API 文档整理。*
