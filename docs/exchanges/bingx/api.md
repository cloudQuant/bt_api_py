# BingX API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V2 (建议)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://bingx-api.github.io/docs/

## 交易所基本信息
- 官方名称: BingX
- 官网: https://bingx.com
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #19 ($400M+)
- 支持的交易对: 300+
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Swap)、标准合约(Standard Contract)、跟单交易(Copy Trading)
- 手续费: Maker 0.1%, Taker 0.1% (现货)

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://open-api.bingx.com` | 主端点 |
| WebSocket 行情 | `wss://open-api-ws.bingx.com/market` | 公共行情流 |
| WebSocket 交易 | `wss://open-api-ws.bingx.com/swap` | 合约交易流 |

## 认证方式

### API密钥获取

1. 注册 BingX 账户并完成 KYC
2. 进入 用户中心 > API 管理
3. 创建 API Key，获取 API Key 和 Secret Key
4. 设置权限（读取、交易、提现）和 IP 白名单

### HMAC SHA256 签名

**签名步骤**:
1. 将所有请求参数按照参数名 ASCII 排序，拼接成查询字符串
2. 添加 `timestamp` 参数（毫秒级时间戳）
3. 使用 Secret Key 对查询字符串进行 HMAC SHA256 签名
4. 将签名作为 `signature` 参数添加到请求中

**Python 签名示例**:

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://open-api.bingx.com"

def generate_signature(params, secret_key):
    """生成 HMAC SHA256 签名"""
    query_string = urlencode(sorted(params.items()))
    signature = hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        sha256
    ).hexdigest()
    return signature

def signed_request(method, path, params=None):
    """发送签名请求"""
    if params is None:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    params['signature'] = generate_signature(params, SECRET_KEY)

    headers = {"X-BX-APIKEY": API_KEY}
    url = f"{BASE_URL}{path}"

    if method == "GET":
        resp = requests.get(url, params=params, headers=headers)
    elif method == "POST":
        resp = requests.post(url, params=params, headers=headers)
    elif method == "DELETE":
        resp = requests.delete(url, params=params, headers=headers)
    else:
        raise ValueError(f"Unsupported method: {method}")

    return resp.json()

# 测试：查询账户余额
result = signed_request("GET", "/openApi/spot/v1/account/balance")
print(result)
```

## 市场数据API

### 1. 查询交易对信息

**端点**: `GET /openApi/spot/v1/common/symbols`

**描述**: 获取所有现货交易对信息，无需签名。

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对，如 BTC-USDT |

```python
resp = requests.get(f"{BASE_URL}/openApi/spot/v1/common/symbols")
data = resp.json()
for symbol in data.get("data", {}).get("symbols", []):
    print(f"{symbol['symbol']}: min_qty={symbol.get('minQty')}, tick_size={symbol.get('tickSize')}")
```

### 2. 获取24h行情

**端点**: `GET /openApi/spot/v1/ticker/24hr`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对，不传返回全部 |

**响应示例**:
```json
{
  "code": 0,
  "data": [
    {
      "symbol": "BTC-USDT",
      "openPrice": "95000.00",
      "highPrice": "97000.00",
      "lowPrice": "94500.00",
      "lastPrice": "96500.00",
      "volume": "1234.5678",
      "quoteVolume": "119000000.00",
      "openTime": 1700000000000,
      "closeTime": 1700086400000
    }
  ]
}
```

### 3. 获取最新价格

**端点**: `GET /openApi/spot/v1/ticker/price`

**参数**: `symbol` (必需)

### 4. 获取最优挂单

**端点**: `GET /openApi/spot/v1/ticker/bookTicker`

**参数**: `symbol` (必需)

### 5. 获取深度数据

**端点**: `GET /openApi/spot/v1/market/depth`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对，如 BTC-USDT |
| limit | INT | 否 | 深度档位，默认20，最大1000 |

```python
resp = requests.get(f"{BASE_URL}/openApi/spot/v1/market/depth", params={
    "symbol": "BTC-USDT",
    "limit": 10
})
data = resp.json()["data"]
print(f"Best bid: {data['bids'][0]}")
print(f"Best ask: {data['asks'][0]}")
```

**聚合深度端点**: `GET /openApi/spot/v2/market/depth`

附加参数: `depth` (档位数), `type` (精度: step0~step5)

### 6. 获取K线数据

**端点**: `GET /openApi/spot/v2/market/kline`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| interval | STRING | 是 | K线周期: 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M |
| startTime | LONG | 否 | 起始时间（毫秒） |
| endTime | LONG | 否 | 结束时间（毫秒） |
| limit | INT | 否 | 默认500，最大1440 |

```python
resp = requests.get(f"{BASE_URL}/openApi/spot/v2/market/kline", params={
    "symbol": "BTC-USDT",
    "interval": "1h",
    "limit": 5
})
for kline in resp.json().get("data", []):
    print(f"Time: {kline[0]}, O: {kline[1]}, H: {kline[2]}, L: {kline[3]}, C: {kline[4]}, Vol: {kline[5]}")
```

### 7. 获取最近成交

**端点**: `GET /openApi/spot/v1/market/trades`

**参数**: `symbol` (必需), `limit` (可选，默认100，最大500)

### 8. 历史K线

**端点**: `GET /openApi/market/his/v1/kline`

**参数**: `symbol`, `interval`, `startTime`, `endTime`, `limit` (最大500)

## 交易API

> 以下端点均需 HMAC SHA256 签名认证。

### 1. 查询账户余额

**端点**: `GET /openApi/spot/v1/account/balance`

```python
result = signed_request("GET", "/openApi/spot/v1/account/balance")
for asset in result.get("data", {}).get("balances", []):
    free = float(asset.get("free", 0))
    locked = float(asset.get("locked", 0))
    if free > 0 or locked > 0:
        print(f"{asset['asset']}: free={free}, locked={locked}")
```

**响应示例**:
```json
{
  "code": 0,
  "data": {
    "balances": [
      {"asset": "BTC", "free": "0.5", "locked": "0.01"},
      {"asset": "USDT", "free": "10000.00", "locked": "500.00"}
    ]
  }
}
```

### 2. 下单

**端点**: `POST /openApi/spot/v1/trade/order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对，如 BTC-USDT |
| side | ENUM | 是 | BUY / SELL |
| type | ENUM | 是 | MARKET / LIMIT |
| quantity | DECIMAL | 条件 | 数量（LIMIT 必需，MARKET SELL 必需） |
| quoteOrderQty | DECIMAL | 条件 | 报价资产金额（MARKET BUY 可用） |
| price | DECIMAL | 条件 | 价格（LIMIT 必需） |
| timeInForce | ENUM | 否 | GTC (默认) / IOC / FOK / POST_ONLY |
| newClientOrderId | STRING | 否 | 客户自定义订单ID |

**Python示例**:
```python
# 限价买单
result = signed_request("POST", "/openApi/spot/v1/trade/order", {
    "symbol": "BTC-USDT",
    "side": "BUY",
    "type": "LIMIT",
    "price": "90000",
    "quantity": "0.001",
    "timeInForce": "GTC"
})
print(f"Order ID: {result['data']['orderId']}")

# 市价买单（按金额）
result = signed_request("POST", "/openApi/spot/v1/trade/order", {
    "symbol": "ETH-USDT",
    "side": "BUY",
    "type": "MARKET",
    "quoteOrderQty": "100"
})
print(f"Market order: {result['data']['orderId']}")
```

**响应示例**:
```json
{
  "code": 0,
  "data": {
    "symbol": "BTC-USDT",
    "orderId": 1234567890,
    "transactTime": 1700000000000,
    "price": "90000",
    "origQty": "0.001",
    "executedQty": "0",
    "type": "LIMIT",
    "side": "BUY",
    "status": "NEW"
  }
}
```

### 3. 撤单

**端点**: `POST /openApi/spot/v1/trade/cancel`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| orderId | LONG | 是 | 订单ID |

```python
result = signed_request("POST", "/openApi/spot/v1/trade/cancel", {
    "symbol": "BTC-USDT",
    "orderId": 1234567890
})
print(result)
```

### 4. 批量撤单

**端点**: `POST /openApi/spot/v1/trade/cancelOrders`

**参数**: `symbol` (必需)

### 5. 查询订单详情

**端点**: `GET /openApi/spot/v1/trade/query`

**参数**: `symbol` (必需), `orderId` (必需)

### 6. 查询未成交订单

**端点**: `GET /openApi/spot/v1/trade/openOrders`

**参数**: `symbol` (可选)

### 7. 查询历史订单

**端点**: `GET /openApi/spot/v1/trade/historyOrders`

**参数**: `symbol` (必需), `orderId` (可选), `startTime`, `endTime`, `limit`

### 8. 查询成交历史

**端点**: `GET /openApi/spot/v1/trade/myTrades`

**参数**: `symbol`, `orderId`, `startTime`, `endTime`, `fromId`, `limit`

## 账户管理API

### 1. 账户总览

**端点**: `GET /openApi/account/v1/allAccountBalance`

**参数**: `accountType` (可选: fund, standard_contract, perpetual_contract, spot, copy_trading)

### 2. 资产划转

**端点**: `POST /openApi/api/v3/post/asset/transfer`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| type | ENUM | 是 | 划转方向 (FUND_SFUTURES / SFUTURES_FUND / FUND_PFUTURES / PFUTURES_FUND / SFUTURES_PFUTURES / PFUTURES_SFUTURES) |
| asset | STRING | 是 | 币种，如 USDT |
| amount | DECIMAL | 是 | 划转数量 |

### 3. 划转记录

**端点**: `GET /openApi/api/v3/asset/transfer`

**参数**: `type`, `startTime`, `endTime`, `current` (页码), `size` (每页数量)

### 4. 充值记录

**端点**: `GET /openApi/api/v3/capital/deposit/hisrec`

### 5. 提现记录

**端点**: `GET /openApi/api/v3/capital/withdraw/history`

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 全局 | 根据端点不同 | 每个端点有独立限制 |
| 市场数据 | 较宽松 | 公共接口限制较高 |
| 交易接口 | 较严格 | 防止过度交易 |
| WebSocket | 连接数有限 | 建议单连接多订阅 |

### 最佳实践

- 所有请求添加 `timestamp` 参数
- 使用 `recvWindow` 参数控制请求有效时间窗口（默认5000ms，最大60000ms）
- 监控返回的速率限制信息
- 批量操作优先使用批量接口

```python
import time

class BingXRateLimiter:
    def __init__(self, requests_per_second=10):
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0

    def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()
```

## WebSocket支持

### 公共频道

**连接地址**: `wss://open-api-ws.bingx.com/market`

**支持的频道**:
- **Trade**: 实时成交
- **Kline**: K线数据
- **Depth**: 深度数据
- **Ticker**: 24h行情

### Python WebSocket 示例

```python
import websocket
import json
import gzip
import io

def on_message(ws, message):
    # BingX 使用 gzip 压缩
    buf = io.BytesIO(message)
    with gzip.GzipFile(fileobj=buf) as f:
        data = json.loads(f.read().decode('utf-8'))

    # 心跳响应
    if "ping" in str(data):
        ws.send(json.dumps({"pong": data.get("ping")}))
        return

    print(data)

def on_open(ws):
    # 订阅 BTC-USDT 深度
    sub_msg = {
        "id": "depth1",
        "reqType": "sub",
        "dataType": "BTC-USDT@depth5@500ms"
    }
    ws.send(json.dumps(sub_msg))

    # 订阅 BTC-USDT K线
    sub_kline = {
        "id": "kline1",
        "reqType": "sub",
        "dataType": "BTC-USDT@kline_1m"
    }
    ws.send(json.dumps(sub_kline))

    # 订阅 BTC-USDT 成交
    sub_trade = {
        "id": "trade1",
        "reqType": "sub",
        "dataType": "BTC-USDT@trade"
    }
    ws.send(json.dumps(sub_trade))

ws = websocket.WebSocketApp(
    "wss://open-api-ws.bingx.com/market",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()
```

### 订阅格式

| 数据类型 | dataType 格式 | 说明 |
|---------|--------------|------|
| 深度 | `{symbol}@depth{level}@{interval}` | level: 5/10/20/50/100, interval: 100ms/200ms/500ms/1000ms |
| K线 | `{symbol}@kline_{period}` | period: 1m,3m,5m,15m,30m,1h,2h,4h,6h,12h,1d,1w |
| 成交 | `{symbol}@trade` | 实时成交推送 |
| 24h行情 | `{symbol}@ticker` | 24h行情推送 |

### 心跳机制

- 服务器定期发送 `ping` 消息
- 客户端需回复 `pong` 消息
- 超时未回复将断开连接

## 错误代码

### 通用错误码

| 错误码 | HTTP状态码 | 描述 |
|--------|-----------|------|
| 0 | 200 | 成功 |
| 100001 | 400 | 签名验证失败 (Signature verification failed) |
| 100002 | 401 | 参数错误 (Parameter error) |
| 100003 | 401 | API Key 不存在 |
| 100004 | 403 | IP 不在白名单 |
| 100005 | 429 | 请求频率超限 |
| 100006 | 400 | 请求时间戳过期 |
| 100400 | 400 | 请求参数错误 |
| 100440 | 400 | 订单价格超出限制 |
| 100500 | 500 | 服务器内部错误 |
| 100503 | 503 | 服务不可用 |

### 交易错误码

| 错误码 | 描述 |
|--------|------|
| 80001 | 余额不足 (Insufficient balance) |
| 80002 | 订单不存在 (Order not found) |
| 80003 | 交易对不存在 (Symbol not found) |
| 80004 | 订单数量不符合最小要求 |
| 80005 | 订单价格超出精度 |
| 80012 | 委托数量超限 |
| 80014 | 订单已被撤销 |

### 错误响应格式

```json
{
  "code": 100001,
  "msg": "Signature verification failed",
  "data": null
}
```

### Python 错误处理示例

```python
def safe_request(method, path, params=None):
    """带错误处理的请求"""
    try:
        result = signed_request(method, path, params)
        code = result.get("code", -1)

        if code == 0:
            return result.get("data")

        msg = result.get("msg", "Unknown error")
        print(f"API Error [{code}]: {msg}")

        if code == 100005:
            print("Rate limited, waiting...")
            time.sleep(2)
            return safe_request(method, path, params)
        elif code == 100006:
            print("Timestamp expired, retrying with fresh timestamp...")
            return safe_request(method, path, params)
        elif code == 80001:
            print("Insufficient balance")

        return None

    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        return None
```

## 代码示例

### Python 完整交易示例

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://open-api.bingx.com"

def generate_signature(params, secret_key):
    query_string = urlencode(sorted(params.items()))
    return hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        sha256
    ).hexdigest()

def signed_request(method, path, params=None):
    if params is None:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    params['signature'] = generate_signature(params, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    elif method == "POST":
        return requests.post(url, params=params, headers=headers).json()
    elif method == "DELETE":
        return requests.delete(url, params=params, headers=headers).json()

# ===== 公共接口（无需签名）=====

def get_symbols():
    """获取交易对列表"""
    return requests.get(f"{BASE_URL}/openApi/spot/v1/common/symbols").json()

def get_ticker(symbol):
    """获取24h行情"""
    return requests.get(f"{BASE_URL}/openApi/spot/v1/ticker/24hr",
                       params={"symbol": symbol}).json()

def get_depth(symbol, limit=20):
    """获取深度"""
    return requests.get(f"{BASE_URL}/openApi/spot/v1/market/depth",
                       params={"symbol": symbol, "limit": limit}).json()

def get_klines(symbol, interval="1h", limit=100):
    """获取K线"""
    return requests.get(f"{BASE_URL}/openApi/spot/v2/market/kline",
                       params={"symbol": symbol, "interval": interval, "limit": limit}).json()

# ===== 私有接口（需签名）=====

def get_balance():
    """查询账户余额"""
    return signed_request("GET", "/openApi/spot/v1/account/balance")

def place_order(symbol, side, order_type, quantity=None, price=None, quote_qty=None):
    """下单"""
    params = {"symbol": symbol, "side": side, "type": order_type}
    if quantity:
        params["quantity"] = str(quantity)
    if price:
        params["price"] = str(price)
    if quote_qty:
        params["quoteOrderQty"] = str(quote_qty)
    return signed_request("POST", "/openApi/spot/v1/trade/order", params)

def cancel_order(symbol, order_id):
    """撤单"""
    return signed_request("POST", "/openApi/spot/v1/trade/cancel",
                         {"symbol": symbol, "orderId": order_id})

def get_open_orders(symbol=None):
    """查询未成交订单"""
    params = {}
    if symbol:
        params["symbol"] = symbol
    return signed_request("GET", "/openApi/spot/v1/trade/openOrders", params)

# ===== 使用示例 =====

# 获取 BTC-USDT 行情
ticker = get_ticker("BTC-USDT")
if ticker.get("code") == 0:
    for t in ticker["data"]:
        print(f"BTC-USDT Last: {t['lastPrice']}, Vol: {t['volume']}")

# 获取深度
depth = get_depth("BTC-USDT", limit=5)
if depth.get("code") == 0:
    d = depth["data"]
    print(f"Best bid: {d['bids'][0]}")
    print(f"Best ask: {d['asks'][0]}")

# 获取K线
klines = get_klines("BTC-USDT", "1h", 3)
print(f"Recent klines: {klines}")

# 查询余额
balance = get_balance()
if balance.get("code") == 0:
    for b in balance["data"].get("balances", []):
        if float(b.get("free", 0)) > 0:
            print(f"{b['asset']}: {b['free']}")

# 限价买单
order = place_order("BTC-USDT", "BUY", "LIMIT", quantity="0.001", price="90000")
print(f"Order: {order}")
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC SHA256 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、深度、K线、成交）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket 订阅示例（含 gzip 解压和心跳）
- 添加速率限制和错误码表
- 添加账户管理和资产划转端点

---

## 相关资源

- [BingX 官方 API 文档](https://bingx-api.github.io/docs/)
- [BingX 官网](https://bingx.com)
- [BingX GitHub](https://github.com/BingX-API)
- [BingX Python SDK (bingx-py)](https://pypi.org/project/bingx-py/)
- [CCXT BingX 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 BingX 官方 API 文档整理。*
