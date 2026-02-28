# BeQuant API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: v3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://api.bequant.io/>
- API Explorer: <https://api.bequant.io/api/3/explore/>

## 交易所基本信息

- 官方名称: BeQuant
- 官网: <https://bequant.io>
- 交易所类型: CEX (中心化交易所)
- 24h 交易量排名: #34 ($80M+)
- 支持的交易对: 200+
- 支持的交易类型: 现货(Spot)、杠杆(Margin)、合约(Futures)
- API 版本: v3（推荐），v2 仍可用

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.bequant.io/api/3`> | 主端点 |

| WS 公共数据 | `wss://api.bequant.io/api/3/ws/public` | 行情数据流 |

| WS 交易 | `wss://api.bequant.io/api/3/ws/trading` | 交易数据流 |

| WS 钱包 | `wss://api.bequant.io/api/3/ws/wallet` | 钱包数据流 |

## 认证方式

### API 密钥获取

1. 登录 BeQuant 账户
2. 进入 API 管理页面
3. 创建 API Key，设置权限：
   - **Orderbook, History, Trading balance**- 读取权限
   - **Place/cancel orders**- 交易权限
   - **Payment information** - 钱包信息权限
1. 保存 API Key 与 Secret Key

### 方式一：Basic 认证

```python
import requests

session = requests.Session()
session.auth = ("your_api_key", "your_secret_key")
response = session.get("<https://api.bequant.io/api/3/spot/balance")>
print(response.json())

```bash

### 方式二：HS256 签名认证

- *签名步骤**:
1. 构建签名消息: `method + URL_path + [?query] + [body] + timestamp + [window]`
2. 使用 Secret Key 进行 HMAC SHA256 签名
3. 设置请求头: `Authorization: HS256 Base64(apiKey:signature:timestamp[:window])`

```python
from base64 import b64encode
from hashlib import sha256
from hmac import HMAC
from time import time
from urllib.parse import urlsplit
from requests import Session
from requests.auth import AuthBase

class HS256Auth(AuthBase):
    def __init__(self, api_key, secret_key, window=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.window = window

    def __call__(self, r):
        url = urlsplit(r.url)
        message = [r.method, url.path]
        if url.query:
            message.append('?')
            message.append(url.query)
        if r.body:
            message.append(r.body)
        timestamp = str(int(time() *1000))
        window = str(self.window) if self.window else None
        message.append(timestamp)
        if window:
            message.append(window)
        signature = HMAC(
            key=self.secret_key.encode(),
            msg=''.join(message).encode(),
            digestmod=sha256
        ).hexdigest()
        data = [self.api_key, signature, timestamp]
        if window:
            data.append(window)
        encoded = b64encode(':'.join(data).encode()).decode()
        r.headers['Authorization'] = f'HS256 {encoded}'
        return r

# 使用示例

auth = HS256Auth(api_key='YOUR_KEY', secret_key='YOUR_SECRET')
with Session() as s:
    resp = s.get('<https://api.bequant.io/api/3/wallet/balance',> auth=auth)
    print(resp.json())

```bash

## 市场数据 API

### 1. 获取货币列表

- *端点**: `GET /api/3/public/currency`

- *描述**: 返回所有可用货币及网络信息。无需认证。

- *响应示例**:

```json
{
  "BTC": {
    "full_name": "Bitcoin",
    "crypto": true,
    "payin_enabled": true,
    "payout_enabled": true,
    "transfer_enabled": true,
    "precision_transfer": "0.00000001",
    "networks": [
      {
        "network": "BTC",
        "protocol": "BTC",
        "default": true,
        "payin_enabled": true,
        "payout_enabled": true,
        "payout_fee": "0.0005",
        "payin_confirmations": 3
      }
    ]
  }
}

```bash

### 2. 获取交易对信息

- *端点**: `GET /api/3/public/symbol`

- *描述**: 返回所有交易对信息，包含价格精度、数量精度等。

### 3. 获取行情数据

- *端点**: `GET /api/3/public/ticker` / `GET /api/3/public/ticker/{symbol}`

- *描述**: 获取所有或指定交易对的 Ticker 数据。

- *参数**: `symbols` (可选) - 逗号分隔的交易对列表

- *响应示例**:

```json
{
  "ETHBTC": {
    "ask": "0.050043",
    "bid": "0.050042",
    "last": "0.050042",
    "low": "0.047052",
    "high": "0.051679",
    "open": "0.047800",
    "volume": "36456.720",
    "volume_quote": "1782.625000",
    "timestamp": "2024-04-12T14:57:19.999Z"
  }
}

```bash

### 4. 获取深度数据

- *端点**: `GET /api/3/public/orderbook/{symbol}`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| depth | INT | 否 | 深度档位，默认 100，0 表示全量 |

| volume | DECIMAL | 否 | 按成交量聚合（指定后 depth 忽略） |

- *响应示例**:

```json
{
  "timestamp": "2024-04-11T11:30:38.597Z",
  "ask": [["9779.68", "2.497"], ["9779.70", "1.200"]],
  "bid": [["9779.67", "0.037"], ["9779.29", "0.171"]]
}

```bash

### 5. 获取 K 线数据

- *端点**: `GET /api/3/public/candles/{symbol}`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| period | ENUM | 否 | K 线周期: M1,M3,M5,M15,M30,H1,H4,D1,D7,1M。默认 M30 |

| sort | ENUM | 否 | ASC/DESC，默认 DESC |

| from | DateTime | 否 | 起始时间 |

| till | DateTime | 否 | 结束时间 |

| limit | INT | 否 | 返回数量，默认 100，最大 1000 |

- *响应示例**:

```json
[
  {
    "timestamp": "2024-04-20T20:00:00.000Z",
    "open": "0.050459",
    "close": "0.050087",
    "min": "0.050000",
    "max": "0.050511",
    "volume": "1326.628",
    "volume_quote": "66.555987736"
  }
]

```bash

### 6. 获取最近成交

- *端点**: `GET /api/3/public/trades/{symbol}`

- *参数**: `sort`, `from`, `till`, `limit`, `offset`

## 交易 API

### 1. 查询现货交易余额

- *端点**: `GET /api/3/spot/balance`

- *权限**: Orderbook, History, Trading balance

- *响应示例**:

```json
[
  {
    "currency": "ETH",
    "available": "10.000000000",
    "reserved": "0.56",
    "reserved_margin": "0",
    "cross_margin_reserved": "0"
  }
]

```bash

### 2. 创建现货订单

- *端点**: `POST /api/3/spot/order`

- *权限**: Place/cancel orders

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对，如 ETHBTC |

| side | ENUM | 是 | buy / sell |

| type | ENUM | 否 | limit(默认), market, stopLimit, stopMarket, takeProfitLimit, takeProfitMarket |

| time_in_force | ENUM | 否 | GTC(默认), IOC, FOK, Day, GTD |

| quantity | DECIMAL | 是 | 订单数量 |

| price | DECIMAL | 条件 | 限价单必需 |

| stop_price | DECIMAL | 条件 | 止损/止盈单必需 |

| post_only | BOOL | 否 | 仅做 Maker |

| client_order_id | STRING | 否 | 客户自定义 ID |

- *Python 示例**:

```python
import requests

session = requests.Session()
session.auth = ("apiKey", "secretKey")

order_data = {
    'symbol': 'ETHBTC',
    'side': 'sell',
    'quantity': '0.063',
    'price': '0.046016'
}
r = session.post('<https://api.bequant.io/api/3/spot/order',> data=order_data)
print(r.json())

```bash

- *响应示例**:

```json
{
  "id": 828680665,
  "client_order_id": "f4307c6e507e49019907c917b6d7a084",
  "symbol": "ETHBTC",
  "side": "sell",
  "status": "new",
  "type": "limit",
  "time_in_force": "GTC",
  "quantity": "0.063",
  "price": "0.046016",
  "quantity_cumulative": "0.000",
  "post_only": false,
  "created_at": "2024-04-15T17:01:05.092Z",
  "updated_at": "2024-04-15T17:01:05.092Z"
}

```bash

### 3. 撤销订单

- *端点**: `DELETE /api/3/spot/order/{client_order_id}`

- *权限**: Place/cancel orders

### 4. 撤销所有订单

- *端点**: `DELETE /api/3/spot/order`

- *参数**: `symbol` (可选) - 指定交易对

### 5. 查询活跃订单

- *端点**: `GET /api/3/spot/order`

### 6. 查询历史订单

- *端点**: `GET /api/3/spot/history/order`

- *参数**: `symbol`, `sort`, `from`, `till`, `limit`, `offset`

### 7. 查询成交历史

- *端点**: `GET /api/3/spot/history/trade`

### 8. 查询手续费

- *端点**: `GET /api/3/spot/fee` / `GET /api/3/spot/fee/{symbol}`

- *响应示例**:

```json
{"symbol": "ETHBTC", "take_rate": "0.001", "make_rate": "-0.0001"}

```bash

## 账户管理 API

### 1. 查询钱包余额

- *端点**: `GET /api/3/wallet/balance`

- *权限**: Payment information

- *响应示例**:

```json
[
  {"currency": "BTC", "available": "0.00005821", "reserved": "0.00001"},
  {"currency": "USDT", "available": "0.01", "reserved": "0"}
]

```bash

### 2. 获取充币地址

- *端点**: `GET /api/3/wallet/crypto/address`

- *参数**: `currency`, `network_code`

### 3. 提币

- *端点**: `POST /api/3/wallet/crypto/withdraw`

### 4. 内部转账

- *端点**: `POST /api/3/wallet/transfer`

- *描述**: 在钱包(Wallet)和交易(Spot)账户之间转账。

### 5. 交易记录

- *端点**: `GET /api/3/wallet/transactions`

## 速率限制

### REST API（按 IP）

| 端点 | 请求/秒 | 突发 |

|------|---------|------|

| `/*` (默认) | 20 | 30 |

| `/public/*` | 30 | 50 |

| `/wallet/*` | 10 | 10 |

| `/spot/order/*` | 300 | 450 |

| `/margin/order/*` | 300 | 450 |

| `/futures/order/*` | 300 | 450 |

### WebSocket（按 IP）

| 端点 | 连接/秒 | 突发 |

|------|---------|------|

| `/ws/public` | 10 | 10 |

| `/ws/trading` | 10 | 10 |

| `/ws/wallet` | 10 | 10 |

### 订单限制（按账户）

- 全市场活跃挂单上限: **25,000**
- 单交易对活跃挂单上限: **2,000**

### 触发限制后的行为

- **HTTP 429**: 超过速率限制
- 建议实施指数退避重试策略

## WebSocket 支持

BeQuant 提供三个独立的 WebSocket 端点：

### 公共频道 (`/ws/public`)

- **Trades**: 实时成交
- **Candles**: K 线数据
- **Ticker / Mini Ticker**: 行情数据
- **Order Book**: 订单簿（全量/增量/Top）
- **Price Rates**: 价格汇率

### 交易频道 (`/ws/trading`) - 需认证

- **Reports**: 订单状态更新
- **Balances**: 交易余额更新
- 支持下单、撤单、改单操作

### 钱包频道 (`/ws/wallet`) - 需认证

- **Transactions**: 充值/提现状态更新
- **Wallet Balances**: 钱包余额更新

### WebSocket 认证

支持 Basic 和 HS256 两种方式：

```python
import websocket
import json

def on_open(ws):

# Basic 认证
    auth_msg = {
        "method": "login",
        "params": {
            "algo": "BASIC",
            "pKey": "your_api_key",
            "sKey": "your_secret_key"
        }
    }
    ws.send(json.dumps(auth_msg))

# 订阅订单更新
    ws.send(json.dumps({"method": "spot_subscribe"}))

ws = websocket.WebSocketApp(
    "wss://api.bequant.io/api/3/ws/trading",
    on_open=on_open,
    on_message=lambda ws, msg: print(json.loads(msg))
)
ws.run_forever()

```bash

## 错误代码

### HTTP 状态码

| 状态码 | 描述 |

|--------|------|

| 200 | 成功 |

| 400 | 请求参数错误 |

| 401 | 认证失败 |

| 403 | 权限不足 |

| 404 | 资源未找到 |

| 429 | 超过速率限制 |

| 500 | 服务器内部错误 |

| 503 | 服务维护中 |

| 504 | 网关超时 |

### 业务错误码

| 错误码 | 描述 |

|--------|------|

| 20001 | Insufficient funds（余额不足） |

| 20002 | Order not found（订单不存在） |

| 20008 | Quantity too low（数量过小） |

| 20001 | Insufficient funds（含手续费不足） |

| 10001 | Validation error（参数验证错误） |

| 1002 | Authorization failed（认证失败） |

| 1004 | Authorization required（需要认证） |

| 1005 | Forbidden action（操作被禁止） |

- *错误响应格式**:

```json
{
  "error": {
    "code": 20001,
    "message": "Insufficient funds",
    "description": "Check that the funds are sufficient, given commissions"
  }
}

```bash

## 代码示例

### Python 完整示例

```python
import requests

BASE_URL = "<https://api.bequant.io/api/3">

# ===== 公共接口（无需认证）=====

def get_ticker(symbol=None):
    """获取行情"""
    if symbol:
        url = f"{BASE_URL}/public/ticker/{symbol}"
    else:
        url = f"{BASE_URL}/public/ticker"
    return requests.get(url).json()

def get_orderbook(symbol, depth=20):
    """获取深度"""
    url = f"{BASE_URL}/public/orderbook/{symbol}"
    return requests.get(url, params={"depth": depth}).json()

def get_candles(symbol, period="H1", limit=100):
    """获取 K 线"""
    url = f"{BASE_URL}/public/candles/{symbol}"
    return requests.get(url, params={"period": period, "limit": limit}).json()

# ===== 私有接口（需认证）=====

session = requests.Session()
session.auth = ("YOUR_API_KEY", "YOUR_SECRET_KEY")

def get_balance():
    """获取交易余额"""
    return session.get(f"{BASE_URL}/spot/balance").json()

def place_order(symbol, side, quantity, price=None, order_type="limit"):
    """下单"""
    data = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": quantity,
    }
    if price:
        data["price"] = price
    return session.post(f"{BASE_URL}/spot/order", data=data).json()

def cancel_order(client_order_id):
    """撤单"""
    return session.delete(f"{BASE_URL}/spot/order/{client_order_id}").json()

# ===== 使用示例 =====

ticker = get_ticker("BTCUSDT")
print(f"BTC/USDT Last: {ticker.get('last')}")

book = get_orderbook("BTCUSDT", depth=5)
print(f"Best bid: {book['bid'][0]}, Best ask: {book['ask'][0]}")

candles = get_candles("BTCUSDT", period="H1", limit=3)
for c in candles:
    print(f"{c['timestamp']}: O={c['open']} H={c['max']} L={c['min']} C={c['close']}")

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加 Basic/HS256 认证完整代码示例
- 添加 WebSocket 三端点详细说明
- 添加速率限制详细规则
- 添加错误码表

- --

## 相关资源

- [BeQuant 官方 API 文档](<https://api.bequant.io/)>
- [API v3 Swagger Explorer](<https://api.bequant.io/api/3/explore/)>
- [BeQuant 官网](<https://bequant.io)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 BeQuant 官方 API 文档整理。*
