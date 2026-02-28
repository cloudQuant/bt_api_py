# CoinEx API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://docs.coinex.com/api/v2/>
- 认证文档: <https://docs.coinex.com/api/v2/authorization>

## 交易所基本信息

- 官方名称: CoinEx
- 官网: <https://www.coinex.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 香港
- 支持的交易对: 700+ (USDT, BTC, USDC 计价)
- 支持的交易类型: 现货(Spot)、保证金(Margin)、永续合约(Futures)
- 手续费: Maker 0.20%, Taker 0.20% (现货基础费率，VIP 阶梯)
- 特点: 无 KYC 即可交易，支持 CET 抵扣手续费

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.coinex.com`> | 主端点 |

| WebSocket | `wss://socket.coinex.com/v2/` | 实时数据 |

## 认证方式

### API 密钥获取

1. 登录 CoinEx 账户
2. 进入 API 管理页面
3. 创建 API Key，获取 `access_id` 和 `secret_key`
4. 设置权限和 IP 白名单

### HMAC-SHA256 签名

- *签名步骤**:
1. 拼接签名字符串: `METHOD + request_path(含 query_string) + body(可选) + timestamp`
2. 使用 `secret_key` 作为密钥进行 HMAC-SHA256 签名
3. 将签名转为小写十六进制（64 字符）

- *请求头**:

| Header | 描述 |

|--------|------|

| X-COINEX-KEY | access_id |

| X-COINEX-SIGN | HMAC-SHA256 签名 |

| X-COINEX-TIMESTAMP | 毫秒级时间戳 |

| Content-Type | application/json（POST 请求） |

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

ACCESS_ID = "your_access_id"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://api.coinex.com">

def coinex_request(method, path, params=None, body=None):
    """发送 CoinEx 签名请求"""
    timestamp = str(int(time.time() *1000))

# 构建 request_path
    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{query}"
    else:
        request_path = path

# 构建签名字符串
    body_str = json.dumps(body) if body else ""
    prepared_str = method.upper() + request_path + body_str + timestamp

# HMAC-SHA256 签名
    signature = hmac.new(
        bytes(SECRET_KEY, 'latin-1'),
        msg=bytes(prepared_str, 'latin-1'),
        digestmod=hashlib.sha256
    ).hexdigest().lower()

    headers = {
        "X-COINEX-KEY": ACCESS_ID,
        "X-COINEX-SIGN": signature,
        "X-COINEX-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{request_path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()

# 测试：查询账户信息

result = coinex_request("GET", "/v2/assets/spot/balance")
print(result)

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取市场列表

- *端点**: `GET /v2/spot/market`

```python
resp = requests.get(f"{BASE_URL}/v2/spot/market")
data = resp.json()
if data["code"] == 0:
    for m in data["data"][:5]:
        print(f"{m['market']}: base={m['base_ccy']}, quote={m['quote_ccy']}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /v2/spot/ticker`

- *参数**: `market` (可选, 如 `BTCUSDT`，不传返回全部)

```python

# 单个

resp = requests.get(f"{BASE_URL}/v2/spot/ticker", params={"market": "BTCUSDT"})
data = resp.json()
if data["code"] == 0:
    for t in data["data"]:
        print(f"{t['market']}: last={t['last']}, vol={t['volume']}, "
              f"high={t['high']}, low={t['low']}")

# 全部

resp = requests.get(f"{BASE_URL}/v2/spot/ticker")
print(f"Total markets: {len(resp.json()['data'])}")

```bash

### 3. 获取订单簿

- *端点**: `GET /v2/spot/depth`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| market | STRING | 是 | 交易对，如 BTCUSDT |

| limit | INT | 否 | 深度档数 (5/10/20/50, 默认 20) |

| interval | STRING | 否 | 合并精度 |

```python
resp = requests.get(f"{BASE_URL}/v2/spot/depth", params={
    "market": "BTCUSDT", "limit": 10
})
data = resp.json()
if data["code"] == 0:
    depth = data["data"]["depth"]
    for ask in depth["asks"][:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}")
    for bid in depth["bids"][:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}")

```bash

### 4. 获取最近成交

- *端点**: `GET /v2/spot/deals`

- *参数**: `market` (必需), `limit` (可选, 默认 100, 最大 1000)

```python
resp = requests.get(f"{BASE_URL}/v2/spot/deals", params={
    "market": "BTCUSDT", "limit": 10
})
for trade in resp.json()["data"]:
    print(f"Price={trade['price']}, Amount={trade['amount']}, Side={trade['side']}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /v2/spot/kline`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| market | STRING | 是 | 交易对 |

| period | STRING | 是 | 1min/3min/5min/15min/30min/1hour/2hour/4hour/6hour/12hour/1day/3day/1week |

| limit | INT | 否 | 数量（默认 100） |

```python
resp = requests.get(f"{BASE_URL}/v2/spot/kline", params={
    "market": "BTCUSDT",
    "period": "1hour",
    "limit": 24
})
for candle in resp.json()["data"]:
    print(f"O={candle['open']} H={candle['high']} L={candle['low']} "
          f"C={candle['close']} V={candle['volume']}")

```bash

### 6. 获取单个市场信息

- *端点**: `GET /v2/spot/index`

- *参数**: `market` (必需)

## 交易 API

> 以下端点均需签名认证。

### 1. 查询现货余额

- *端点**: `GET /v2/assets/spot/balance`

```python
balances = coinex_request("GET", "/v2/assets/spot/balance")
if balances["code"] == 0:
    for b in balances["data"]:
        available = float(b.get("available", 0))
        frozen = float(b.get("frozen", 0))
        if available > 0 or frozen > 0:
            print(f"{b['ccy']}: available={b['available']}, frozen={b['frozen']}")

```bash

### 2. 下单

- *端点**: `POST /v2/spot/order`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| market | STRING | 是 | 交易对，如 BTCUSDT |

| market_type | STRING | 是 | SPOT |

| side | STRING | 是 | buy / sell |

| type | STRING | 是 | limit / market |

| amount | STRING | 条件 | 数量（limit 必需；market sell 必需） |

| price | STRING | 条件 | 价格（limit 必需） |

| client_id | STRING | 否 | 客户端订单 ID |

```python

# 限价买单

order = coinex_request("POST", "/v2/spot/order", body={
    "market": "BTCUSDT",
    "market_type": "SPOT",
    "side": "buy",
    "type": "limit",
    "amount": "0.001",
    "price": "40000"
})
if order["code"] == 0:
    print(f"Order ID: {order['data']['order_id']}")

# 市价买单 (按金额)

order = coinex_request("POST", "/v2/spot/order", body={
    "market": "BTCUSDT",
    "market_type": "SPOT",
    "side": "buy",
    "type": "market",
    "amount": "100"  # USDT 金额

})

```bash

### 3. 撤单

- *端点**: `DELETE /v2/spot/order`

- *参数**: `market` (必需), `order_id` (必需)

```python
result = coinex_request("DELETE", "/v2/spot/order", params={
    "market": "BTCUSDT",
    "order_id": "123456789"
})
print(f"Cancel: {result}")

```bash

### 4. 批量撤单

- *端点**: `DELETE /v2/spot/order/batch`

```python
result = coinex_request("DELETE", "/v2/spot/order/batch", params={
    "market": "BTCUSDT"
})

```bash

### 5. 查询挂单列表

- *端点**: `GET /v2/spot/pending-order`

- *参数**: `market` (可选), `market_type` (SPOT), `side` (可选), `page`, `limit`

```python
orders = coinex_request("GET", "/v2/spot/pending-order", params={
    "market": "BTCUSDT",
    "market_type": "SPOT"
})
if orders["code"] == 0:
    for o in orders["data"]:
        print(f"ID:{o['order_id']} {o['side']} {o['type']} "
              f"price={o['price']} amount={o['amount']}")

```bash

### 6. 查询历史订单

- *端点**: `GET /v2/spot/finished-order`

### 7. 查询成交记录

- *端点**: `GET /v2/spot/user-deals`

### 8. 批量下单

- *端点**: `POST /v2/spot/order/batch`

## 合约交易 API

### 端点概览

| 端点 | 方法 | 描述 |

|------|------|------|

| /v2/futures/order | POST | 合约下单 |

| /v2/futures/order | DELETE | 合约撤单 |

| /v2/futures/pending-order | GET | 查询挂单 |

| /v2/futures/position | GET | 查询持仓 |

| /v2/assets/futures/balance | GET | 查询合约余额 |

```python

# 合约下单

order = coinex_request("POST", "/v2/futures/order", body={
    "market": "BTCUSDT",
    "market_type": "FUTURES",
    "side": "buy",
    "type": "limit",
    "amount": "1",
    "price": "40000",
    "leverage": "10"
})

```bash

## 账户管理 API

### 端点概览

| 端点 | 方法 | 描述 |

|------|------|------|

| /v2/assets/spot/balance | GET | 现货余额 |

| /v2/assets/futures/balance | GET | 合约余额 |

| /v2/assets/margin/balance | GET | 杠杆余额 |

| /v2/assets/deposit/address | GET | 充值地址 |

| /v2/assets/deposit/history | GET | 充值记录 |

| /v2/assets/withdraw | POST | 提现 |

| /v2/assets/withdraw/history | GET | 提现记录 |

| /v2/assets/transfer | POST | 内部转账 |

| /v2/account/info | GET | 账户信息 |

```python

# 查询账户信息

info = coinex_request("GET", "/v2/account/info")
print(info)

# 查询充值地址

addr = coinex_request("GET", "/v2/assets/deposit/address", params={
    "ccy": "BTC", "chain": "BTC"
})
if addr["code"] == 0:
    print(f"Address: {addr['data']['address']}")

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 公共端点 | 按端点不同 | IP 限制 |

| 私有端点 | 按端点不同 | UID 限制 |

| 下单 | 较高频率限制 | 参考官方文档 |

### 最佳实践

- 使用 WebSocket 获取实时行情，减少 REST 轮询
- timestamp 需在服务器时间 ±30s 范围内
- 使用 `client_id` 实现订单幂等性
- 设置 IP 白名单增强安全性

## WebSocket 支持

### 连接信息

- *WebSocket URL**: `wss://socket.coinex.com/v2/`

### 公共频道

| 频道 | 方法 | 描述 |

|------|------|------|

| state.subscribe | 订阅 | 市场状态（Ticker） |

| deals.subscribe | 订阅 | 实时成交 |

| depth.subscribe | 订阅 | 订单簿 |

| kline.subscribe | 订阅 | K 线数据 |

### Python WebSocket 示例

```python
import websocket
import json
import time

def on_message(ws, message):
    data = json.loads(message)
    method = data.get("method", "")

    if method == "state.update":
        params = data.get("params", [])
        for market, info in params[0].items() if params else []:
            print(f"Ticker {market}: last={info.get('last')}")
    elif method == "deals.update":
        params = data.get("params", [])
        if params:
            market = params[0]
            deals = params[1]
            for d in deals:
                print(f"Trade {market}: price={d['price']}, amount={d['amount']}, type={d['type']}")
    elif method == "depth.update":
        params = data.get("params", [])
        if params:
            is_full = params[0]
            depth = params[1]
            market = params[2]
            print(f"Depth {market}: full={is_full}, asks={len(depth.get('asks', []))}, bids={len(depth.get('bids', []))}")

def on_open(ws):

# 订阅 Ticker
    ws.send(json.dumps({
        "method": "state.subscribe",
        "params": ["BTCUSDT"],
        "id": 1
    }))

# 订阅成交
    ws.send(json.dumps({
        "method": "deals.subscribe",
        "params": ["BTCUSDT"],
        "id": 2
    }))

# 订阅深度 (市场, 档数, 精度, 是否全量)
    ws.send(json.dumps({
        "method": "depth.subscribe",
        "params": ["BTCUSDT", 20, "0", True],
        "id": 3
    }))

ws = websocket.WebSocketApp(
    "wss://socket.coinex.com/v2/",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

### WebSocket 认证

```python
def ws_auth(ws):
    """WebSocket 认证"""
    timestamp = int(time.time() *1000)
    prepared_str = str(timestamp)
    signed_str = hmac.new(
        bytes(SECRET_KEY, 'latin-1'),
        msg=bytes(prepared_str, 'latin-1'),
        digestmod=hashlib.sha256
    ).hexdigest().lower()

    ws.send(json.dumps({
        "method": "server.sign",
        "params": {
            "access_id": ACCESS_ID,
            "signed_str": signed_str,
            "timestamp": timestamp
        },
        "id": 100
    }))

```bash

## 错误代码

### 响应格式

```json
{
  "code": 0,
  "data": { ... },
  "message": "OK"
}

```bash

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 0 | 成功 |

| 1 | 通用错误 |

| 2 | 参数错误 |

| 3 | 内部错误 |

| 23 | IP 不在白名单 |

| 24 | 访问被拒绝 |

| 25 | 签名验证失败 |

| 35 | 余额不足 |

| 36 | 订单数量不合规 |

| 37 | 订单价格不合规 |

| 40 | 订单不存在 |

| 49 | 撤单失败 |

| 107 | 请求频率限制 |

### Python 错误处理

```python
def safe_coinex_request(method, path, params=None, body=None):
    """带错误处理的 CoinEx 请求"""
    try:
        result = coinex_request(method, path, params, body)
        if result.get("code") == 0:
            return result.get("data")

        code = result.get("code")
        msg = result.get("message", "Unknown")
        error_map = {
            25: "Signature verification failed - check secret_key",
            35: "Insufficient balance",
            107: "Rate limited - slow down requests",
        }
        hint = error_map.get(code, "")
        print(f"CoinEx Error [{code}]: {msg}. {hint}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 代码示例

### Python 完整交易示例

```python
import hmac
import hashlib
import time
import json
import requests

ACCESS_ID = "your_access_id"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://api.coinex.com">

def sign_request(method, path, body_str, timestamp):
    prepared = method.upper() + path + body_str + timestamp
    return hmac.new(
        bytes(SECRET_KEY, 'latin-1'),
        msg=bytes(prepared, 'latin-1'),
        digestmod=hashlib.sha256
    ).hexdigest().lower()

def api_get(path, params=None):
    ts = str(int(time.time()*1000))
    rp = path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
    sig = sign_request("GET", rp, "", ts)
    headers = {"X-COINEX-KEY": ACCESS_ID, "X-COINEX-SIGN": sig, "X-COINEX-TIMESTAMP": ts}
    return requests.get(f"{BASE_URL}{rp}", headers=headers).json()

def api_post(path, body=None):
    ts = str(int(time.time()* 1000))
    body_str = json.dumps(body) if body else ""
    sig = sign_request("POST", path, body_str, ts)
    headers = {"X-COINEX-KEY": ACCESS_ID, "X-COINEX-SIGN": sig,
               "X-COINEX-TIMESTAMP": ts, "Content-Type": "application/json"}
    return requests.post(f"{BASE_URL}{path}", headers=headers, data=body_str).json()

# ===== 公共接口 =====

# Ticker

resp = requests.get(f"{BASE_URL}/v2/spot/ticker", params={"market": "BTCUSDT"})
for t in resp.json()["data"]:
    print(f"{t['market']}: last={t['last']}, vol={t['volume']}")

# 订单簿

resp = requests.get(f"{BASE_URL}/v2/spot/depth", params={"market": "BTCUSDT", "limit": 5})
depth = resp.json()["data"]["depth"]
print(f"Best ask: {depth['asks'][0]}, Best bid: {depth['bids'][0]}")

# K 线

resp = requests.get(f"{BASE_URL}/v2/spot/kline", params={
    "market": "BTCUSDT", "period": "1hour", "limit": 5
})
for c in resp.json()["data"]:
    print(f"O={c['open']} H={c['high']} L={c['low']} C={c['close']}")

# ===== 私有接口 =====

# 查询余额

balances = api_get("/v2/assets/spot/balance")
if balances["code"] == 0:
    for b in balances["data"]:
        if float(b.get("available", 0)) > 0:
            print(f"{b['ccy']}: {b['available']}")

# 限价买单

order = api_post("/v2/spot/order", {
    "market": "BTCUSDT", "market_type": "SPOT",
    "side": "buy", "type": "limit",
    "amount": "0.001", "price": "40000"
})
if order["code"] == 0:
    oid = order["data"]["order_id"]
    print(f"Order: {oid}")

# 查询挂单
    pending = api_get("/v2/spot/pending-order", {"market": "BTCUSDT", "market_type": "SPOT"})
    print(f"Pending: {len(pending.get('data', []))}")

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 V2 REST API 端点说明
- 添加 HMAC-SHA256 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K 线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加合约和账户管理 API 端点概览
- 添加 WebSocket (公共/认证) 订阅示例
- 添加错误代码表和错误处理

- --

## 相关资源

- [CoinEx API V2 文档](<https://docs.coinex.com/api/v2/)>
- [CoinEx 认证文档](<https://docs.coinex.com/api/v2/authorization)>
- [CoinEx 官网](<https://www.coinex.com)>
- [CCXT CoinEx 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 CoinEx 官方 API V2 文档整理。*
