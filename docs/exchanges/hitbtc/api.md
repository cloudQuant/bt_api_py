# HitBTC API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://api.hitbtc.com/>
- Demo API: <https://api.demo.hitbtc.com/>

## 交易所基本信息

- 官方名称: HitBTC
- 官网: <https://hitbtc.com>
- 交易所类型: CEX (中心化交易所)
- 支持的交易对: 1000+ (USDT, BTC, ETH 计价)
- 支持的交易类型: 现货(Spot)、保证金(Margin)、永续合约(Futures)
- 手续费: Maker -0.01%, Taker 0.07% (基础费率，负 Maker 费率)
- 特点: 高流动性，API 与 BeQuant 共享相同架构

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.hitbtc.com/api/3`> | 主端点 V3 |

| Demo REST API | `<https://api.demo.hitbtc.com/api/3`> | 沙盒测试 |

| WebSocket Public | `wss://api.hitbtc.com/api/3/ws/public` | 公共行情 |

| WebSocket Trading | `wss://api.hitbtc.com/api/3/ws/trading` | 交易（需认证） |

| WebSocket Wallet | `wss://api.hitbtc.com/api/3/ws/wallet` | 钱包（需认证） |

## 认证方式

### API 密钥获取

1. 注册 HitBTC 账户
2. 进入 API Keys 管理页面
3. 创建 API Key，获取 API Key 和 Secret Key
4. 设置权限（Order Book, Trading, Account）

### Basic Authentication

HitBTC V3 使用 HTTP Basic Authentication，API Key 作为用户名，Secret Key 作为密码。

### Python 认证示例

```python
import requests
from requests.auth import HTTPBasicAuth
import json

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://api.hitbtc.com/api/3">

# Basic Auth

auth = HTTPBasicAuth(API_KEY, SECRET_KEY)

def hitbtc_get(path, params=None):
    """发送 GET 请求"""
    resp = requests.get(f"{BASE_URL}{path}", auth=auth, params=params)
    return resp.json()

def hitbtc_post(path, body=None):
    """发送 POST 请求"""
    resp = requests.post(f"{BASE_URL}{path}", auth=auth, json=body)
    return resp.json()

def hitbtc_delete(path, params=None):
    """发送 DELETE 请求"""
    resp = requests.delete(f"{BASE_URL}{path}", auth=auth, params=params)
    return resp.json()

# 测试

result = hitbtc_get("/spot/balance")
print(result)

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取所有交易对

- *端点**: `GET /public/symbol`

```python
resp = requests.get(f"{BASE_URL}/public/symbol")
symbols = resp.json()
for sym_id, info in list(symbols.items())[:5]:
    print(f"{sym_id}: base={info['base_currency']}, quote={info['quote_currency']}, "
          f"status={info['status']}")

```bash

### 2. 获取单个交易对

- *端点**: `GET /public/symbol/{symbol}`

```python
resp = requests.get(f"{BASE_URL}/public/symbol/BTCUSDT")
info = resp.json()
print(f"BTCUSDT: tick_size={info['tick_size']}, qty_increment={info['quantity_increment']}")

```bash

### 3. 获取 Ticker

- *端点**: `GET /public/ticker` 或 `GET /public/ticker/{symbol}`

```python

# 单个

resp = requests.get(f"{BASE_URL}/public/ticker/BTCUSDT")
t = resp.json()
print(f"BTC/USDT: last={t['last']}, bid={t['bid']}, ask={t['ask']}, "
      f"high={t['high']}, low={t['low']}, vol={t['volume']}")

# 全部

resp = requests.get(f"{BASE_URL}/public/ticker")
tickers = resp.json()
print(f"Total tickers: {len(tickers)}")

```bash

### 4. 获取订单簿

- *端点**: `GET /public/orderbook/{symbol}`

- *参数**: `depth` (可选, 默认 10, 最大 0=全部), `volume` (可选, 按金额聚合)

```python
resp = requests.get(f"{BASE_URL}/public/orderbook/BTCUSDT", params={"depth": 10})
book = resp.json()
for ask in book["ask"][:5]:
    print(f"ASK: price={ask['price']}, size={ask['size']}")
for bid in book["bid"][:5]:
    print(f"BID: price={bid['price']}, size={bid['size']}")

```bash

### 5. 获取最近成交

- *端点**: `GET /public/trades/{symbol}`

- *参数**: `sort` (ASC/DESC), `from`/`till` (时间或 ID), `limit` (默认 10, 最大 1000)

```python
resp = requests.get(f"{BASE_URL}/public/trades/BTCUSDT", params={"limit": 10})
for trade in resp.json():
    print(f"ID={trade['id']} price={trade['price']} qty={trade['qty']} "
          f"side={trade['side']} time={trade['timestamp']}")

```bash

### 6. 获取 K 线数据

- *端点**: `GET /public/candles/{symbol}`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| period | STRING | 否 | M1/M3/M5/M15/M30/H1/H4/D1/D7/1M (默认 M30) |

| sort | STRING | 否 | ASC/DESC |

| from | STRING | 否 | 开始时间 (ISO 8601) |

| till | STRING | 否 | 结束时间 (ISO 8601) |

| limit | INT | 否 | 数量 (默认 100, 最大 1000) |

```python
resp = requests.get(f"{BASE_URL}/public/candles/BTCUSDT", params={
    "period": "H1",
    "limit": 24
})
for candle in resp.json():
    print(f"T={candle['timestamp']} O={candle['open']} H={candle['max']} "
          f"L={candle['min']} C={candle['close']} V={candle['volume']}")

```bash

### 7. 获取币种信息

- *端点**: `GET /public/currency`

## 交易 API

> 以下端点均需 Basic Auth 认证。

### 1. 查询现货余额

- *端点**: `GET /spot/balance`

```python
balances = hitbtc_get("/spot/balance")
for b in balances:
    available = float(b.get("available", 0))
    reserved = float(b.get("reserved", 0))
    if available > 0 or reserved > 0:
        print(f"{b['currency']}: available={b['available']}, reserved={b['reserved']}")

```bash

### 2. 下单

- *端点**: `POST /spot/order`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| side | STRING | 是 | buy / sell |

| type | STRING | 否 | limit / market / stopLimit / stopMarket (默认 limit) |

| quantity | STRING | 是 | 数量 |

| price | STRING | 条件 | 价格（limit/stopLimit 必需） |

| stop_price | STRING | 条件 | 触发价（止损单） |

| time_in_force | STRING | 否 | GTC / IOC / FOK / Day / GTD |

| client_order_id | STRING | 否 | 客户端订单 ID |

| post_only | BOOL | 否 | 仅 Maker |

```python

# 限价买单

order = hitbtc_post("/spot/order", {
    "symbol": "BTCUSDT",
    "side": "buy",
    "type": "limit",
    "quantity": "0.001",
    "price": "40000",
    "time_in_force": "GTC"
})
print(f"Order ID: {order.get('id')}, Client ID: {order.get('client_order_id')}")

# 市价卖单

order = hitbtc_post("/spot/order", {
    "symbol": "BTCUSDT",
    "side": "sell",
    "type": "market",
    "quantity": "0.001"
})

# 止损限价单

order = hitbtc_post("/spot/order", {
    "symbol": "BTCUSDT",
    "side": "sell",
    "type": "stopLimit",
    "quantity": "0.001",
    "price": "38000",
    "stop_price": "39000"
})

```bash

### 3. 撤单

- *端点**: `DELETE /spot/order/{clientOrderId}`

```python
result = hitbtc_delete("/spot/order/my_order_001")
print(f"Cancelled: {result}")

```bash

### 4. 全部撤单

- *端点**: `DELETE /spot/order`

- *参数**: `symbol` (可选, 不传撤所有)

```python
result = hitbtc_delete("/spot/order", params={"symbol": "BTCUSDT"})
print(f"Cancelled orders: {result}")

```bash

### 5. 查询活跃订单

- *端点**: `GET /spot/order`

- *参数**: `symbol` (可选)

```python
orders = hitbtc_get("/spot/order", params={"symbol": "BTCUSDT"})
for o in orders:
    print(f"ID:{o['client_order_id']} {o['side']} {o['type']} "
          f"price={o['price']} qty={o['quantity']} status={o['status']}")

```bash

### 6. 查询成交记录

- *端点**: `GET /spot/history/trade`

- *参数**: `symbol` (可选), `sort`, `from`, `till`, `limit`

### 7. 查询历史订单

- *端点**: `GET /spot/history/order`

## 账户管理 API

### 端点概览

| 端点 | 方法 | 描述 |

|------|------|------|

| /wallet/balance | GET | 钱包余额 |

| /wallet/crypto/address | GET | 充值地址 |

| /wallet/crypto/withdraw | POST | 发起提现 |

| /wallet/transactions | GET | 交易记录 |

| /wallet/transfer | POST | 内部转账（钱包↔交易） |

| /spot/fee | GET | 查询手续费率 |

| /spot/fee/{symbol} | GET | 查询交易对费率 |

```python

# 查询钱包余额

wallet = hitbtc_get("/wallet/balance")
for b in wallet:
    if float(b.get("available", 0)) > 0:
        print(f"{b['currency']}: available={b['available']}, reserved={b['reserved']}")

# 内部转账：钱包 → 交易账户

result = hitbtc_post("/wallet/transfer", {
    "currency": "USDT",
    "amount": "100",
    "source": "wallet",
    "destination": "spot"
})

# 查询手续费

fee = hitbtc_get("/spot/fee/BTCUSDT")
print(f"Maker: {fee['maker_rate']}, Taker: {fee['taker_rate']}")

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| Market Data | 100 次/秒 | 按 IP |

| Trading | 300 次/秒 | 按 API Key |

| Wallet | 10 次/秒 | 按 API Key |

### 最佳实践

- 使用 WebSocket 获取实时数据
- 使用 Demo API 进行测试: `<https://api.demo.hitbtc.com/api/3`>
- 使用 `client_order_id` 管理订单
- HitBTC 和 BeQuant 共享 API 架构，切换仅需更换域名

## WebSocket 支持

### 连接信息

| URL | 用途 |

|-----|------|

| `wss://api.hitbtc.com/api/3/ws/public` | 公共行情 |

| `wss://api.hitbtc.com/api/3/ws/trading` | 交易（需 Basic Auth） |

| `wss://api.hitbtc.com/api/3/ws/wallet` | 钱包（需 Basic Auth） |

### 公共频道

| 方法 | 描述 |

|------|------|

| subscribe_ticker | 订阅 Ticker |

| subscribe_orderbook | 订阅订单簿 |

| subscribe_trades | 订阅成交 |

| subscribe_candles | 订阅 K 线 |

| subscribe_mini_ticker | 订阅迷你 Ticker |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    method = data.get("method", "")
    params = data.get("params", {}) if isinstance(data.get("params"), dict) else data.get("ch", "")

    if "ticker" in method:
        print(f"Ticker: {params}")
    elif "trades" in method:
        print(f"Trade update: {params}")
    elif "orderbook" in method:
        print(f"Book update: {params}")

def on_open(ws):

# 订阅 Ticker
    ws.send(json.dumps({
        "method": "subscribe",
        "ch": "ticker/1s",
        "params": {"symbols": ["BTCUSDT"]},
        "id": 1
    }))

# 订阅成交
    ws.send(json.dumps({
        "method": "subscribe",
        "ch": "trades",
        "params": {"symbols": ["BTCUSDT"]},
        "id": 2
    }))

# 订阅订单簿
    ws.send(json.dumps({
        "method": "subscribe",
        "ch": "orderbook/full",
        "params": {"symbols": ["BTCUSDT"]},
        "id": 3
    }))

ws = websocket.WebSocketApp(
    "wss://api.hitbtc.com/api/3/ws/public",
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

| 403 | 权限不足 |

| 404 | 资源不存在 |

| 429 | 速率限制 |

| 500 | 服务器错误 |

### 业务错误码

| 错误码 | 描述 |

|--------|------|

| 20001 | 余额不足 |

| 20002 | 订单不存在 |

| 20003 | 订单数量无效 |

| 20004 | 订单价格无效 |

| 20008 | 超过最大订单数 |

| 10001 | 认证失败 |

| 10020 | 权限不足 |

### Python 错误处理

```python
def safe_hitbtc_request(method_func, path, **kwargs):
    """带错误处理的请求"""
    try:
        result = method_func(path, **kwargs)
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            print(f"HitBTC Error [{err.get('code')}]: {err.get('message')} - {err.get('description', '')}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 V3 REST API 端点说明
- 添加 Basic Auth 认证方式及 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K 线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket (公共/交易/钱包) 频道订阅示例
- 添加账户管理 API 端点概览
- 添加错误代码表和错误处理

- --

## 相关资源

- [HitBTC API V3 文档](<https://api.hitbtc.com/)>
- [HitBTC Demo API](<https://api.demo.hitbtc.com/)>
- [HitBTC 官网](<https://hitbtc.com)>
- [CCXT HitBTC 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 HitBTC 官方 API V3 文档整理。*
