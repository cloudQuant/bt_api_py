# Bitrue API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v1/v2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档 (Spot): https://github.com/Bitrue-exchange/Spot-official-api-docs
- 官方文档 (Futures): https://github.com/Bitrue-exchange/USDT-M-Future-open-api-docs

## 交易所基本信息
- 官方名称: Bitrue
- 官网: https://www.bitrue.com
- 交易所类型: CEX (中心化交易所)
- 总部: 新加坡
- 支持的交易对: 700+ (USDT, BTC, ETH, XRP 计价)
- 支持的交易类型: 现货(Spot)、USDT-M 永续合约(Futures)、Coin-M 交割合约(Delivery)
- 手续费: Maker 0.098%, Taker 0.098% (现货基础费率)
- 特点: XRP 生态友好，支持多种 XRP 相关交易对

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| Spot REST API | `https://www.bitrue.com/api/v1` | 现货 V1 |
| Spot REST API V2 | `https://www.bitrue.com/api/v2` | 现货 V2 |
| Futures REST API | `https://fapi.bitrue.com` | USDT-M 合约 |
| Delivery REST API | `https://dapi.bitrue.com` | Coin-M 合约 |
| WebSocket (Spot) | `wss://ws.bitrue.com/kline-api/ws` | 现货行情 |
| WebSocket (Futures) | `wss://fstream.bitrue.com` | 合约行情 |

## 认证方式

### API密钥获取

1. 注册 Bitrue 账户并完成 KYC
2. 进入 API 管理页面
3. 创建 API Key，获取 API Key 和 Secret Key
4. 设置权限（读取、交易、提现）和 IP 白名单

### HMAC SHA256 签名 (Spot)

Bitrue 现货 API 采用与 Binance 类似的签名机制。

**签名步骤**:
1. 将所有请求参数拼接为查询字符串
2. 添加 `timestamp` 参数（毫秒级时间戳）
3. 使用 Secret Key 对查询字符串进行 HMAC SHA256 签名
4. 将签名作为 `signature` 参数追加到查询字符串

**请求头**:

| Header | 描述 |
|--------|------|
| X-MBX-APIKEY | API Key |

### HMAC SHA256 签名 (Futures)

合约 API 的签名方式略有不同。

**请求头**:

| Header | 描述 |
|--------|------|
| X-CH-APIKEY | API Key |
| X-CH-SIGN | HMAC SHA256 签名 |
| X-CH-TS | 毫秒时间戳 |

**签名字符串**: `timestamp + HTTP_METHOD + path + body`

### Python 签名示例 (Spot)

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://www.bitrue.com"

def spot_signed_request(method, path, params=None):
    """发送现货签名请求"""
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)

    query_string = urlencode(params)
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    query_string += f"&signature={signature}"

    headers = {"X-MBX-APIKEY": API_KEY}
    url = f"{BASE_URL}{path}?{query_string}"

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unsupported method: {method}")

    return resp.json()

# 测试：查询账户信息
result = spot_signed_request("GET", "/api/v1/account")
print(result)
```

### Python 签名示例 (Futures)

```python
import hmac
import hashlib
import time
import json
import requests

FAPI_KEY = "your_api_key"
FAPI_SECRET = "your_secret_key"
FAPI_URL = "https://fapi.bitrue.com"

def futures_signed_request(method, path, body=None):
    """发送合约签名请求"""
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""

    sign_str = timestamp + method.upper() + path + body_str
    signature = hmac.new(
        FAPI_SECRET.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-CH-APIKEY": FAPI_KEY,
        "X-CH-SIGN": signature,
        "X-CH-TS": timestamp,
        "Content-Type": "application/json",
    }

    url = f"{FAPI_URL}{path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    else:
        resp = requests.post(url, headers=headers, data=body_str)
    return resp.json()
```

## 市场数据API (Spot)

> 公共 API 无需认证。

### 1. 获取交易对信息

**端点**: `GET /api/v1/exchangeInfo`

```python
resp = requests.get(f"{BASE_URL}/api/v1/exchangeInfo")
info = resp.json()
for s in info["symbols"][:5]:
    print(f"{s['symbol']}: base={s['baseAsset']}, quote={s['quoteAsset']}, status={s['status']}")
```

### 2. 获取订单簿

**端点**: `GET /api/v1/depth`

**参数**: `symbol` (必需), `limit` (可选, 默认100, 最大1000)

```python
resp = requests.get(f"{BASE_URL}/api/v1/depth", params={"symbol": "BTCUSDT", "limit": 10})
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, qty={ask[1]}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid[0]}, qty={bid[1]}")
```

### 3. 获取最近成交

**端点**: `GET /api/v1/trades`

**参数**: `symbol` (必需), `limit` (可选, 默认500, 最大1000)

```python
resp = requests.get(f"{BASE_URL}/api/v1/trades", params={"symbol": "BTCUSDT", "limit": 10})
for trade in resp.json():
    print(f"Price={trade['price']}, Qty={trade['qty']}, Time={trade['time']}")
```

### 4. 获取 Ticker

**端点**: `GET /api/v1/ticker/24hr`

**参数**: `symbol` (可选, 不传返回全部)

```python
resp = requests.get(f"{BASE_URL}/api/v1/ticker/24hr", params={"symbol": "BTCUSDT"})
ticker = resp.json()
print(f"BTC/USDT: Last={ticker['lastPrice']}, High={ticker['highPrice']}, Low={ticker['lowPrice']}")
print(f"Volume={ticker['volume']}, Change={ticker['priceChangePercent']}%")
```

### 5. 获取K线数据

**端点**: `GET /api/v1/market/kline`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| scale | STRING | 是 | K线周期: 1/5/15/30/60/240/1D/1W |
| limit | INT | 否 | 数量 |

```python
resp = requests.get(f"{BASE_URL}/api/v1/market/kline", params={
    "symbol": "BTCUSDT",
    "scale": "60",
    "limit": 24
})
for candle in resp.json():
    print(f"O={candle[1]} H={candle[2]} L={candle[3]} C={candle[4]} V={candle[5]}")
```

## 交易API (Spot)

> 以下端点均需签名认证。

### 1. 查询账户信息

**端点**: `GET /api/v1/account`

```python
account = spot_signed_request("GET", "/api/v1/account")
for b in account.get("balances", []):
    free = float(b["free"])
    locked = float(b["locked"])
    if free > 0 or locked > 0:
        print(f"{b['asset']}: free={b['free']}, locked={b['locked']}")
```

### 2. 下单

**端点**: `POST /api/v1/order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| side | ENUM | 是 | BUY / SELL |
| type | ENUM | 是 | LIMIT / MARKET |
| quantity | DECIMAL | 是 | 数量 |
| price | DECIMAL | 条件 | 价格（LIMIT 必需） |
| timeInForce | ENUM | 条件 | GTC / IOC / FOK（LIMIT 必需） |
| newClientOrderId | STRING | 否 | 客户端订单ID |

```python
# 限价买单
order = spot_signed_request("POST", "/api/v1/order", {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "quantity": "0.001",
    "price": "40000",
    "timeInForce": "GTC"
})
print(f"Order ID: {order.get('orderId')}, Status: {order.get('status')}")

# 市价买单
order = spot_signed_request("POST", "/api/v1/order", {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "MARKET",
    "quantity": "0.001"
})
```

### 3. 撤单

**端点**: `DELETE /api/v1/order`

```python
result = spot_signed_request("DELETE", "/api/v1/order", {
    "symbol": "BTCUSDT",
    "orderId": "12345678"
})
print(f"Cancelled: {result}")
```

### 4. 查询订单

**端点**: `GET /api/v1/order`

```python
order = spot_signed_request("GET", "/api/v1/order", {
    "symbol": "BTCUSDT",
    "orderId": "12345678"
})
print(f"Status: {order.get('status')}, Executed: {order.get('executedQty')}/{order.get('origQty')}")
```

### 5. 查询当前挂单

**端点**: `GET /api/v1/openOrders`

```python
orders = spot_signed_request("GET", "/api/v1/openOrders", {"symbol": "BTCUSDT"})
for o in orders:
    print(f"ID:{o['orderId']} {o['side']} {o['type']} price={o['price']} qty={o['origQty']}")
```

### 6. 查询历史订单

**端点**: `GET /api/v1/allOrders`

### 7. 查询成交记录

**端点**: `GET /api/v1/myTrades`

## 合约交易API (Futures)

### 1. 查询合约列表

**端点**: `GET /fapi/v1/contracts`

### 2. 合约下单

**端点**: `POST /fapi/v1/order`

```python
order = futures_signed_request("POST", "/fapi/v1/order", {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "volume": "1",
    "price": "40000",
    "open": "OPEN",  # OPEN=开仓, CLOSE=平仓
    "leverage": "10"
})
print(f"Futures order: {order}")
```

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 现货公共 | 1200 次/分钟 | 按 IP |
| 现货交易 | 100 次/10秒 | 按 UID |
| 合约 | 按端点不同 | 参考官方文档 |

### 最佳实践

- 使用 WebSocket 获取实时行情
- 控制请求频率
- 使用 `newClientOrderId` 实现幂等性
- 使用沙盒环境进行测试

## WebSocket支持

### 现货 WebSocket

**URL**: `wss://ws.bitrue.com/kline-api/ws`

**支持频道**:
- `market_{symbol}_ticker` - Ticker
- `market_{symbol}_depth_step0` - 订单簿
- `market_{symbol}_trade_ticker` - 成交

### Python WebSocket 示例

```python
import websocket
import json
import gzip

def on_message(ws, message):
    # 数据经 gzip 压缩
    data = json.loads(gzip.decompress(message).decode('utf-8'))

    if "ping" in data:
        ws.send(json.dumps({"pong": data["ping"]}))
        return

    channel = data.get("channel", "")
    tick = data.get("tick", {})

    if "ticker" in channel:
        print(f"Ticker: last={tick.get('close')}, vol={tick.get('vol')}")
    elif "depth" in channel:
        print(f"Depth: asks={len(tick.get('asks', []))}, bids={len(tick.get('buys', []))}")
    elif "trade" in channel:
        for t in tick.get("data", []):
            print(f"Trade: price={t['price']}, vol={t['vol']}, side={t['side']}")

def on_open(ws):
    # 订阅 Ticker
    ws.send(json.dumps({
        "event": "sub",
        "params": {"channel": "market_btcusdt_ticker", "cb_id": "btc_ticker"}
    }))
    # 订阅深度
    ws.send(json.dumps({
        "event": "sub",
        "params": {"channel": "market_btcusdt_depth_step0", "cb_id": "btc_depth"}
    }))

ws = websocket.WebSocketApp(
    "wss://ws.bitrue.com/kline-api/ws",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()
```

## 错误代码

### 常见错误码

| 错误码 | 描述 |
|--------|------|
| -1000 | 未知错误 |
| -1001 | 内部错误 |
| -1002 | 未授权 |
| -1013 | 数量过滤器不通过 |
| -1015 | 请求过多 |
| -1021 | 时间戳超出范围 |
| -1022 | 签名无效 |
| -1100 | 非法字符 |
| -1102 | 必填参数缺失 |
| -2010 | 订单拒绝 |
| -2011 | 撤单拒绝 |
| -2013 | 订单不存在 |
| -2014 | API Key 格式错误 |
| -2015 | API Key 或权限无效 |

### Python 错误处理

```python
def safe_spot_request(method, path, params=None):
    """带错误处理的请求"""
    try:
        result = spot_signed_request(method, path, params)
        if isinstance(result, dict) and "code" in result and result["code"] < 0:
            print(f"API Error [{result['code']}]: {result.get('msg', 'Unknown')}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加现货和合约两种 HMAC SHA256 签名方式及 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加合约 API 基本说明
- 添加 WebSocket (gzip 压缩) 订阅示例
- 添加错误代码表和错误处理

---

## 相关资源

- [Bitrue Spot API 文档](https://github.com/Bitrue-exchange/Spot-official-api-docs)
- [Bitrue Futures API 文档](https://github.com/Bitrue-exchange/USDT-M-Future-open-api-docs)
- [Bitrue 官网](https://www.bitrue.com)
- [CCXT Bitrue 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Bitrue 官方 API 文档整理。*
