# Bitso API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.bitso.com/bitso-api/

## 交易所基本信息
- 官方名称: Bitso
- 官网: https://bitso.com
- 交易所类型: CEX (中心化交易所)
- 总部: 墨西哥
- 支持的交易对: 100+ (MXN, USD, BTC 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.10%, Taker 0.65% (基础费率，阶梯递减)
- 法币支持: MXN (墨西哥比索), USD, ARS, BRL, COP
- 合规: 拉美最大合规交易所
- 特点: 拉丁美洲领先的加密货币交易所

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://bitso.com/api/v3` | 生产环境 |
| REST API (Stage) | `https://stage.bitso.com/api/v3` | 测试环境 |
| REST API (Sandbox) | `https://api-sandbox.bitso.com/api/v3` | 沙盒 |
| WebSocket | `wss://ws.bitso.com` | 实时数据 |

## 认证方式

### API密钥获取

1. 登录 Bitso 账户
2. 进入 Profile -> API
3. 创建 API Key 并设置权限（交易/余额/账户信息）
4. 保存 API Key 和 Secret（仅显示一次）

### HMAC SHA256 签名

**Authorization 头格式**: `Bitso {key}:{nonce}:{signature}`

**签名步骤**:
1. 生成 nonce（递增整数，推荐毫秒时间戳）
2. 拼接签名字符串: `nonce + method + requestPath + body`
3. 使用 Secret 进行 HMAC SHA256 签名
4. 将签名转为十六进制

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://bitso.com/api/v3"

def bitso_request(method, path, params=None, body=None):
    """发送 Bitso 签名请求"""
    nonce = str(int(time.time() * 1000))

    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"/api/v3{path}?{query}"
    else:
        request_path = f"/api/v3{path}"

    body_str = json.dumps(body) if body else ""
    sign_str = nonce + method.upper() + request_path + body_str

    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Authorization": f"Bitso {API_KEY}:{nonce}:{signature}",
        "Content-Type": "application/json",
    }

    url = f"https://bitso.com{request_path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取交易对列表

**端点**: `GET /available_books`

```python
resp = requests.get(f"{BASE_URL}/available_books")
data = resp.json()
if data["success"]:
    for book in data["payload"][:5]:
        print(f"{book['book']}: min_amount={book['minimum_amount']}, "
              f"min_price={book['minimum_price']}, min_value={book['minimum_value']}")
```

### 2. 获取 Ticker

**端点**: `GET /ticker` (全部) 或 `GET /ticker?book={book}`

```python
# 单个
resp = requests.get(f"{BASE_URL}/ticker", params={"book": "btc_mxn"})
t = resp.json()["payload"]
print(f"BTC/MXN: last={t['last']}, bid={t['bid']}, ask={t['ask']}, "
      f"high={t['high']}, low={t['low']}, volume={t['volume']}")

# 全部
resp = requests.get(f"{BASE_URL}/ticker")
for t in resp.json()["payload"][:5]:
    print(f"{t['book']}: last={t['last']}, vol={t['volume']}")
```

### 3. 获取订单簿

**端点**: `GET /order_book?book={book}`

**参数**: `book` (必需), `aggregate` (可选, true/false)

```python
resp = requests.get(f"{BASE_URL}/order_book", params={"book": "btc_mxn"})
book = resp.json()["payload"]
for ask in book["asks"][:5]:
    print(f"ASK: price={ask['price']}, amount={ask['amount']}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid['price']}, amount={bid['amount']}")
```

### 4. 获取最近成交

**端点**: `GET /trades?book={book}`

**参数**: `book` (必需), `limit` (可选, 默认25, 最大100)

```python
resp = requests.get(f"{BASE_URL}/trades", params={"book": "btc_mxn", "limit": 10})
for t in resp.json()["payload"]:
    print(f"TID={t['tid']} price={t['price']} amount={t['amount']} "
          f"side={t['maker_side']} time={t['created_at']}")
```

## 交易API

### 1. 查询余额

**端点**: `GET /balance`

```python
balances = bitso_request("GET", "/balance")
if balances["success"]:
    for b in balances["payload"]["balances"]:
        avail = float(b["available"])
        locked = float(b["locked"])
        if avail > 0 or locked > 0:
            print(f"{b['currency']}: available={b['available']}, locked={b['locked']}, "
                  f"total={b['total']}")
```

### 2. 下单

**端点**: `POST /orders`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| book | STRING | 是 | 交易对，如 btc_mxn |
| side | STRING | 是 | buy / sell |
| type | STRING | 是 | limit / market |
| major | STRING | 条件 | 基础币数量（limit 或 market sell） |
| minor | STRING | 条件 | 计价币金额（market buy） |
| price | STRING | 条件 | 限价价格 |
| time_in_force | STRING | 否 | goodtillcancelled / fillorkill / immediateorcancel / postonly |

```python
# 限价买单
order = bitso_request("POST", "/orders", body={
    "book": "btc_mxn",
    "side": "buy",
    "type": "limit",
    "major": "0.001",
    "price": "800000"
})
if order["success"]:
    print(f"Order ID: {order['payload']['oid']}")

# 市价买单（按金额）
order = bitso_request("POST", "/orders", body={
    "book": "btc_mxn",
    "side": "buy",
    "type": "market",
    "minor": "1000"  # 1000 MXN
})
```

### 3. 撤单

**端点**: `DELETE /orders/{oid}` 或 `DELETE /orders/all`

```python
# 撤销单个
result = bitso_request("DELETE", "/orders/order_id_here")

# 撤销全部
result = bitso_request("DELETE", "/orders/all")
```

### 4. 查询挂单

**端点**: `GET /open_orders`

```python
orders = bitso_request("GET", "/open_orders", params={"book": "btc_mxn"})
if orders["success"]:
    for o in orders["payload"]:
        print(f"OID:{o['oid']} {o['side']} {o['type']} "
              f"price={o['price']} amount={o['original_amount']}")
```

### 5. 查询订单详情

**端点**: `GET /orders/{oid}`

### 6. 查询成交记录

**端点**: `GET /user_trades`

## 账户管理API

| 端点 | 方法 | 描述 |
|------|------|------|
| /balance | GET | 查询余额 |
| /fees | GET | 查询费率 |
| /fundings | GET | 充值记录 |
| /fundings/{fid} | GET | 充值详情 |
| /withdrawals | GET | 提现记录 |
| /crypto_withdrawal | POST | 加密货币提现 |
| /spei_withdrawal | POST | SPEI 法币提现 |

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 公共接口 | 60 RPM | 按 IP |
| 私有接口 | 300 RPM | 按账户（需 KYC） |

> 超限锁定 1 分钟，持续超限可能被限制 24 小时

## WebSocket支持

### 连接信息

**URL**: `wss://ws.bitso.com`

### 频道

| 频道 | 描述 |
|------|------|
| `trades` | 实时成交 |
| `orders` | 订单簿快照 |
| `diff-orders` | 订单簿增量 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    msg_type = data.get("type", "")
    if msg_type == "trades":
        for t in data.get("payload", []):
            print(f"Trade: book={data['book']}, price={t['r']}, amount={t['a']}, type={t['t']}")
    elif msg_type == "orders":
        payload = data.get("payload", {})
        print(f"Book {data['book']}: asks={len(payload.get('asks', []))}, "
              f"bids={len(payload.get('bids', []))}")
    elif msg_type == "diff-orders":
        payload = data.get("payload", {})
        print(f"Diff {data['book']}: {len(payload.get('asks', []))} ask updates, "
              f"{len(payload.get('bids', []))} bid updates")

def on_open(ws):
    ws.send(json.dumps({"action": "subscribe", "book": "btc_mxn", "type": "trades"}))
    ws.send(json.dumps({"action": "subscribe", "book": "btc_mxn", "type": "orders"}))
    ws.send(json.dumps({"action": "subscribe", "book": "btc_mxn", "type": "diff-orders"}))

ws = websocket.WebSocketApp(
    "wss://ws.bitso.com",
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
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 429 | 速率限制 |
| 500 | 服务器错误 |

## 变更历史

### 2026-02-27
- 完善文档，添加详细 V3 REST API 端点说明
- 添加 HMAC SHA256 签名认证完整 Python 示例
- 添加市场数据、交易、账户管理 API 详细说明
- 添加 WebSocket 频道订阅示例

---

## 相关资源

- [Bitso API V3 文档](https://docs.bitso.com/bitso-api/)
- [Bitso 官网](https://bitso.com)
- [CCXT Bitso 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Bitso 官方 API V3 文档整理。*
