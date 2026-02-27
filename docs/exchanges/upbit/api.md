# Upbit API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://global-docs.upbit.com/reference/rest-api-guide
- Python SDK: https://github.com/upbit-exchange/client

## 交易所基本信息
- 官方名称: Upbit
- 官网: https://upbit.com (韩国) / https://sg.upbit.com (新加坡) / https://th.upbit.com (泰国)
- 交易所类型: CEX (中心化交易所)
- 总部: 韩国（由 Dunamu 运营，Kakao 子公司）
- 支持的交易对: 300+ (KRW, BTC, USDT 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.25%, Taker 0.25% (基础费率)
- 法币支持: KRW (韩元)
- 合规: 韩国金融情报分析院 (KFIU) 注册
- Python SDK: `pip install upbit-client`

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API (韩国) | `https://api.upbit.com` | 主端点 |
| REST API (新加坡) | `https://sg-api.upbit.com` | 新加坡 |
| REST API (泰国) | `https://th-api.upbit.com` | 泰国（Beta） |
| WebSocket | `wss://api.upbit.com/websocket/v1` | 实时数据 |

## 认证方式

### API密钥获取

1. 登录 Upbit 账户并完成身份认证
2. 进入 Open API 管理页面
3. 创建 API Key，获取 Access Key 和 Secret Key
4. 设置 IP 白名单（推荐）
5. 勾选所需权限

### JWT (JSON Web Token) 认证

Upbit 使用 JWT 进行 API 认证。无参数请求和有参数请求的签名方式不同。

**无参数请求**:
1. 构建 JWT Payload: `{"access_key": access_key, "nonce": uuid}`
2. 使用 Secret Key 和 HS256 算法签名
3. 将 JWT token 放入 Authorization 头: `Bearer {token}`

**有参数请求**:
1. 将查询参数拼接为 query string
2. 对 query string 进行 SHA512 哈希
3. 将哈希值加入 JWT Payload: `{"access_key": ..., "nonce": ..., "query_hash": hash, "query_hash_alg": "SHA512"}`
4. 使用 Secret Key 签名

### Python 签名示例

```python
import jwt
import uuid
import hashlib
import requests
from urllib.parse import urlencode, unquote

ACCESS_KEY = "your_access_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.upbit.com/v1"

def upbit_get(path, params=None):
    """发送 Upbit GET 请求"""
    if params:
        query_string = unquote(urlencode(params, doseq=True))
        query_hash = hashlib.sha512(query_string.encode()).hexdigest()
        payload = {
            "access_key": ACCESS_KEY,
            "nonce": str(uuid.uuid4()),
            "query_hash": query_hash,
            "query_hash_alg": "SHA512",
        }
    else:
        payload = {
            "access_key": ACCESS_KEY,
            "nonce": str(uuid.uuid4()),
        }

    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params)
    return resp.json()

def upbit_post(path, params):
    """发送 Upbit POST 请求"""
    query_string = unquote(urlencode(params, doseq=True))
    query_hash = hashlib.sha512(query_string.encode()).hexdigest()

    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "query_hash": query_hash,
        "query_hash_alg": "SHA512",
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, json=params)
    return resp.json()

def upbit_delete(path, params):
    """发送 Upbit DELETE 请求"""
    query_string = unquote(urlencode(params, doseq=True))
    query_hash = hashlib.sha512(query_string.encode()).hexdigest()

    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "query_hash": query_hash,
        "query_hash_alg": "SHA512",
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(f"{BASE_URL}{path}", headers=headers, params=params)
    return resp.json()
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取市场列表

**端点**: `GET /v1/market/all`

**参数**: `isDetails` (可选, true/false)

```python
resp = requests.get(f"{BASE_URL}/market/all", params={"isDetails": "true"})
for m in resp.json()[:5]:
    print(f"{m['market']}: {m['korean_name']} / {m['english_name']}")
```

**响应示例**:
```json
[
  {
    "market": "KRW-BTC",
    "korean_name": "비트코인",
    "english_name": "Bitcoin",
    "market_warning": "NONE"
  }
]
```

> **交易对格式**: `{quote}-{base}`，如 `KRW-BTC` 表示以 KRW 购买 BTC

### 2. 获取 Ticker

**端点**: `GET /v1/ticker`

**参数**: `markets` (必需, 逗号分隔, 如 `KRW-BTC,KRW-ETH`)

```python
resp = requests.get(f"{BASE_URL}/ticker", params={"markets": "KRW-BTC,KRW-ETH"})
for t in resp.json():
    print(f"{t['market']}: trade_price={t['trade_price']}, "
          f"change={t['change']}, change_rate={t['signed_change_rate']:.4f}, "
          f"acc_trade_volume_24h={t['acc_trade_volume_24h']}")
```

### 3. 获取订单簿

**端点**: `GET /v1/orderbook`

**参数**: `markets` (必需, 逗号分隔)

```python
resp = requests.get(f"{BASE_URL}/orderbook", params={"markets": "KRW-BTC"})
for book in resp.json():
    print(f"{book['market']}: total_ask={book['total_ask_size']}, total_bid={book['total_bid_size']}")
    for unit in book["orderbook_units"][:5]:
        print(f"  ASK: {unit['ask_price']} x {unit['ask_size']}")
        print(f"  BID: {unit['bid_price']} x {unit['bid_size']}")
```

### 4. 获取最近成交

**端点**: `GET /v1/trades/ticks`

**参数**: `market` (必需), `count` (可选, 默认1, 最大500), `to` (可选, HH:mm:ss)

```python
resp = requests.get(f"{BASE_URL}/trades/ticks", params={
    "market": "KRW-BTC",
    "count": 10
})
for t in resp.json():
    print(f"price={t['trade_price']}, vol={t['trade_volume']}, "
          f"side={t['ask_bid']}, time={t['trade_date_utc']} {t['trade_time_utc']}")
```

### 5. 获取K线 (日)

**端点**: `GET /v1/candles/days`

**参数**: `market` (必需), `count` (可选, 最大200)

```python
resp = requests.get(f"{BASE_URL}/candles/days", params={
    "market": "KRW-BTC",
    "count": 10
})
for c in resp.json():
    print(f"Date={c['candle_date_time_kst']} O={c['opening_price']} H={c['high_price']} "
          f"L={c['low_price']} C={c['trade_price']} V={c['candle_acc_trade_volume']}")
```

### 6. 获取K线 (分钟)

**端点**: `GET /v1/candles/minutes/{unit}`

**参数**: `unit` (路径参数: 1/3/5/10/15/30/60/240), `market`, `count`

```python
resp = requests.get(f"{BASE_URL}/candles/minutes/60", params={
    "market": "KRW-BTC",
    "count": 24
})
```

### 7. 获取K线 (周/月)

**端点**: `GET /v1/candles/weeks` / `GET /v1/candles/months`

## 交易API

> 以下端点需要 JWT 认证。

### 1. 查询账户信息

**端点**: `GET /v1/accounts`

```python
accounts = upbit_get("/accounts")
for a in accounts:
    if float(a["balance"]) > 0:
        print(f"{a['currency']}: balance={a['balance']}, locked={a['locked']}, "
              f"avg_buy_price={a['avg_buy_price']}")
```

### 2. 下单

**端点**: `POST /v1/orders`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| market | STRING | 是 | 交易对，如 KRW-BTC |
| side | STRING | 是 | bid (买) / ask (卖) |
| ord_type | STRING | 是 | limit / price (市价买) / market (市价卖) / best |
| volume | STRING | 条件 | 数量 (limit/market 必需) |
| price | STRING | 条件 | 价格 (limit/price 必需) |
| identifier | STRING | 否 | 客户端订单ID |

```python
# 限价买单
order = upbit_post("/orders", {
    "market": "KRW-BTC",
    "side": "bid",
    "ord_type": "limit",
    "volume": "0.001",
    "price": "50000000"
})
print(f"Order UUID: {order.get('uuid')}")

# 市价买单（指定总金额）
order = upbit_post("/orders", {
    "market": "KRW-BTC",
    "side": "bid",
    "ord_type": "price",
    "price": "100000"  # 10万 KRW
})

# 市价卖单（指定数量）
order = upbit_post("/orders", {
    "market": "KRW-BTC",
    "side": "ask",
    "ord_type": "market",
    "volume": "0.001"
})
```

### 3. 撤单

**端点**: `DELETE /v1/order`

**参数**: `uuid` 或 `identifier`

```python
result = upbit_delete("/order", {"uuid": "cdd92199-2897-4e14-9571-f45a578b42dc"})
print(f"Cancelled: {result}")
```

### 4. 查询单个订单

**端点**: `GET /v1/order`

```python
order = upbit_get("/order", params={"uuid": "cdd92199-2897-4e14-9571-f45a578b42dc"})
print(f"State: {order['state']}, Executed: {order['executed_volume']}/{order['volume']}")
```

**订单状态**:
- `wait` - 等待成交
- `watch` - 预约订单等待中
- `done` - 全部成交
- `cancel` - 已撤销

### 5. 查询订单列表

**端点**: `GET /v1/orders`

**参数**: `market` (可选), `state` (可选: wait/watch/done/cancel), `page`, `limit`, `order_by`

```python
orders = upbit_get("/orders", params={
    "market": "KRW-BTC",
    "state": "wait",
    "limit": 100
})
for o in orders:
    print(f"UUID:{o['uuid']} {o['side']} {o['ord_type']} "
          f"price={o['price']} vol={o['volume']} state={o['state']}")
```

## 账户管理API

### 1. API Key 信息

**端点**: `GET /v1/api_keys`

### 2. 查询充提状态

**端点**: `GET /v1/status/wallet`

```python
status = requests.get(f"{BASE_URL}/status/wallet").json()
for s in status:
    print(f"{s['currency']}: wallet={s['wallet_state']}, block={s['block_state']}")
```

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| Exchange API (全局) | 900 次/秒 | 按 IP |
| Exchange API (个人) | 30 次/秒 | 按 API Key |
| 下单 | 8 次/秒 | 按 API Key |
| 查询订单 | 30 次/秒 | 按 API Key |
| Quotation API | 1,800 次/分钟 | 按 IP |

### 最佳实践

- 使用 WebSocket 获取实时行情
- 下单频率控制在 8 次/秒以内
- 使用 `identifier` 实现订单幂等性
- JWT token 有效期有限，每次请求生成新 token

## WebSocket支持

### 连接信息

**WebSocket URL**: `wss://api.upbit.com/websocket/v1`

### 支持频道

| 类型 | 描述 |
|------|------|
| `ticker` | Ticker |
| `trade` | 实时成交 |
| `orderbook` | 订单簿 |

### Python WebSocket 示例

```python
import websocket
import json
import uuid

def on_message(ws, message):
    # 响应为 bytes
    data = json.loads(message)
    msg_type = data.get("type", "")

    if msg_type == "ticker":
        print(f"Ticker {data['code']}: price={data['trade_price']}, "
              f"change={data['change']}, vol={data['acc_trade_volume_24h']}")
    elif msg_type == "trade":
        print(f"Trade {data['code']}: price={data['trade_price']}, "
              f"vol={data['trade_volume']}, side={data['ask_bid']}")
    elif msg_type == "orderbook":
        units = data.get("orderbook_units", [])
        print(f"Orderbook {data['code']}: units={len(units)}")

def on_open(ws):
    # 订阅格式: [{"ticket": uuid}, {"type": channel, "codes": [markets]}]
    subscribe_msg = [
        {"ticket": str(uuid.uuid4())},
        {"type": "ticker", "codes": ["KRW-BTC", "KRW-ETH"]},
        {"type": "trade", "codes": ["KRW-BTC"]},
        {"type": "orderbook", "codes": ["KRW-BTC"]}
    ]
    ws.send(json.dumps(subscribe_msg))

ws = websocket.WebSocketApp(
    "wss://api.upbit.com/websocket/v1",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)
```

## 错误代码

### 常见错误

| 错误名 | HTTP状态码 | 描述 |
|--------|-----------|------|
| invalid_query_payload | 400 | JWT payload 无效 |
| jwt_verification | 401 | JWT 验证失败 |
| expired_access_key | 401 | API Key 已过期 |
| nonce_used | 401 | Nonce 重复使用 |
| no_authorization_i_p | 401 | IP 未授权 |
| insufficient_funds_bid | 400 | 买入余额不足 |
| insufficient_funds_ask | 400 | 卖出余额不足 |
| under_min_total_bid | 400 | 低于最小下单金额 |
| under_min_total_ask | 400 | 低于最小卖出金额 |
| too_many_requests | 429 | 请求过于频繁 |

### Python 错误处理

```python
def safe_upbit_request(func, *args, **kwargs):
    """带错误处理的请求"""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            print(f"Upbit Error [{err.get('name')}]: {err.get('message')}")
            return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 V1 REST API 端点说明
- 添加 JWT (HS256) + SHA512 query hash 认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket 订阅示例
- 添加韩国/新加坡/泰国多区域 API 端点
- 添加错误代码表和错误处理

---

## 相关资源

- [Upbit API 文档](https://global-docs.upbit.com/reference/rest-api-guide)
- [Upbit Python SDK](https://github.com/upbit-exchange/client)
- [Upbit 官网](https://upbit.com)
- [CCXT Upbit 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Upbit 官方 API 文档整理。*
