# Poloniex API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: Spot V2 / Futures V3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档 (Spot): <https://api-docs.poloniex.com/spot/api/>
- 官方文档 (Futures): <https://api-docs.poloniex.com/v3/futures/api/>

## 交易所基本信息

- 官方名称: Poloniex
- 官网: <https://poloniex.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 塞舌尔
- 支持的交易对: 400+ (USDT, BTC, USDC, TRX 计价)
- 支持的交易类型: 现货(Spot)、USDT-M 永续合约(Futures)
- 手续费: Maker 0.145%, Taker 0.155% (基础费率)
- 特点: 最早的加密货币交易所之一，2014 年成立

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| Spot REST API | `<https://api.poloniex.com`> | 现货 |

| Futures REST API | `<https://api.poloniex.com/v3`> | 合约 V3 |

| WebSocket (Public) | `wss://ws.poloniex.com/ws/public` | 公共行情 |

| WebSocket (Private) | `wss://ws.poloniex.com/ws/private` | 私有频道 |

## 认证方式

### API 密钥获取

1. 登录 Poloniex 账户
2. 进入 API Keys 管理页面
3. 创建 API Key，获取 API Key、Secret 和 Passphrase
4. 设置权限（读取、交易、提现）和 IP 限制
5. 默认权限仅读取，需手动开启交易权限

### HMAC-SHA256 签名

- *签名步骤**:
1. 获取当前毫秒时间戳
2. 拼接签名字符串: `timestamp + method + requestPath + body`
3. 使用 API Secret 对签名字符串进行 HMAC-SHA256 签名
4. 将签名结果进行 Base64 编码

- *请求头**:

| Header | 描述 |

|--------|------|

| key | API Key |

| signTimestamp | 毫秒时间戳 |

| signature | HMAC-SHA256 签名 (Base64) |

| Content-Type | application/json |

### Python 签名示例

```python
import hmac
import hashlib
import base64
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.poloniex.com">

def poloniex_request(method, path, params=None, body=None):
    """发送 Poloniex 签名请求"""
    timestamp = str(int(time.time() *1000))

# 构建请求路径（含查询参数）
    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{query}"
    else:
        request_path = path

# 构建签名字符串
    body_str = json.dumps(body) if body else ""
    sign_str = timestamp + method.upper() + request_path + body_str

# HMAC-SHA256 + Base64
    signature = base64.b64encode(
        hmac.new(
            API_SECRET.encode('utf-8'),
            sign_str.encode('utf-8'),
            hashlib.sha256
        ).digest()
    ).decode('utf-8')

    headers = {
        "key": API_KEY,
        "signTimestamp": timestamp,
        "signature": signature,
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{request_path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers, data=body_str)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()

# 测试：查询账户余额

result = poloniex_request("GET", "/accounts/balances")
print(result)

```bash

## 市场数据 API

> 公共 API 无需认证，使用 GET 请求。

### 1. 获取市场列表

- *端点**: `GET /markets`

```python
resp = requests.get(f"{BASE_URL}/markets")
for m in resp.json()[:5]:
    print(f"{m['symbol']}: base={m['baseCurrencyName']}, quote={m['quoteCurrencyName']}, "
          f"state={m['state']}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /markets/{symbol}/ticker24h` 或 `GET /markets/ticker24h`

```python

# 单个

resp = requests.get(f"{BASE_URL}/markets/BTC_USDT/ticker24h")
t = resp.json()
print(f"BTC/USDT: close={t['close']}, high={t['high']}, low={t['low']}, "
      f"quantity={t['quantity']}, amount={t['amount']}")

# 全部

resp = requests.get(f"{BASE_URL}/markets/ticker24h")
for t in resp.json()[:5]:
    print(f"{t['symbol']}: close={t['close']}, vol={t['quantity']}")

```bash

### 3. 获取最新价

- *端点**: `GET /markets/{symbol}/price` 或 `GET /markets/price`

```python
resp = requests.get(f"{BASE_URL}/markets/BTC_USDT/price")
print(f"BTC/USDT price: {resp.json()['price']}")

```bash

### 4. 获取订单簿

- *端点**: `GET /markets/{symbol}/orderBook`

- *参数**: `scale` (可选, 精度), `limit` (可选, 默认 10, 最大 150)

```python
resp = requests.get(f"{BASE_URL}/markets/BTC_USDT/orderBook", params={"limit": 10})
book = resp.json()
print(f"Time: {book['time']}")
for ask in book["asks"][:5]:
    print(f"ASK: price={ask}, qty={book['asks'][ask] if isinstance(book['asks'], dict) else ask}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /markets/{symbol}/candles`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| interval | STRING | 是 | MINUTE_1/MINUTE_5/MINUTE_10/MINUTE_15/MINUTE_30/HOUR_1/HOUR_2/HOUR_4/HOUR_6/HOUR_12/DAY_1/DAY_3/WEEK_1/MONTH_1 |

| limit | INT | 否 | 数量 (默认 100, 最大 500) |

| startTime | LONG | 否 | 开始时间（毫秒） |

| endTime | LONG | 否 | 结束时间（毫秒） |

```python
resp = requests.get(f"{BASE_URL}/markets/BTC_USDT/candles", params={
    "interval": "HOUR_1",
    "limit": 24
})
for candle in resp.json():

# [low, high, open, close, amount, quantity, buyTakerAmount, buyTakerQuantity, tradeCount, ts, weightedAverage, interval, startTime, closeTime]
    print(f"O={candle[2]} H={candle[1]} L={candle[0]} C={candle[3]} V={candle[5]}")

```bash

### 6. 获取最近成交

- *端点**: `GET /markets/{symbol}/trades`

- *参数**: `limit` (可选, 默认 500, 最大 1000)

```python
resp = requests.get(f"{BASE_URL}/markets/BTC_USDT/trades", params={"limit": 10})
for trade in resp.json():
    print(f"ID={trade['id']} price={trade['price']} qty={trade['quantity']} "
          f"side={trade['takerSide']} time={trade['ts']}")

```bash

## 交易 API

> 以下端点均需签名认证。

### 1. 查询账户余额

- *端点**: `GET /accounts/balances`

```python
balances = poloniex_request("GET", "/accounts/balances")
for b in balances:
    available = float(b.get("available", 0))
    hold = float(b.get("hold", 0))
    if available > 0 or hold > 0:
        print(f"{b['currency']}: available={b['available']}, hold={b['hold']}")

```bash

### 2. 下单

- *端点**: `POST /orders`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对，如 BTC_USDT |

| side | STRING | 是 | BUY / SELL |

| type | STRING | 否 | LIMIT / MARKET / LIMIT_MAKER (默认 MARKET) |

| quantity | STRING | 条件 | 数量 |

| price | STRING | 条件 | 价格（LIMIT 必需） |

| amount | STRING | 条件 | 金额（市价买时可用） |

| timeInForce | STRING | 否 | GTC / IOC / FOK |

| clientOrderId | STRING | 否 | 客户端订单 ID |

```python

# 限价买单

order = poloniex_request("POST", "/orders", body={
    "symbol": "BTC_USDT",
    "side": "BUY",
    "type": "LIMIT",
    "quantity": "0.001",
    "price": "40000",
    "timeInForce": "GTC"
})
print(f"Order ID: {order.get('id')}")

# 市价买单（按金额）

order = poloniex_request("POST", "/orders", body={
    "symbol": "BTC_USDT",
    "side": "BUY",
    "type": "MARKET",
    "amount": "100"  # 100 USDT

})

# 市价卖单（按数量）

order = poloniex_request("POST", "/orders", body={
    "symbol": "BTC_USDT",
    "side": "SELL",
    "type": "MARKET",
    "quantity": "0.001"
})

```bash

### 3. 撤单

- *端点**: `DELETE /orders/{id}`

```python
result = poloniex_request("DELETE", "/orders/12345678")
print(f"Cancelled: {result}")

```bash

### 4. 批量撤单

- *端点**: `DELETE /orders`

```python

# 撤销指定订单

result = poloniex_request("DELETE", "/orders", body={
    "orderIds": ["123", "456", "789"]
})

# 撤销某交易对所有订单

result = poloniex_request("DELETE", "/orders", body={
    "symbol": "BTC_USDT"
})

```bash

### 5. 查询订单

- *端点**: `GET /orders/{id}`

```python
order = poloniex_request("GET", "/orders/12345678")
print(f"Status: {order.get('state')}, Filled: {order.get('filledQuantity')}/{order.get('quantity')}")

```bash

### 6. 查询挂单列表

- *端点**: `GET /orders`

- *参数**: `symbol` (可选), `side` (可选), `limit` (可选, 默认 100)

```python
orders = poloniex_request("GET", "/orders", params={"symbol": "BTC_USDT"})
for o in orders:
    print(f"ID:{o['id']} {o['side']} {o['type']} price={o['price']} qty={o['quantity']}")

```bash

### 7. 查询历史订单

- *端点**: `GET /orders/history`

### 8. 查询成交记录

- *端点**: `GET /trades`

## 合约交易 API (Futures V3)

### 端点概览

| 端点 | 方法 | 描述 |

|------|------|------|

| /v3/trade/order | POST | 合约下单 |

| /v3/trade/order | DELETE | 合约撤单 |

| /v3/trade/openOrders | GET | 查询挂单 |

| /v3/trade/position | GET | 查询持仓 |

- *合约交易对格式**: `{baseCcy}_{quoteCcy}_PERP`，如 `BTC_USDT_PERP`

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| /accounts/balances | GET | 查询余额 |

| /accounts/transfer | POST | 资金划转 |

| /wallets/addresses | GET | 获取充值地址 |

| /wallets/withdraw | POST | 发起提现 |

| /wallets/activity | GET | 充提记录 |

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 公共端点 | 200 次/秒 | 按 IP |

| 私有端点 | 50 次/秒 | 按 API Key |

| 下单 | 50 次/秒 | 按 API Key |

### 最佳实践

- 使用 WebSocket 获取实时行情
- 时间戳需在服务器时间 ±30s 范围内
- 使用 `clientOrderId` 实现幂等性
- 签名使用 Base64 编码的 HMAC-SHA256

## WebSocket 支持

### 连接信息

| URL | 用途 |

|-----|------|

| `wss://ws.poloniex.com/ws/public` | 公共频道（行情） |

| `wss://ws.poloniex.com/ws/private` | 私有频道（需认证） |

### 公共频道

| 频道 | 描述 |

|------|------|

| `ticker` | 24h Ticker |

| `trades` | 实时成交 |

| `book` | 订单簿 |

| `book_lv2` | L2 订单簿 |

| `candles_{interval}` | K 线数据 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    channel = data.get("channel", "")
    event_data = data.get("data", [])

    if channel == "ticker":
        for t in event_data:
            print(f"Ticker {t['symbol']}: close={t['close']}, vol={t['quantity']}")
    elif channel == "trades":
        for t in event_data:
            print(f"Trade {t['symbol']}: price={t['price']}, qty={t['quantity']}, side={t['takerSide']}")
    elif channel == "book":
        for b in event_data:
            print(f"Book {b.get('symbol')}: asks={len(b.get('asks', []))}, bids={len(b.get('bids', []))}")

def on_open(ws):

# 订阅 Ticker
    ws.send(json.dumps({
        "event": "subscribe",
        "channel": ["ticker"],
        "symbols": ["BTC_USDT"]
    }))

# 订阅成交
    ws.send(json.dumps({
        "event": "subscribe",
        "channel": ["trades"],
        "symbols": ["BTC_USDT"]
    }))

# 订阅订单簿
    ws.send(json.dumps({
        "event": "subscribe",
        "channel": ["book"],
        "symbols": ["BTC_USDT"]
    }))

ws = websocket.WebSocketApp(
    "wss://ws.poloniex.com/ws/public",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

## 错误代码

### HTTP 错误码

| 状态码 | 描述 |

|--------|------|

| 200 | 成功 |

| 400 | 请求参数错误 |

| 401 | 认证失败 |

| 404 | 资源不存在 |

| 429 | 速率限制 |

| 500 | 服务器错误 |

### 业务错误码

| 错误码 | 描述 |

|--------|------|

| 21301 | 订单不存在 |

| 21302 | 订单金额过小 |

| 21303 | 订单数量过小 |

| 21304 | 余额不足 |

| 21305 | 超过最大订单数 |

| 21308 | 价格精度错误 |

| 21309 | 数量精度错误 |

### Python 错误处理

```python
def safe_poloniex_request(method, path, params=None, body=None):
    """带错误处理的请求"""
    try:
        result = poloniex_request(method, path, params, body)
        if isinstance(result, dict) and "code" in result:
            code = result["code"]
            if code != 200 and code != 0:
                print(f"API Error [{code}]: {result.get('message', 'Unknown')}")
                return None
        return result
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print("Rate limited, waiting...")
            time.sleep(2)
            return safe_poloniex_request(method, path, params, body)
        print(f"HTTP Error: {e}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC-SHA256 + Base64 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K 线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket 公共频道订阅示例
- 添加合约和账户管理 API 端点概览
- 添加错误代码表和错误处理

- --

## 相关资源

- [Poloniex Spot API 文档](<https://api-docs.poloniex.com/spot/api/)>
- [Poloniex Futures API 文档](<https://api-docs.poloniex.com/v3/futures/api/)>
- [Poloniex 官网](<https://poloniex.com)>
- [CCXT Poloniex 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Poloniex 官方 API 文档整理。*
