# BTCTurk API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: Public V2 / Private V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.btcturk.com/

## 交易所基本信息
- 官方名称: BTCTurk | Kripto
- 官网: https://www.btcturk.com
- 交易所类型: CEX (中心化交易所)
- 总部: 土耳其
- 支持的交易对: 100+ (TRY, USDT 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.10%, Taker 0.20% (基础费率)
- 法币支持: TRY (土耳其里拉)
- 合规: 土耳其首家加密货币交易所 (2013年成立)
- 特点: 土耳其最大的加密货币交易所

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.btcturk.com` | 主端点 |
| WebSocket | `wss://ws-feed-pro.btcturk.com` | 实时数据 |

## 认证方式

### API密钥获取

1. 登录 BTCTurk 账户
2. 进入 Account -> API Access
3. 创建 API Key 并设置权限
4. 保存 Public Key 和 Private Key

### HMAC SHA256 签名

BTCTurk 使用 HMAC SHA256，Private Key 需先 Base64 解码。

**签名步骤**:
1. 生成毫秒时间戳
2. 拼接消息: `publicKey + timestamp`
3. Base64 解码 Private Key 获取密钥字节
4. 使用解码后的密钥对消息进行 HMAC SHA256
5. Base64 编码签名结果

**请求头**:

| Header | 描述 |
|--------|------|
| X-PCK | Public Key |
| X-Stamp | 毫秒时间戳 |
| X-Signature | Base64(HMAC-SHA256(message, Base64Decode(privateKey))) |
| Content-Type | application/json |

### Python 签名示例

```python
import hmac
import hashlib
import base64
import time
import json
import requests

PUBLIC_KEY = "your_public_key"
PRIVATE_KEY = "your_private_key"  # Base64 编码
BASE_URL = "https://api.btcturk.com"

def btcturk_request(method, path, params=None, body=None):
    """发送 BTCTurk 签名请求"""
    timestamp = str(int(time.time() * 1000))
    message = PUBLIC_KEY + timestamp

    # Base64 解码 Private Key
    private_key_bytes = base64.b64decode(PRIVATE_KEY)

    # HMAC SHA256 + Base64 编码
    signature = base64.b64encode(
        hmac.new(
            private_key_bytes,
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
    ).decode('utf-8')

    headers = {
        "X-PCK": PUBLIC_KEY,
        "X-Stamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json",
    }

    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE_URL}{path}?{query}"
    else:
        url = f"{BASE_URL}{path}"

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, json=body)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers, params=params)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取交易所信息

**端点**: `GET /api/v2/server/exchangeinfo`

```python
resp = requests.get(f"{BASE_URL}/api/v2/server/exchangeinfo")
data = resp.json()
if data["success"]:
    for s in data["data"]["symbols"][:5]:
        print(f"{s['name']}: numerator={s['numeratorScale']}, "
              f"denominator={s['denominatorScale']}, status={s['status']}")
```

### 2. 获取 Ticker

**端点**: `GET /api/v2/ticker`

**参数**: `pairSymbol` (可选, 如 `BTCUSDT`)

```python
# 全部
resp = requests.get(f"{BASE_URL}/api/v2/ticker")
for t in resp.json()["data"][:5]:
    print(f"{t['pair']}: last={t['last']}, bid={t['bid']}, ask={t['ask']}, "
          f"high={t['high']}, low={t['low']}, volume={t['volume']}")

# 单个
resp = requests.get(f"{BASE_URL}/api/v2/ticker", params={"pairSymbol": "BTCUSDT"})
```

### 3. 获取订单簿

**端点**: `GET /api/v2/orderbook`

**参数**: `pairSymbol` (必需), `limit` (可选, 默认25, 最大1000)

```python
resp = requests.get(f"{BASE_URL}/api/v2/orderbook", params={
    "pairSymbol": "BTCUSDT", "limit": 10
})
data = resp.json()["data"]
for ask in data["asks"][:5]:
    print(f"ASK: price={ask[0]}, amount={ask[1]}")
for bid in data["bids"][:5]:
    print(f"BID: price={bid[0]}, amount={bid[1]}")
```

### 4. 获取最近成交

**端点**: `GET /api/v2/trades`

**参数**: `pairSymbol` (必需), `last` (可选, 返回条数)

```python
resp = requests.get(f"{BASE_URL}/api/v2/trades", params={
    "pairSymbol": "BTCUSDT", "last": 10
})
for t in resp.json()["data"]:
    print(f"Price={t['price']}, Amount={t['amount']}, Side={t['side']}, Time={t['date']}")
```

### 5. 获取K线数据 (OHLC)

**端点**: `GET /api/v2/ohlcs`

**参数**: `pair` (必需), `from`/`to` (Unix 时间戳)

```python
import time as t
now = int(t.time())
resp = requests.get(f"{BASE_URL}/api/v2/ohlcs", params={
    "pair": "BTCUSDT",
    "from": now - 86400,
    "to": now
})
for c in resp.json()["data"][:10]:
    print(f"T={c['time']} O={c['open']} H={c['high']} L={c['low']} "
          f"C={c['close']} V={c['volume']}")
```

## 交易API

### 1. 查询余额

**端点**: `GET /api/v1/users/balances`

```python
balances = btcturk_request("GET", "/api/v1/users/balances")
if balances["success"]:
    for b in balances["data"]:
        if float(b.get("balance", 0)) > 0:
            print(f"{b['asset']}: balance={b['balance']}, available={b['free']}, "
                  f"locked={b['locked']}")
```

### 2. 下单

**端点**: `POST /api/v1/order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| pairSymbol | STRING | 是 | 交易对 |
| orderType | STRING | 是 | buy / sell |
| orderMethod | STRING | 是 | limit / market / stoplimit / stopmarket |
| quantity | DECIMAL | 条件 | 数量 |
| price | DECIMAL | 条件 | 价格（limit 必需） |
| stopPrice | DECIMAL | 条件 | 触发价（stop 必需） |
| newOrderClientId | STRING | 否 | 客户端订单ID |

```python
# 限价买单
order = btcturk_request("POST", "/api/v1/order", body={
    "pairSymbol": "BTCUSDT",
    "orderType": "buy",
    "orderMethod": "limit",
    "quantity": 0.001,
    "price": 40000
})
if order["success"]:
    print(f"Order ID: {order['data']['id']}")

# 市价买单
order = btcturk_request("POST", "/api/v1/order", body={
    "pairSymbol": "BTCUSDT",
    "orderType": "buy",
    "orderMethod": "market",
    "quantity": 0.001
})
```

### 3. 撤单

**端点**: `DELETE /api/v1/order`

**参数**: `id` (必需)

```python
result = btcturk_request("DELETE", "/api/v1/order", params={"id": "12345678"})
```

### 4. 查询挂单

**端点**: `GET /api/v1/openOrders`

```python
orders = btcturk_request("GET", "/api/v1/openOrders", params={"pairSymbol": "BTCUSDT"})
if orders["success"]:
    for o in orders["data"]["asks"]:
        print(f"ASK ID:{o['id']} price={o['price']} qty={o['quantity']}")
    for o in orders["data"]["bids"]:
        print(f"BID ID:{o['id']} price={o['price']} qty={o['quantity']}")
```

### 5. 查询订单详情

**端点**: `GET /api/v1/order/{orderId}`

### 6. 查询成交记录

**端点**: `GET /api/v1/users/transactions/trade`

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| WebSocket 连接 | 15 次/分钟 | 连接频率 |
| REST API | 按端点配置 | 详见官方文档 |

## WebSocket支持

### 连接信息

**URL**: `wss://ws-feed-pro.btcturk.com`

### 频道

| 频道 | 描述 |
|------|------|
| `orderbook` | 订单簿 |
| `trade` | 实时成交 |
| `ticker` | Ticker |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    channel = data.get("channel", "")
    if "trade" in channel:
        print(f"Trade: {data}")
    elif "orderbook" in channel:
        print(f"Orderbook update: {data}")
    elif "ticker" in channel:
        print(f"Ticker: {data}")

def on_open(ws):
    # 加入频道
    ws.send(json.dumps([
        151, {"type": 151, "channel": "trade", "event": "BTCUSDT", "join": True}
    ]))
    ws.send(json.dumps([
        151, {"type": 151, "channel": "orderbook", "event": "BTCUSDT", "join": True}
    ]))

ws = websocket.WebSocketApp(
    "wss://ws-feed-pro.btcturk.com",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)
```

## 错误代码

| HTTP状态码 | 描述 |
|-----------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 认证失败 |
| 429 | 速率限制 |
| 500 | 服务器错误 |

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC SHA256 + Base64 签名认证 Python 示例
- 添加市场数据、交易、WebSocket 订阅示例

---

## 相关资源

- [BTCTurk API 文档](https://docs.btcturk.com/)
- [BTCTurk 官网](https://www.btcturk.com)
- [CCXT BTCTurk 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 BTCTurk 官方 API 文档整理。*
