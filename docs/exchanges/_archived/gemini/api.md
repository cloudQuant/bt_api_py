# Gemini API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://docs.gemini.com/>
- 市场数据: <https://docs.gemini.com/rest/market-data>

## 交易所基本信息

- 官方名称: Gemini
- 官网: <https://www.gemini.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 美国纽约（由 Winklevoss 兄弟创办）
- 支持的交易对: 100+ (USD, BTC, ETH, GBP, EUR, SGD 计价)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetuals)
- 手续费: Maker 0.20%, Taker 0.40% (API 费率，阶梯递减)
- 法币支持: USD, GBP, EUR, SGD, CAD, AUD, HKD
- 合规: 纽约州金融服务部 (NYDFS) 持牌信托公司
- 特点: 高度合规，SOC 2 认证，面向机构和个人

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.gemini.com`> | 主端点 |

| Sandbox REST API | `<https://api.sandbox.gemini.com`> | 沙盒测试 |

| WebSocket (Market Data) | `wss://api.gemini.com/v1/marketdata/{symbol}` | 行情 |

| WebSocket (Order Events) | `wss://api.gemini.com/v1/order/events` | 订单事件 |

| Sandbox WebSocket | `wss://api.sandbox.gemini.com/v1/marketdata/{symbol}` | 沙盒行情 |

## 认证方式

### API 密钥获取

1. 注册 Gemini 账户并完成 KYC
2. 进入 API Settings 页面
3. 创建 API Key，选择 Primary 或 Master 权限
4. 保存 API Key 和 API Secret
5. 可设置 IP 白名单

### HMAC SHA384 签名

Gemini 使用 HMAC SHA384 对 payload 签名。

- *签名步骤**:
1. 构建 JSON payload，包含 `request` (路径), `nonce` (递增整数)
2. 将 payload 序列化为 JSON 字符串
3. Base64 编码 JSON 字符串
4. 使用 API Secret 对 Base64 字符串进行 HMAC SHA384 签名
5. 将签名转为十六进制

- *请求头**:

| Header | 描述 |

|--------|------|

| X-GEMINI-APIKEY | API Key |

| X-GEMINI-PAYLOAD | Base64 编码的 payload |

| X-GEMINI-SIGNATURE | HMAC SHA384 签名 (hex) |

| Content-Type | text/plain |

| Content-Length | 0 |

| Cache-Control | no-cache |

### Python 签名示例

```python
import hmac
import hashlib
import base64
import json
import time
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.gemini.com">

def gemini_request(path, extra_params=None):
    """发送 Gemini 签名请求"""
    payload = {
        "request": path,
        "nonce": int(time.time() *1000),
    }
    if extra_params:
        payload.update(extra_params)

# JSON -> Base64
    payload_json = json.dumps(payload)
    payload_b64 = base64.b64encode(payload_json.encode('utf-8'))

# HMAC SHA384
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        payload_b64,
        hashlib.sha384
    ).hexdigest()

    headers = {
        "X-GEMINI-APIKEY": API_KEY,
        "X-GEMINI-PAYLOAD": payload_b64.decode('utf-8'),
        "X-GEMINI-SIGNATURE": signature,
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Cache-Control": "no-cache",
    }

    url = f"{BASE_URL}{path}"
    resp = requests.post(url, headers=headers)
    return resp.json()

# 测试

balances = gemini_request("/v1/balances")
print(balances)

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取交易对列表

- *端点**: `GET /v1/symbols`

```python
resp = requests.get(f"{BASE_URL}/v1/symbols")
symbols = resp.json()
print(f"Total symbols: {len(symbols)}")

# ['btcusd', 'ethbtc', 'ethusd', ...]

```bash

### 2. 获取交易对详情

- *端点**: `GET /v1/symbols/details/{symbol}`

```python
resp = requests.get(f"{BASE_URL}/v1/symbols/details/btcusd")
info = resp.json()
print(f"BTCUSD: tick_size={info['tick_size']}, quote_increment={info['quote_increment']}, "
      f"min_order_size={info['min_order_size']}, status={info['status']}")

```bash

### 3. 获取 Ticker V2

- *端点**: `GET /v2/ticker/{symbol}`

```python
resp = requests.get(f"{BASE_URL}/v2/ticker/btcusd")
t = resp.json()
print(f"BTC/USD: close={t['close']}, open={t['open']}, high={t['high']}, low={t['low']}")
print(f"Bid={t['bid']}, Ask={t['ask']}")
print(f"Changes: 1h={t.get('changes', {}).get('1h')}, "
      f"24h={t.get('changes', {}).get('24h')}")

```bash

### 4. 获取订单簿

- *端点**: `GET /v1/book/{symbol}`

- *参数**: `limit_bids` (可选, 默认 50), `limit_asks` (可选, 默认 50)

```python
resp = requests.get(f"{BASE_URL}/v1/book/btcusd", params={
    "limit_bids": 10, "limit_asks": 10
})
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask['price']}, amount={ask['amount']}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid['price']}, amount={bid['amount']}")

```bash

### 5. 获取最近成交

- *端点**: `GET /v1/trades/{symbol}`

- *参数**: `limit_trades` (可选, 默认 50, 最大 500), `since` (可选, 时间戳), `include_breaks` (可选)

```python
resp = requests.get(f"{BASE_URL}/v1/trades/btcusd", params={"limit_trades": 10})
for trade in resp.json():
    print(f"TID={trade['tid']} price={trade['price']} amount={trade['amount']} "
          f"type={trade['type']} time={trade['timestampms']}")

```bash

### 6. 获取 K 线数据

- *端点**: `GET /v2/candles/{symbol}/{time_frame}`

- *参数**: time_frame 路径参数: `1m`/`5m`/`15m`/`30m`/`1hr`/`6hr`/`1day`

```python
resp = requests.get(f"{BASE_URL}/v2/candles/btcusd/1hr")
for candle in resp.json()[:10]:

# [timestamp_ms, open, high, low, close, volume]
    print(f"T={candle[0]} O={candle[1]} H={candle[2]} L={candle[3]} C={candle[4]} V={candle[5]}")

```bash

### 7. 获取价格 Feed

- *端点**: `GET /v1/pricefeed`

```python
resp = requests.get(f"{BASE_URL}/v1/pricefeed")
for p in resp.json()[:5]:
    print(f"{p['pair']}: price={p['price']}, change={p['percentChange24h']}%")

```bash

## 交易 API

> 以下端点均需 HMAC SHA384 认证。所有私有请求使用 POST 方法。

### 1. 查询余额

- *端点**: `POST /v1/balances`

```python
balances = gemini_request("/v1/balances")
for b in balances:
    if float(b["amount"]) > 0:
        print(f"{b['currency']}: amount={b['amount']}, available={b['available']}, "
              f"availableForWithdrawal={b['availableForWithdrawal']}")

```bash

### 2. 下单

- *端点**: `POST /v1/order/new`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对，如 btcusd |

| amount | STRING | 是 | 数量 |

| price | STRING | 是 | 价格 |

| side | STRING | 是 | buy / sell |

| type | STRING | 是 | exchange limit (现货限价) |

| client_order_id | STRING | 否 | 客户端订单 ID |

| options | ARRAY | 否 | maker-or-cancel, immediate-or-cancel, fill-or-kill, auction-only |

```python

# 限价买单

order = gemini_request("/v1/order/new", {
    "symbol": "btcusd",
    "amount": "0.001",
    "price": "40000",
    "side": "buy",
    "type": "exchange limit"
})
print(f"Order ID: {order.get('order_id')}, Status: {order.get('type')}")

# Maker-or-Cancel 订单（只做 Maker）

order = gemini_request("/v1/order/new", {
    "symbol": "btcusd",
    "amount": "0.001",
    "price": "40000",
    "side": "buy",
    "type": "exchange limit",
    "options": ["maker-or-cancel"]
})

# IOC 订单

order = gemini_request("/v1/order/new", {
    "symbol": "ethbtc",
    "amount": "1.0",
    "price": "0.05",
    "side": "buy",
    "type": "exchange limit",
    "options": ["immediate-or-cancel"]
})

```bash

### 3. 撤单

- *端点**: `POST /v1/order/cancel`

```python
result = gemini_request("/v1/order/cancel", {
    "order_id": "12345678"
})
print(f"Cancelled: {result}")

```bash

### 4. 全部撤单

- *端点**: `POST /v1/order/cancel/all`

```python
result = gemini_request("/v1/order/cancel/all")
print(f"Cancelled count: {result.get('result')}")

```bash

### 5. 查询订单状态

- *端点**: `POST /v1/order/status`

```python
order = gemini_request("/v1/order/status", {
    "order_id": "12345678"
})
print(f"Status: {order.get('is_live')}, Executed: {order.get('executed_amount')}/{order.get('original_amount')}")

```bash

### 6. 查询活跃订单

- *端点**: `POST /v1/orders`

```python
orders = gemini_request("/v1/orders")
for o in orders:
    print(f"ID:{o['order_id']} {o['side']} {o['symbol']} "
          f"price={o['price']} amount={o['remaining_amount']}/{o['original_amount']}")

```bash

### 7. 查询历史成交

- *端点**: `POST /v1/mytrades`

- *参数**: `symbol` (必需), `limit_trades` (可选, 默认 50, 最大 500), `timestamp` (可选)

```python
trades = gemini_request("/v1/mytrades", {
    "symbol": "btcusd",
    "limit_trades": 10
})
for t in trades:
    print(f"Price={t['price']}, Amount={t['amount']}, Fee={t['fee_amount']} {t['fee_currency']}")

```bash

## 账户管理 API

| 端点 | 描述 |

|------|------|

| POST /v1/balances | 查询余额 |

| POST /v1/notionalbalances/{currency} | 查询总资产（以指定币种计价） |

| POST /v1/transfers | 查询转账记录 |

| POST /v1/addresses/{network} | 获取充值地址 |

| POST /v1/withdraw/{currency} | 发起提现 |

| POST /v1/deposit/{currency}/newAddress | 生成新充值地址 |

| POST /v1/account/list | 子账户列表 |

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 公共 API | 120 次/分钟 | 按 IP |

| 私有 API | 600 次/分钟 | 按 API Key |

| 下单 | 自适应 | 根据账户等级 |

### 最佳实践

- 使用 Sandbox 进行测试: `<https://api.sandbox.gemini.com`>
- nonce 必须严格递增（推荐使用毫秒时间戳）
- 使用 WebSocket 获取实时行情
- 使用 `client_order_id` 标识订单
- Content-Type 应设为 `text/plain`，Content-Length 设为 `0`

## WebSocket 支持

### Market Data WebSocket

每个交易对一个 WebSocket 连接。

- *URL**: `wss://api.gemini.com/v1/marketdata/{symbol}`

- *参数** (查询字符串): `heartbeat` (true/false), `top_of_book` (true/false), `bids` (true/false), `offers` (true/false), `trades` (true/false), `auctions` (true/false)

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    msg_type = data.get("type", "")

    if msg_type == "update":
        events = data.get("events", [])
        for event in events:
            if event["type"] == "trade":
                print(f"Trade: price={event['price']}, amount={event['amount']}, "
                      f"makerSide={event['makerSide']}")
            elif event["type"] == "change":
                print(f"Book: side={event['side']}, price={event['price']}, "
                      f"remaining={event['remaining']}")
    elif msg_type == "heartbeat":
        pass  # keep-alive

ws = websocket.WebSocketApp(
    "wss://api.gemini.com/v1/marketdata/btcusd?heartbeat=true&trades=true",
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()

```bash

### Order Events WebSocket (需认证)

```python
import websocket
import json
import hmac
import hashlib
import base64
import time

def create_ws_auth_headers():
    payload = {
        "request": "/v1/order/events",
        "nonce": int(time.time() * 1000)
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode())
    sig = hmac.new(API_SECRET.encode(), payload_b64, hashlib.sha384).hexdigest()
    return {
        "X-GEMINI-APIKEY": API_KEY,
        "X-GEMINI-PAYLOAD": payload_b64.decode(),
        "X-GEMINI-SIGNATURE": sig,
    }

headers = create_ws_auth_headers()
ws = websocket.WebSocketApp(
    "wss://api.gemini.com/v1/order/events",
    header=headers,
    on_message=lambda ws, msg: print(json.loads(msg)),
)
ws.run_forever()

```bash

## 错误代码

### HTTP 错误

| 状态码 | 描述 |

|--------|------|

| 200 | 成功 |

| 400 | 请求参数错误 |

| 403 | 权限不足 |

| 404 | 端点不存在 |

| 406 | 接受频率过高 |

| 429 | 速率限制 |

| 500 | 服务器错误 |

### 业务错误

| 错误原因 | 描述 |

|---------|------|

| InvalidPrice | 无效价格 |

| InvalidQuantity | 无效数量 |

| InvalidSide | 无效方向 |

| InvalidSymbol | 无效交易对 |

| InsufficientFunds | 余额不足 |

| OrderNotFound | 订单不存在 |

| InvalidNonce | nonce 无效（必须递增） |

| InvalidSignature | 签名无效 |

| RateLimit | 速率限制 |

| MaintenanceMode | 维护模式 |

### Python 错误处理

```python
def safe_gemini_request(path, extra_params=None):
    """带错误处理的请求"""
    try:
        result = gemini_request(path, extra_params)
        if isinstance(result, dict) and "result" in result and result["result"] == "error":
            print(f"Gemini Error [{result.get('reason')}]: {result.get('message')}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 V1/V2 REST API 端点说明
- 添加 HMAC SHA384 + Base64 签名认证完整 Python 示例
- 添加市场数据 API（Ticker V2、订单簿、成交、K 线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket (Market Data / Order Events) 订阅示例
- 添加 Sandbox 测试环境信息
- 添加错误代码表和错误处理

- --

## 相关资源

- [Gemini API 文档](<https://docs.gemini.com/)>
- [Gemini Sandbox](<https://exchange.sandbox.gemini.com/)>
- [Gemini 官网](<https://www.gemini.com)>
- [CCXT Gemini 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Gemini 官方 API 文档整理。*
