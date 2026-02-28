# Bithumb Global API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1.0.0
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://github.com/bithumb-pro/bithumb.pro-official-api-docs>

## 交易所基本信息

- 官方名称: Bithumb Global
- 官网: <https://www.bithumb.pro>
- 交易所类型: CEX (中心化交易所)
- 总部: 韩国
- 支持的交易对: 200+ (USDT, BTC 计价)
- 支持的交易类型: 现货(Spot)、合约(Contract，已弃用)
- 法币支持: KRW (韩元，仅韩国站)
- 官方 SDK: Java, Python, C++, Go, PHP

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://global-openapi.bithumb.pro/openapi/v1`> | 主端点 |

| WebSocket | `wss://global-api.bithumb.pro/message/realtime` | 实时数据 |

## 认证方式

### API 密钥获取

1. 注册 Bithumb Global 账户并完成 KYC
2. 在网站申请 API Key 和 Secret Key
3. 设置 IP 白名单和权限

### HmacSHA256 签名

- *签名步骤**:
1. 将所有请求参数按字母顺序排列
2. 用 `&` 连接为查询字符串: `apiKey=XXX&msgNo=123&timestamp=1534892332334`
3. 使用 Secret Key 进行 HmacSHA256 签名（小写十六进制）
4. 将 `signature` 添加到请求参数中

- *公共请求参数** (认证接口必需):

| 参数 | 描述 | 必需 |

|------|------|------|

| apiKey | API Key | 是 |

| timestamp | 毫秒级时间戳 | 是 |

| msgNo | 请求唯一标识 (≤50 字符) | 是 |

| signature | HmacSHA256 签名 | 是 |

### Python 签名示例

```python
import hmac
import hashlib
import time
import uuid
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://global-openapi.bithumb.pro/openapi/v1">

def generate_signature(params, secret_key):
    """生成 HmacSHA256 签名"""
    sorted_params = sorted(params.items())
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
    signature = hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def private_request(method, path, extra_params=None):
    """发送签名请求"""
    params = {
        "apiKey": API_KEY,
        "timestamp": str(int(time.time() *1000)),
        "msgNo": str(uuid.uuid4()).replace("-", "")[:32],
    }
    if extra_params:
        params.update(extra_params)

    params["signature"] = generate_signature(params, SECRET_KEY)
    url = f"{BASE_URL}{path}"

    if method == "GET":
        resp = requests.get(url, params=params)
    else:
        resp = requests.post(url, json=params, headers={"Content-Type": "application/json"})
    return resp.json()

# 测试

result = private_request("POST", "/spot/assetList", {"assetType": "spot"})
print(result)

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取 Ticker

- *端点**: `GET /spot/ticker`

- *参数**: `symbol` (必需, 如 `BTC-USDT`，传 `ALL` 获取全部)

```python
resp = requests.get(f"{BASE_URL}/spot/ticker", params={"symbol": "BTC-USDT"})
data = resp.json()
if data["success"]:
    for t in data["data"]:
        print(f"{t['s']}: last={t['c']}, high={t['h']}, low={t['l']}, vol={t['v']}, change={t['p']}")

```bash

- *响应字段**:

| 字段 | 描述 |

|------|------|

| c | 最新价（24h 内） |

| h | 24h 最高价 |

| l | 24h 最低价 |

| p | 24h 涨跌幅 |

| v | 24h 成交量 |

| s | 交易对 |

### 2. 获取订单簿

- *端点**: `GET /spot/orderBook`

- *参数**: `symbol` (必需)

```python
resp = requests.get(f"{BASE_URL}/spot/orderBook", params={"symbol": "BTC-USDT"})
data = resp.json()
if data["success"]:
    book = data["data"]
    print(f"Symbol: {book['symbol']}, Version: {book['ver']}")
    for ask in book["s"][:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}")
    for bid in book["b"][:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}")

```bash

- *响应字段**:

| 字段 | 描述 |

|------|------|

| b | 买单 [price, quantity] |

| s | 卖单 [price, quantity] |

| ver | 版本号 |

| symbol | 交易对 |

### 3. 获取最近成交

- *端点**: `GET /spot/trades`

- *参数**: `symbol` (必需)

```python
resp = requests.get(f"{BASE_URL}/spot/trades", params={"symbol": "BTC-USDT"})
for trade in resp.json()["data"]:
    print(f"{trade['s']} price={trade['p']} qty={trade['v']} time={trade['t']}")

```bash

### 4. 获取 K 线数据

- *端点**: `GET /spot/kline`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| type | STRING | 是 | K 线类型: m1,m3,m5,m15,m30,h1,h2,h4,h6,h8,h12,d1,d3,w1,M1 |

| start | LONG | 是 | 开始时间（秒） |

| end | LONG | 是 | 结束时间（秒） |

```python
import time

end = int(time.time())
start = end - 3600 *24  # 最近 24 小时

resp = requests.get(f"{BASE_URL}/spot/kline", params={
    "symbol": "BTC-USDT",
    "type": "h1",
    "start": start,
    "end": end
})
for candle in resp.json()["data"]:
    print(f"O={candle['o']} H={candle['h']} L={candle['l']} C={candle['c']} V={candle['v']}")

```bash

### 5. 获取交易对配置

- *端点**: `GET /spot/config`

```python
resp = requests.get(f"{BASE_URL}/spot/config")
for pair in resp.json()["data"]["spotConfig"]:
    print(f"{pair['symbol']}: accuracy={pair['accuracy']}")

```bash

## 交易 API

> 以下端点均需 HmacSHA256 签名认证。使用 POST 方法，Content-Type: application/json。

### 1. 下单

- *端点**: `POST /spot/placeOrder`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对，如 BTC-USDT |

| type | STRING | 是 | limit (限价) / market (市价) |

| side | STRING | 是 | buy / sell |

| price | STRING | 是 | 价格 (市价单传 -1) |

| quantity | STRING | 是 | 数量 |

| timestamp | STRING | 是 | 毫秒时间戳 |

```python

# 限价买单

result = private_request("POST", "/spot/placeOrder", {
    "symbol": "BTC-USDT",
    "type": "limit",
    "side": "buy",
    "price": "40000",
    "quantity": "0.001"
})
if result["code"] == "0":
    print(f"Order ID: {result['data']['orderId']}")
else:
    print(f"Error: {result['msg']}")

# 市价买单（quantity 为 USDT 金额）

result = private_request("POST", "/spot/placeOrder", {
    "symbol": "BTC-USDT",
    "type": "market",
    "side": "buy",
    "price": "-1",
    "quantity": "100"
})

```bash

### 2. 撤单

- *端点**: `POST /spot/cancelOrder`

- *参数**: `orderId` (必需), `symbol` (必需)

```python
result = private_request("POST", "/spot/cancelOrder", {
    "orderId": "23132134242",
    "symbol": "BTC-USDT"
})
print(f"Cancel: {result['msg']}")

```bash

### 3. 查询订单详情

- *端点**: `POST /spot/orderDetail`

- *参数**: `orderId`, `symbol`, `page` (可选), `count` (可选, 默认 10)

```python
detail = private_request("POST", "/spot/orderDetail", {
    "orderId": "23132134242",
    "symbol": "BTC-USDT"
})
if detail["code"] == "0":
    for trade in detail["data"]["list"]:
        print(f"Price={trade['price']}, Get={trade['getCount']}, Fee={trade['fee']}")

```bash

### 4. 查询单个订单

- *端点**: `POST /spot/singleOrder`

- *参数**: `orderId`, `symbol`

```python
order = private_request("POST", "/spot/singleOrder", {
    "orderId": "23132134242",
    "symbol": "BTC-USDT"
})
if order["code"] == "0":
    o = order["data"]
    print(f"Status={o['status']}, Side={o['side']}, Traded={o['tradedNum']}/{o['quantity']}")

```bash

### 5. 查询历史订单

- *端点**: `POST /spot/orderList`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| side | STRING | 是 | buy / sell |

| symbol | STRING | 是 | 交易对 |

| status | STRING | 是 | traded (历史订单) |

| queryRange | STRING | 是 | thisweek (7 天内) / thisweekago (7 天前) |

| page | STRING | 否 | 页码 |

| count | STRING | 否 | 每页数量 |

- *订单状态枚举**:
- `send` - 已发送
- `pending` - 挂单中
- `success` - 完全成交
- `cancel` - 已撤销

```python
orders = private_request("POST", "/spot/orderList", {
    "side": "buy",
    "symbol": "BTC-USDT",
    "status": "traded",
    "queryRange": "thisweek"
})
if orders["code"] == "0":
    for o in orders["data"]["list"]:
        print(f"ID:{o['orderId']} {o['side']} {o['type']} price={o['price']} "
              f"traded={o['tradedNum']}/{o['quantity']} status={o['status']}")

```bash

## 账户管理 API

### 1. 查询资产

- *端点**: `POST /spot/assetList`

- *参数**: `assetType` (必需, `spot` 或 `wallet`), `coinType` (可选)

```python
assets = private_request("POST", "/spot/assetList", {"assetType": "spot"})
if assets["code"] == "0":
    for a in assets["data"]:
        if float(a["count"]) > 0 or float(a["frozen"]) > 0:
            print(f"{a['coinType']}: available={a['count']}, frozen={a['frozen']}")

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 下单/撤单 | 10 次/秒 | 每个 API Key |

| 公共接口 | 较宽松 | 行情数据 |

### 最佳实践

- 使用 WebSocket 获取实时行情数据，减少 REST 轮询
- 控制下单/撤单频率在 10 次/秒以内
- 使用 `msgNo` 保证请求幂等性
- 首次使用交易 API 前需在网页端签署交易协议

## WebSocket 支持

### 连接信息

- *WebSocket URL**: `wss://global-api.bithumb.pro/message/realtime`

### 支持的订阅

| 频道 | 说明 |

|------|------|

| `TICKER:{symbol}` | Ticker 推送 |

| `ORDERBOOK:{symbol}` | 订单簿推送 |

| `TRADE:{symbol}` | 成交推送 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    topic = data.get("topic", "")
    msg_data = data.get("data", {})

    if "TICKER" in topic:
        print(f"Ticker: {msg_data}")
    elif "ORDERBOOK" in topic:
        print(f"OrderBook update: asks={len(msg_data.get('s', []))}, bids={len(msg_data.get('b', []))}")
    elif "TRADE" in topic:
        print(f"Trade: {msg_data}")

def on_open(ws):

# 订阅 Ticker
    ws.send(json.dumps({
        "cmd": "subscribe",
        "args": ["TICKER:BTC-USDT"]
    }))

# 订阅订单簿
    ws.send(json.dumps({
        "cmd": "subscribe",
        "args": ["ORDERBOOK:BTC-USDT"]
    }))

# 订阅成交
    ws.send(json.dumps({
        "cmd": "subscribe",
        "args": ["TRADE:BTC-USDT"]
    }))

ws = websocket.WebSocketApp(
    "wss://global-api.bithumb.pro/message/realtime",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever()

```bash

## 错误代码

| 错误码 | 描述 | 说明 |

|--------|------|------|

| 0 | success | 成功 |

| 9000 | missing parameter | apiKey 或 signature 缺失 |

| 9001 | version not matched | 版本不匹配 |

| 9002 | verifySignature failed | 签名验证失败 |

| 9004 | access denied | IP 白名单/权限/账户状态问题 |

| 9005 | key expired | API Key 已过期 |

| 9007 | request invalid | 时间戳异常或 msgNo 过长 |

| 9008 | params error | 请求参数错误 |

| 9010 | access denied (IP) | IP 不在白名单 |

| 9011 | access denied (permission) | 无 API 权限 |

| 9012 | access denied (account) | 账户异常 |

| 9999 | system error | 系统错误 |

| 20000 | order params error | 订单参数错误 |

| 20002 | account abnormal | 资产账户异常 |

| 20003 | asset not enough | 资产不足 |

| 20004 | order absent | 订单不存在 |

| 20010 | pair closed | 交易对已关闭 |

| 20012 | cancel failed | 订单状态已变更 |

| 20043 | price accuracy wrong | 检查交易对精度配置 |

| 20044 | quantity accuracy wrong | 检查交易对精度配置 |

| 20048 | pair not open | 交易对未开放 |

| 20053 | need sign protocol | 首次交易需在网页端签署协议 |

| 20054 | price out of range | 价格超出范围 |

| 20056 | quantity out of range | 数量超出范围（最大 1 亿） |

### Python 错误处理示例

```python
def safe_request(method, path, extra_params=None):
    """带错误处理的请求"""
    try:
        result = private_request(method, path, extra_params)
        if result.get("code") == "0":
            return result.get("data")

        code = result.get("code", "unknown")
        msg = result.get("msg", "Unknown error")

        error_actions = {
            "9002": "Check signature algorithm",
            "9004": "Check IP whitelist and permissions",
            "20003": "Insufficient balance",
            "20053": "Sign trading protocol on website first",
            "20054": "Order price out of allowed range",
        }
        action = error_actions.get(code, "")
        print(f"API Error [{code}]: {msg}. {action}")
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
import uuid
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://global-openapi.bithumb.pro/openapi/v1">

def sign(params):
    sorted_params = sorted(params.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_params)
    return hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()

def api_post(path, extra=None):
    params = {
        "apiKey": API_KEY,
        "timestamp": str(int(time.time() * 1000)),
        "msgNo": uuid.uuid4().hex[:32],
    }
    if extra:
        params.update(extra)
    params["signature"] = sign(params)
    return requests.post(f"{BASE_URL}{path}", json=params,
                        headers={"Content-Type": "application/json"}).json()

# ===== 公共接口 =====

# Ticker

resp = requests.get(f"{BASE_URL}/spot/ticker", params={"symbol": "ALL"})
for t in resp.json()["data"][:5]:
    print(f"{t['s']}: last={t['c']}, vol={t['v']}")

# 订单簿

resp = requests.get(f"{BASE_URL}/spot/orderBook", params={"symbol": "BTC-USDT"})
book = resp.json()["data"]
print(f"\nBTC-USDT OrderBook: asks={len(book['s'])}, bids={len(book['b'])}")

# 成交记录

resp = requests.get(f"{BASE_URL}/spot/trades", params={"symbol": "BTC-USDT"})
for t in resp.json()["data"][:3]:
    print(f"Trade: {t['s']} price={t['p']} qty={t['v']}")

# K 线

end_ts = int(time.time())
resp = requests.get(f"{BASE_URL}/spot/kline", params={
    "symbol": "BTC-USDT", "type": "h1",
    "start": end_ts - 86400, "end": end_ts
})
print(f"\nKline count: {len(resp.json()['data'])}")

# ===== 私有接口 =====

# 查询资产

assets = api_post("/spot/assetList", {"assetType": "spot"})
if assets["code"] == "0":
    for a in assets["data"]:
        if float(a["count"]) > 0:
            print(f"\n{a['coinType']}: {a['count']} (frozen: {a['frozen']})")

# 限价买单

order = api_post("/spot/placeOrder", {
    "symbol": "BTC-USDT", "type": "limit",
    "side": "buy", "price": "40000", "quantity": "0.001"
})
if order["code"] == "0":
    order_id = order["data"]["orderId"]
    print(f"\nOrder placed: {order_id}")

# 查询订单
    detail = api_post("/spot/singleOrder", {
        "orderId": order_id, "symbol": "BTC-USDT"
    })
    if detail["code"] == "0":
        print(f"Status: {detail['data']['status']}")

# 撤单
    cancel = api_post("/spot/cancelOrder", {
        "orderId": order_id, "symbol": "BTC-USDT"
    })
    print(f"Cancel: {cancel['msg']}")

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加 HmacSHA256 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K 线）详细说明
- 添加交易 API（下单、撤单、查询订单）完整示例
- 添加 WebSocket 订阅示例
- 添加完整错误代码表和错误处理

- --

## 相关资源

- [Bithumb Global API 文档](<https://github.com/bithumb-pro/bithumb.pro-official-api-docs)>
- [Python SDK](<https://github.com/bithumb-pro/python-api-client)>
- [Java SDK](<https://github.com/bithumb-pro/java-api-client)>
- [Bithumb Global 官网](<https://www.bithumb.pro)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Bithumb Global 官方 API 文档整理。*
