# Crypto.com Exchange API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html

## 交易所基本信息
- 官方名称: Crypto.com Exchange
- 官网: https://crypto.com/exchange
- 交易所类型: CEX (中心化交易所)
- 总部: 新加坡
- 支持的交易对: 500+ (USDT, USD, BTC, CRO 计价)
- 支持的交易类型: 现货(Spot)、保证金(Margin)、衍生品(Derivatives)
- 手续费: Maker 0.075%, Taker 0.075% (基础费率，CRO 质押可降低)
- 法币支持: USD, EUR, GBP 等
- 合规: 多国持牌，美国 FinCEN MSB 注册
- 特点: CRO 代币生态，Visa 卡集成

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.crypto.com/exchange/v1` | 主端点 |
| WebSocket (User) | `wss://stream.crypto.com/exchange/v1/user` | 私有频道 |
| WebSocket (Market) | `wss://stream.crypto.com/exchange/v1/market` | 公共行情 |

## 认证方式

### API密钥获取

1. 登录 Crypto.com Exchange 账户
2. 进入 Settings -> API Keys
3. 创建 API Key，获取 API Key 和 Secret Key
4. 设置权限和 IP 白名单
5. 启用 2FA 以使用交易权限

### HMAC-SHA256 签名

**签名步骤**:
1. 将请求参数键按字母升序排列
2. 将所有参数键值对拼接为字符串（无分隔符）: `key1value1key2value2...`
3. 构建签名字符串: `method + id + api_key + param_string + nonce`
4. 使用 Secret Key 进行 HMAC-SHA256 签名
5. 将签名转为十六进制

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.crypto.com/exchange/v1"

def build_param_string(params):
    """将参数按键排序并拼接"""
    if not params:
        return ""
    sorted_keys = sorted(params.keys())
    parts = []
    for key in sorted_keys:
        value = params[key]
        if isinstance(value, list):
            for item in value:
                parts.append(f"{key}{item}")
        else:
            parts.append(f"{key}{value}")
    return "".join(parts)

def crypto_request(method, req_id=None, params=None):
    """发送 Crypto.com Exchange 签名请求"""
    nonce = int(time.time() * 1000)
    if req_id is None:
        req_id = nonce

    param_string = build_param_string(params)
    sign_str = f"{method}{req_id}{API_KEY}{param_string}{nonce}"

    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    body = {
        "id": req_id,
        "method": method,
        "api_key": API_KEY,
        "params": params or {},
        "sig": signature,
        "nonce": nonce,
    }

    resp = requests.post(BASE_URL + "/" + method, json=body)
    return resp.json()

# 测试
result = crypto_request("private/get-account-summary")
print(result)
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取交易对信息

**端点**: `GET /public/get-instruments`

```python
resp = requests.get(f"{BASE_URL}/public/get-instruments")
data = resp.json()
if data["code"] == 0:
    for inst in data["result"]["data"][:5]:
        print(f"{inst['symbol']}: base={inst['base_ccy']}, quote={inst['quote_ccy']}, "
              f"price_decimals={inst['price_decimals']}, qty_decimals={inst['quantity_decimals']}")
```

### 2. 获取 Ticker

**端点**: `GET /public/get-tickers` 或 `GET /public/get-tickers?instrument_name={symbol}`

```python
# 单个
resp = requests.get(f"{BASE_URL}/public/get-tickers", params={"instrument_name": "BTC_USDT"})
data = resp.json()
if data["code"] == 0:
    for t in data["result"]["data"]:
        print(f"{t['i']}: last={t['a']}, bid={t['b']}, ask={t['k']}, "
              f"high={t['h']}, low={t['l']}, vol={t['v']}")

# 全部
resp = requests.get(f"{BASE_URL}/public/get-tickers")
print(f"Total: {len(resp.json()['result']['data'])}")
```

**Ticker 字段说明**:

| 字段 | 描述 |
|------|------|
| i | 交易对名称 |
| a | 最新价 |
| b | 最优买价 |
| k | 最优卖价 |
| h | 24h最高价 |
| l | 24h最低价 |
| v | 24h成交量 |
| vv | 24h成交额 |
| t | 时间戳 |

### 3. 获取订单簿

**端点**: `GET /public/get-book`

**参数**: `instrument_name` (必需), `depth` (可选, 默认50, 最大50)

```python
resp = requests.get(f"{BASE_URL}/public/get-book", params={
    "instrument_name": "BTC_USDT",
    "depth": 10
})
data = resp.json()
if data["code"] == 0:
    book = data["result"]["data"][0]
    for ask in book["asks"][:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}, count={ask[2]}")
    for bid in book["bids"][:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}, count={bid[2]}")
```

### 4. 获取最近成交

**端点**: `GET /public/get-trades`

**参数**: `instrument_name` (必需), `count` (可选, 默认100)

```python
resp = requests.get(f"{BASE_URL}/public/get-trades", params={
    "instrument_name": "BTC_USDT",
    "count": 10
})
data = resp.json()
if data["code"] == 0:
    for t in data["result"]["data"]:
        side = t["s"]  # BUY/SELL
        print(f"Price={t['p']}, Qty={t['q']}, Side={side}, Time={t['t']}")
```

### 5. 获取K线数据

**端点**: `GET /public/get-candlestick`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instrument_name | STRING | 是 | 交易对 |
| period | STRING | 是 | 1m/5m/15m/30m/1h/4h/6h/12h/1D/7D/14D/1M |
| count | INT | 否 | 数量 (默认300, 最大300) |

```python
resp = requests.get(f"{BASE_URL}/public/get-candlestick", params={
    "instrument_name": "BTC_USDT",
    "period": "1h",
    "count": 24
})
data = resp.json()
if data["code"] == 0:
    for c in data["result"]["data"]:
        print(f"T={c['t']} O={c['o']} H={c['h']} L={c['l']} C={c['c']} V={c['v']}")
```

## 交易API

> 以下端点均需签名认证。

### 1. 查询账户摘要

**方法**: `private/get-account-summary`

```python
result = crypto_request("private/get-account-summary")
if result["code"] == 0:
    for acc in result["result"]["data"]:
        balance = float(acc.get("total_balance", 0))
        if balance > 0:
            print(f"{acc['instrument_name']}: balance={acc['total_balance']}, "
                  f"available={acc['total_available']}")
```

### 2. 下单

**方法**: `private/create-order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instrument_name | STRING | 是 | 交易对，如 BTC_USDT |
| side | STRING | 是 | BUY / SELL |
| type | STRING | 是 | LIMIT / MARKET / STOP_LOSS / STOP_LIMIT / TAKE_PROFIT / TAKE_PROFIT_LIMIT |
| quantity | DECIMAL | 条件 | 数量 |
| price | DECIMAL | 条件 | 价格（LIMIT 必需） |
| notional | DECIMAL | 条件 | 金额（市价买可用） |
| client_oid | STRING | 否 | 客户端订单ID |
| time_in_force | STRING | 否 | GOOD_TILL_CANCEL / FILL_OR_KILL / IMMEDIATE_OR_CANCEL |
| exec_inst | STRING | 否 | POST_ONLY |

```python
# 限价买单
order = crypto_request("private/create-order", params={
    "instrument_name": "BTC_USDT",
    "side": "BUY",
    "type": "LIMIT",
    "quantity": "0.001",
    "price": "40000",
    "time_in_force": "GOOD_TILL_CANCEL"
})
if order["code"] == 0:
    print(f"Order ID: {order['result']['order_id']}")

# 市价买单（按金额）
order = crypto_request("private/create-order", params={
    "instrument_name": "BTC_USDT",
    "side": "BUY",
    "type": "MARKET",
    "notional": "100"  # 100 USDT
})

# 市价卖单（按数量）
order = crypto_request("private/create-order", params={
    "instrument_name": "BTC_USDT",
    "side": "SELL",
    "type": "MARKET",
    "quantity": "0.001"
})
```

### 3. 撤单

**方法**: `private/cancel-order`

```python
result = crypto_request("private/cancel-order", params={
    "instrument_name": "BTC_USDT",
    "order_id": "12345678"
})
```

### 4. 全部撤单

**方法**: `private/cancel-all-orders`

```python
result = crypto_request("private/cancel-all-orders", params={
    "instrument_name": "BTC_USDT"
})
```

### 5. 查询挂单

**方法**: `private/get-open-orders`

```python
orders = crypto_request("private/get-open-orders", params={
    "instrument_name": "BTC_USDT"
})
if orders["code"] == 0:
    for o in orders["result"]["data"]:
        print(f"ID:{o['order_id']} {o['side']} {o['type']} "
              f"price={o['price']} qty={o['quantity']} status={o['status']}")
```

### 6. 查询订单详情

**方法**: `private/get-order-detail`

### 7. 查询历史订单

**方法**: `private/get-order-history`

### 8. 查询成交记录

**方法**: `private/get-trades`

## 账户管理API

| 方法 | 描述 |
|------|------|
| private/get-account-summary | 账户摘要 |
| private/get-deposit-address | 充值地址 |
| private/get-deposit-history | 充值记录 |
| private/create-withdrawal | 发起提现 |
| private/get-withdrawal-history | 提现记录 |
| private/get-currency-networks | 币种网络信息 |

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 公共端点 | 100 次/秒 | 按 IP |
| create-order | 15 次/100ms | 按用户 |
| cancel-order | 15 次/100ms | 按用户 |
| get-order-detail | 30 次/100ms | 按用户 |
| get-trades / get-order-history | 5 次/秒 | 按用户 |

### 最佳实践

- 使用 WebSocket 获取实时行情，减少 REST 轮询
- 使用 `client_oid` 实现订单幂等性
- 利用 CRO 质押降低手续费

## WebSocket支持

### 连接信息

| URL | 用途 |
|-----|------|
| `wss://stream.crypto.com/exchange/v1/market` | 公共行情 |
| `wss://stream.crypto.com/exchange/v1/user` | 私有频道（需认证） |

### 公共频道

| 频道 | 描述 |
|------|------|
| `ticker.{instrument}` | Ticker |
| `trade.{instrument}` | 实时成交 |
| `book.{instrument}.{depth}` | 订单簿 |
| `candlestick.{period}.{instrument}` | K线 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    method = data.get("method", "")

    if method == "subscribe":
        result = data.get("result", {})
        channel = result.get("channel", "")
        sub_data = result.get("data", [])

        if "ticker" in channel:
            for t in sub_data:
                print(f"Ticker {t['i']}: last={t['a']}, vol={t['v']}")
        elif "trade" in channel:
            for t in sub_data:
                print(f"Trade: price={t['p']}, qty={t['q']}, side={t['s']}")
        elif "book" in channel:
            for b in sub_data:
                print(f"Book: asks={len(b.get('asks', []))}, bids={len(b.get('bids', []))}")

def on_open(ws):
    # 订阅 Ticker
    ws.send(json.dumps({
        "id": 1,
        "method": "subscribe",
        "params": {"channels": ["ticker.BTC_USDT"]}
    }))
    # 订阅成交
    ws.send(json.dumps({
        "id": 2,
        "method": "subscribe",
        "params": {"channels": ["trade.BTC_USDT"]}
    }))
    # 订阅订单簿
    ws.send(json.dumps({
        "id": 3,
        "method": "subscribe",
        "params": {"channels": ["book.BTC_USDT.10"]}
    }))

ws = websocket.WebSocketApp(
    "wss://stream.crypto.com/exchange/v1/market",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)
```

### WebSocket 认证 (私有频道)

```python
def ws_auth(ws):
    """WebSocket 认证"""
    nonce = int(time.time() * 1000)
    method = "public/auth"
    req_id = nonce
    sign_str = f"{method}{req_id}{API_KEY}{nonce}"
    sig = hmac.new(SECRET_KEY.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

    ws.send(json.dumps({
        "id": req_id,
        "method": method,
        "api_key": API_KEY,
        "sig": sig,
        "nonce": nonce,
    }))
```

## 错误代码

### 响应格式

```json
{
  "id": 1,
  "method": "...",
  "code": 0,
  "result": { ... }
}
```

### 常见错误码

| 错误码 | 描述 |
|--------|------|
| 0 | 成功 |
| 10001 | 系统错误 |
| 10002 | 参数无效 |
| 10003 | IP 未授权 |
| 10004 | API Key 无效 |
| 10005 | 签名无效 |
| 10006 | 时间戳过期 |
| 10007 | 请求过于频繁 |
| 10008 | 权限不足 |
| 20001 | 余额不足 |
| 20007 | 订单不存在 |
| 30003 | 交易对不存在 |
| 30004 | 价格精度错误 |
| 30005 | 数量精度错误 |
| 30006 | 最小下单金额不满足 |

### Python 错误处理

```python
def safe_crypto_request(method, params=None):
    """带错误处理的请求"""
    try:
        result = crypto_request(method, params=params)
        if result.get("code") == 0:
            return result.get("result")
        code = result.get("code")
        msg = result.get("message", "Unknown")
        print(f"Crypto.com Error [{code}]: {msg}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 V1 REST API 端点说明
- 添加 HMAC-SHA256 签名认证完整 Python 示例
- 添加市场数据 API（Ticker、订单簿、成交、K线）详细说明
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 WebSocket (公共/认证) 订阅示例
- 添加错误代码表和错误处理

---

## 相关资源

- [Crypto.com Exchange API V1 文档](https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html)
- [Crypto.com 官网](https://crypto.com/exchange)
- [CCXT Crypto.com 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 Crypto.com Exchange 官方 API V1 文档整理。*
