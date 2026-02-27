# BigONE API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v3 (现货/钱包)，v2 (合约)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://open.big.one/docs/api.html

## 交易所基本信息
- 官方名称: BigONE
- 官网: https://big.one
- 交易所类型: CEX (中心化交易所)
- 总部: 中国
- 24h交易量排名: #25 ($440M+)
- 支持的交易对: 200+
- 支持的交易类型: 现货(Spot)、合约(Contract/Swap)
- 手续费: Maker 0.1%, Taker 0.1%
- 费率文档: https://bigone.zendesk.com/hc/en-us/articles/115001933374

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST 公共 (现货) | `https://big.one/api/v3` | 市场数据（无需认证） |
| REST 私有 (现货) | `https://big.one/api/v3/viewer` | 账户和交易（需认证） |
| REST 公共 (合约) | `https://big.one/api/contract/v2` | 合约市场数据 |
| REST 私有 (合约) | `https://big.one/api/contract/v2` | 合约交易（需认证） |
| WebSocket | `wss://big.one/ws/v2` | 实时数据流 |

> **备用域名**: `bigone.com` 可替换 `big.one`

## 认证方式

### API密钥获取

1. 注册 BigONE 账户
2. 进入 用户中心 > API管理
3. 创建 API Key，获取 API Key 和 Secret Key
4. 设置权限和 IP 白名单

### JWT (JSON Web Token) 认证

BigONE 使用 JWT (HS256) 进行 API 认证。每个请求需要在 Header 中携带 JWT Token。

**JWT Payload 结构**:

| 字段 | 类型 | 描述 |
|------|------|------|
| type | STRING | 固定值 `"OpenAPIV2"` |
| sub | STRING | API Key |
| nonce | STRING | 随机数（建议使用纳秒级时间戳） |
| recv_window | STRING | 请求有效窗口（毫秒），可选 |

**Python 签名示例**:

```python
import jwt
import time
import uuid
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://big.one/api/v3"

def generate_jwt_token():
    """生成 JWT Token"""
    payload = {
        "type": "OpenAPIV2",
        "sub": API_KEY,
        "nonce": str(int(time.time() * 1e9)),  # 纳秒级时间戳
    }
    token = jwt.encode(payload, API_SECRET, algorithm="HS256")
    return token

def get_headers():
    """获取认证请求头"""
    token = generate_jwt_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

# 测试：获取账户余额
response = requests.get(f"{BASE_URL}/viewer/accounts", headers=get_headers())
print(response.json())
```

> **依赖**: `pip install PyJWT requests`

## 市场数据API

### 1. 服务器连通性检测

**端点**: `GET /api/v3/ping`

**描述**: 检查 API 服务器是否正常运行。

```python
import requests

resp = requests.get("https://big.one/api/v3/ping")
print(resp.json())
# {"data": {"timestamp": "2024-01-01T00:00:00Z"}}
```

### 2. 获取所有交易对

**端点**: `GET /api/v3/asset_pairs`

**描述**: 获取所有可用交易对的基本信息。

**响应示例**:
```json
{
  "data": [
    {
      "id": "d2185614-50c3-4588-b146-b8afe7534da6",
      "name": "BTC-USDT",
      "base_asset": {"symbol": "BTC"},
      "quote_asset": {"symbol": "USDT"},
      "base_scale": 8,
      "quote_scale": 2,
      "min_quote_value": "1.0",
      "maker_fee_rate": "0.001",
      "taker_fee_rate": "0.001"
    }
  ]
}
```

### 3. 获取行情数据

**端点**: `GET /api/v3/asset_pairs/{asset_pair_name}/ticker`

**描述**: 获取指定交易对的 Ticker 数据。

```python
resp = requests.get("https://big.one/api/v3/asset_pairs/BTC-USDT/ticker")
data = resp.json()["data"]
print(f"Last: {data['close']}, Vol: {data['volume']}")
```

**响应示例**:
```json
{
  "data": {
    "asset_pair_name": "BTC-USDT",
    "bid": {"price": "96500.00", "quantity": "0.5"},
    "ask": {"price": "96501.00", "quantity": "0.3"},
    "open": "95000.00",
    "high": "97000.00",
    "low": "94500.00",
    "close": "96500.00",
    "volume": "1234.5678",
    "daily_change": "1500.00"
  }
}
```

**批量获取**: `GET /api/v3/asset_pairs/tickers`

### 4. 获取深度数据

**端点**: `GET /api/v3/asset_pairs/{asset_pair_name}/depth`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| limit | INT | 否 | 深度档位，默认50 |

```python
resp = requests.get("https://big.one/api/v3/asset_pairs/BTC-USDT/depth", params={"limit": 10})
data = resp.json()["data"]
print(f"Best bid: {data['bids'][0]}")
print(f"Best ask: {data['asks'][0]}")
```

**响应示例**:
```json
{
  "data": {
    "asset_pair_name": "BTC-USDT",
    "bids": [
      {"price": "96500.00", "quantity": "0.5", "order_count": 3},
      {"price": "96499.00", "quantity": "1.2", "order_count": 5}
    ],
    "asks": [
      {"price": "96501.00", "quantity": "0.3", "order_count": 2},
      {"price": "96502.00", "quantity": "0.8", "order_count": 4}
    ]
  }
}
```

### 5. 获取K线数据

**端点**: `GET /api/v3/asset_pairs/{asset_pair_name}/candles`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| period | ENUM | 否 | K线周期: min1, min5, min15, min30, hour1, hour3, hour4, hour6, hour12, day1, week1, month1 |
| time | DateTime | 否 | 起始时间 (ISO 8601) |
| limit | INT | 否 | 返回数量，默认100，最大500 |

```python
resp = requests.get("https://big.one/api/v3/asset_pairs/BTC-USDT/candles", params={
    "period": "hour1",
    "limit": 5
})
for candle in resp.json()["data"]:
    print(f"{candle['time']}: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")
```

### 6. 获取最近成交

**端点**: `GET /api/v3/asset_pairs/{asset_pair_name}/trades`

**参数**: `limit` (可选，默认50)

## 交易API

> 以下端点均需 JWT 认证。

### 1. 查询账户余额

**端点**: `GET /api/v3/viewer/accounts`

**描述**: 获取所有现货账户余额。

```python
resp = requests.get(f"{BASE_URL}/viewer/accounts", headers=get_headers())
for account in resp.json()["data"]:
    if float(account["balance"]) > 0:
        print(f"{account['asset_symbol']}: balance={account['balance']}, locked={account['locked_balance']}")
```

**响应示例**:
```json
{
  "data": [
    {
      "asset_symbol": "BTC",
      "balance": "0.12345678",
      "locked_balance": "0.01000000"
    },
    {
      "asset_symbol": "USDT",
      "balance": "10000.00",
      "locked_balance": "500.00"
    }
  ]
}
```

### 2. 创建订单

**端点**: `POST /api/v3/viewer/orders`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| asset_pair_name | STRING | 是 | 交易对，如 BTC-USDT |
| side | ENUM | 是 | BID (买) / ASK (卖) |
| type | ENUM | 否 | LIMIT (限价，默认) / MARKET (市价) / STOP_LIMIT / STOP_MARKET |
| price | DECIMAL | 条件 | 限价单必需 |
| amount | DECIMAL | 是 | 订单数量 |
| stop_price | DECIMAL | 条件 | 止损单必需 |
| operator | ENUM | 条件 | 止损方向: GTE (>=) / LTE (<=) |
| client_order_id | STRING | 否 | 客户自定义订单ID |

**Python示例**:
```python
import json

# 限价买单
order_data = {
    "asset_pair_name": "BTC-USDT",
    "side": "BID",
    "type": "LIMIT",
    "price": "95000.00",
    "amount": "0.001"
}
resp = requests.post(
    f"{BASE_URL}/viewer/orders",
    headers=get_headers(),
    data=json.dumps(order_data)
)
print(resp.json())
```

**响应示例**:
```json
{
  "data": {
    "id": "12345678",
    "asset_pair_name": "BTC-USDT",
    "side": "BID",
    "type": "LIMIT",
    "price": "95000.00",
    "amount": "0.001",
    "filled_amount": "0",
    "avg_deal_price": "0",
    "state": "PENDING",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
}
```

### 3. 撤销订单

**端点**: `POST /api/v3/viewer/orders/{id}/cancel`

```python
order_id = "12345678"
resp = requests.post(f"{BASE_URL}/viewer/orders/{order_id}/cancel", headers=get_headers())
print(resp.json())
```

### 4. 撤销所有订单

**端点**: `POST /api/v3/viewer/orders/cancel`

**参数**: `asset_pair_name` (可选，指定交易对)

### 5. 查询订单详情

**端点**: `GET /api/v3/viewer/orders/{id}`

### 6. 查询活跃订单

**端点**: `GET /api/v3/viewer/orders`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| asset_pair_name | STRING | 否 | 交易对过滤 |
| state | ENUM | 否 | 订单状态: PENDING / FILLED / CANCELLED |
| limit | INT | 否 | 返回数量 |
| page_token | STRING | 否 | 分页令牌 |

### 7. 查询成交历史

**端点**: `GET /api/v3/viewer/trades`

**参数**: `asset_pair_name`, `limit`, `page_token`

## 账户管理API

### 1. 钱包余额

**端点**: `GET /api/v3/viewer/fund/accounts`

**描述**: 获取资金账户余额（用于充提）。

### 2. 获取充币地址

**端点**: `GET /api/v3/viewer/assets/{asset_symbol}/address`

```python
resp = requests.get(f"{BASE_URL}/viewer/assets/BTC/address", headers=get_headers())
print(resp.json())
```

### 3. 提币

**端点**: `POST /api/v3/viewer/withdrawals`

**参数**: `symbol`, `target_address`, `amount`, `memo` (可选)

### 4. 充提记录

- 充值记录: `GET /api/v3/viewer/deposits`
- 提现记录: `GET /api/v3/viewer/withdrawals`

### 5. 内部转账

**端点**: `POST /api/v3/viewer/transfer`

**描述**: 在现货账户(SPOT)、资金账户(FUND)、合约账户(CONTRACT)之间转账。

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 全局 | 500 次/10秒 | 所有端点合计 |
| 单请求间隔 | 20ms | 最小请求间隔 |
| 下单 | 较高限额 | 交易端点有独立额度 |

### 最佳实践

```python
import time
import requests

class RateLimiter:
    """简单速率限制器"""
    def __init__(self, max_requests=50, per_seconds=1):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.requests = []

    def wait(self):
        now = time.time()
        self.requests = [r for r in self.requests if now - r < self.per_seconds]
        if len(self.requests) >= self.max_requests:
            sleep_time = self.per_seconds - (now - self.requests[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        self.requests.append(time.time())

limiter = RateLimiter(max_requests=45, per_seconds=1)

def safe_request(url, **kwargs):
    limiter.wait()
    return requests.get(url, **kwargs)
```

## WebSocket支持

### 连接地址

```text
wss://big.one/ws/v2
```

### 公共频道（无需认证）

- **Ticker 行情**: 订阅交易对行情
- **深度数据**: 订阅订单簿变动
- **成交数据**: 订阅实时成交

### 私有频道（需 JWT 认证）

- **订单更新**: 订单状态变动推送
- **账户余额**: 余额变动推送

### WebSocket 认证

```python
import websocket
import json
import jwt
import time

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"

def generate_jwt():
    payload = {
        "type": "OpenAPIV2",
        "sub": API_KEY,
        "nonce": str(int(time.time() * 1e9)),
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")

def on_open(ws):
    # 认证
    auth_msg = {
        "requestId": "1",
        "authenticateCustomerRequest": {
            "token": f"Bearer {generate_jwt()}"
        }
    }
    ws.send(json.dumps(auth_msg))

    # 订阅 BTC-USDT Ticker
    sub_msg = {
        "requestId": "2",
        "subscribeMarketsTickerRequest": {
            "markets": ["BTC-USDT"]
        }
    }
    ws.send(json.dumps(sub_msg))

def on_message(ws, message):
    data = json.loads(message)
    print(data)

ws = websocket.WebSocketApp(
    "wss://big.one/ws/v2",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}")
)
ws.run_forever()
```

### 订阅/取消订阅

| 操作 | 请求字段 | 说明 |
|------|---------|------|
| 订阅 Ticker | `subscribeMarketsTickerRequest` | markets 数组 |
| 取消 Ticker | `unsubscribeMarketsTickerRequest` | markets 数组 |
| 订阅成交 | `subscribeMarketTradesRequest` | market 字符串 |
| 取消成交 | `unsubscribeMarketTradesRequest` | market 字符串 |
| 订阅深度 | `subscribeMarketDepthRequest` | market 字符串 |
| 订阅订单 | `subscribeOrdersRequest` | 需认证 |

## 错误处理

### 响应格式

所有 API 响应遵循统一格式：

**成功**:
```json
{
  "data": { ... }
}
```

**错误**:
```json
{
  "errors": [
    {
      "code": 10030,
      "message": "INVALID_ARGUMENT",
      "detail": "Invalid asset pair name"
    }
  ]
}
```

### 常见错误码

| 错误码 | HTTP状态码 | 描述 |
|--------|-----------|------|
| 10001 | 401 | 认证失败 (Authentication failed) |
| 10002 | 403 | 权限不足 (Permission denied) |
| 10010 | 400 | 参数错误 (Invalid argument) |
| 10013 | 400 | 交易对不存在 (Asset pair not found) |
| 10020 | 400 | 余额不足 (Insufficient funds) |
| 10030 | 400 | 无效参数 (Invalid argument) |
| 10031 | 400 | 订单不存在 (Order not found) |
| 10033 | 400 | 价格超出限制 (Price out of range) |
| 10034 | 400 | 数量过小 (Amount too small) |
| 10050 | 429 | 超过速率限制 (Rate limit exceeded) |
| 10060 | 500 | 服务器内部错误 (Internal server error) |

### Python 错误处理示例

```python
def safe_api_call(method, url, **kwargs):
    """带错误处理的 API 调用"""
    try:
        if method == "GET":
            resp = requests.get(url, **kwargs)
        elif method == "POST":
            resp = requests.post(url, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")

        result = resp.json()

        if "errors" in result:
            for error in result["errors"]:
                code = error.get("code", "unknown")
                msg = error.get("message", "")
                detail = error.get("detail", "")
                print(f"API Error [{code}]: {msg} - {detail}")

                if code == 10050:
                    print("Rate limited, waiting 1 second...")
                    time.sleep(1)
                    return safe_api_call(method, url, **kwargs)
                elif code == 10001:
                    print("Authentication failed, regenerating JWT...")
                    kwargs["headers"] = get_headers()
                    return safe_api_call(method, url, **kwargs)
            return None

        return result.get("data")

    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        return None
```

## 代码示例

### Python 完整交易示例

```python
import jwt
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://big.one/api/v3"

def generate_jwt_token():
    payload = {
        "type": "OpenAPIV2",
        "sub": API_KEY,
        "nonce": str(int(time.time() * 1e9)),
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")

def get_headers():
    return {
        "Authorization": f"Bearer {generate_jwt_token()}",
        "Content-Type": "application/json",
    }

# ===== 公共接口 =====

def get_ticker(pair):
    """获取行情"""
    url = f"{BASE_URL}/asset_pairs/{pair}/ticker"
    return requests.get(url).json().get("data")

def get_depth(pair, limit=20):
    """获取深度"""
    url = f"{BASE_URL}/asset_pairs/{pair}/depth"
    return requests.get(url, params={"limit": limit}).json().get("data")

def get_candles(pair, period="hour1", limit=100):
    """获取K线"""
    url = f"{BASE_URL}/asset_pairs/{pair}/candles"
    return requests.get(url, params={"period": period, "limit": limit}).json().get("data")

# ===== 私有接口 =====

def get_accounts():
    """获取账户余额"""
    url = f"{BASE_URL}/viewer/accounts"
    return requests.get(url, headers=get_headers()).json().get("data")

def place_order(pair, side, price, amount, order_type="LIMIT"):
    """下单"""
    url = f"{BASE_URL}/viewer/orders"
    data = {
        "asset_pair_name": pair,
        "side": side,
        "type": order_type,
        "price": str(price),
        "amount": str(amount),
    }
    return requests.post(url, headers=get_headers(), data=json.dumps(data)).json()

def cancel_order(order_id):
    """撤单"""
    url = f"{BASE_URL}/viewer/orders/{order_id}/cancel"
    return requests.post(url, headers=get_headers()).json()

def get_open_orders(pair=None):
    """查询未成交订单"""
    url = f"{BASE_URL}/viewer/orders"
    params = {"state": "PENDING"}
    if pair:
        params["asset_pair_name"] = pair
    return requests.get(url, headers=get_headers(), params=params).json().get("data")

# ===== 使用示例 =====

# 获取 BTC-USDT 行情
ticker = get_ticker("BTC-USDT")
if ticker:
    print(f"BTC-USDT Last: {ticker['close']}, High: {ticker['high']}, Low: {ticker['low']}")

# 获取深度
depth = get_depth("BTC-USDT", limit=5)
if depth:
    print(f"Best bid: {depth['bids'][0]['price']}")
    print(f"Best ask: {depth['asks'][0]['price']}")

# 获取账户余额
accounts = get_accounts()
if accounts:
    for acc in accounts:
        bal = float(acc.get("balance", 0))
        if bal > 0:
            print(f"{acc['asset_symbol']}: {bal}")

# 下限价买单
result = place_order("BTC-USDT", "BID", "90000.00", "0.001")
print(f"Order result: {result}")
```

## 合约API概要

BigONE 合约 API 使用 v2 版本，基础路径为 `/api/contract/v2`。

| 端点 | 方法 | 描述 |
|------|------|------|
| `/symbols` | GET | 获取合约交易对列表 |
| `/instruments` | GET | 获取合约工具信息 |
| `/depth@{symbol}/snapshot` | GET | 合约深度快照 |
| `/instruments/prices` | GET | 合约价格 |
| `/accounts` | GET | 合约账户余额（需认证） |
| `/orders` | POST | 创建合约订单（需认证） |
| `/orders/{id}` | DELETE | 撤销合约订单（需认证） |
| `/positions/{symbol}/margin` | PUT | 调整保证金（需认证） |
| `/positions/{symbol}/risk-limit` | PUT | 调整风控限制（需认证） |

## 变更历史

### 2026-02-27
- 完善文档，添加详细 REST API 端点说明
- 添加 JWT (HS256) 认证完整 Python 示例
- 添加市场数据 API 详细说明和响应示例
- 添加交易 API 完整示例（下单/撤单/查询）
- 添加 WebSocket 订阅和认证示例
- 添加速率限制和错误处理
- 添加合约 API 概要

---

## 相关资源

- [BigONE 官方 API 文档](https://open.big.one/docs/api.html)
- [BigONE 官网](https://big.one)
- [BigONE Python SDK (非官方)](https://github.com/mchardysam/python-bigone)
- [CCXT BigONE 实现](https://github.com/ccxt/ccxt/blob/master/python/ccxt/bigone.py)
- [BigONE 费率说明](https://bigone.zendesk.com/hc/en-us/articles/115001933374)

---

*本文档由 bt_api_py 项目维护，内容基于 BigONE 官方 API 文档整理。*
