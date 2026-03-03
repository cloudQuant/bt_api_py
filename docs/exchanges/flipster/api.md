# Flipster API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://api-docs.flipster.io>

## 交易所基本信息

- 官方名称: Flipster
- 官网: <https://flipster.io>
- CMC 衍生品排名: #61
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(Perpetual Futures)
- 支持的交易对: 400+
- 手续费: 阶梯制(按交易量)
- 最大杠杆: 100x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.flipster.io` | 主端点 |
| WebSocket | `wss://ws.flipster.io` | 行情流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-API-Key`: API Key
- `X-API-Sign`: HMAC SHA256 签名
- `api-expires`: Unix 时间戳(秒)，请求过期时间

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.flipster.io"

def signed_request(method, path, params=None, body=None):
    expires = str(int(time.time()) + 30)
    body_str = ""
    if body:
        import json
        body_str = json.dumps(body)
    sign_str = f"{method.upper()}{path}{expires}{body_str}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "X-API-Key": API_KEY,
        "X-API-Sign": signature,
        "api-expires": expires,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, data=body_str, headers=headers).json()
```

## 市场数据 API

### 1. 获取产品列表
- **端点**: `GET /api/v1/products`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/ticker`
- **参数**: `symbol` (可选)

### 3. 获取深度
- **端点**: `GET /api/v1/orderbook`
- **参数**: `symbol` (必需), `depth` (可选)

### 4. 获取 K 线
- **端点**: `GET /api/v1/candles`
- **参数**: `symbol`, `resolution`, `startTime`, `endTime`, `limit`

### 5. 获取最近成交
- **端点**: `GET /api/v1/trades`
- **参数**: `symbol`, `limit`

### 6. 获取服务器时间
- **端点**: `GET /api/v1/time`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/order`
- **参数**: `symbol`, `side` (buy/sell), `type` (limit/market), `quantity`, `price`, `reduceOnly`

### 2. 撤单
- **端点**: `DELETE /api/v1/order`
- **参数**: `orderId`

### 3. 查询余额
- **端点**: `GET /api/v1/balances`

### 4. 查询持仓
- **端点**: `GET /api/v1/positions`

## WebSocket

### 订阅: `{"type": "subscribe", "channels": ["orderbook:BTC-PERP"]}`
### 频道: `orderbook`, `trades`, `ticker`, `candles`
### 心跳: 定期 ping/pong

## 特殊说明

- 仅支持永续合约交易
- 使用 `api-expires` 防止重放攻击
- 使用服务器时间端点校准时钟

## 相关资源

- [Flipster API 文档](https://api-docs.flipster.io)
- [Flipster 官网](https://flipster.io)
