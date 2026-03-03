# Tapbit API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://www.tapbit.com/openapi-docs>

## 交易所基本信息

- 官方名称: Tapbit
- 官网: <https://www.tapbit.com>
- CMC 衍生品排名: #31
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)
- 最大杠杆: 150x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://openapi.tapbit.com` | 现货端点 |
| REST (合约) | `https://openapi.tapbit.com` | 合约端点 |
| WebSocket | `wss://ws.tapbit.com/ws` | 行情流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-ACCESS-KEY`: API Key
- `X-ACCESS-SIGN`: HMAC SHA256 签名
- `X-ACCESS-TIMESTAMP`: 毫秒时间戳

```python
import hmac, time, requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://openapi.tapbit.com"

def signed_request(method, path, params=None):
    timestamp = str(int(time.time() * 1000))
    query = urlencode(sorted(params.items())) if params else ""
    sign_str = f"{timestamp}{method.upper()}{path}{query}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "X-ACCESS-KEY": API_KEY,
        "X-ACCESS-SIGN": signature,
        "X-ACCESS-TIMESTAMP": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /spot/v1/instruments`

### 2. 获取 Ticker
- **端点**: `GET /spot/v1/ticker`
- **参数**: `instId` (可选)

### 3. 获取深度
- **端点**: `GET /spot/v1/depth`
- **参数**: `instId` (必需), `size` (可选)

### 4. 获取 K 线
- **端点**: `GET /spot/v1/kline`
- **参数**: `instId`, `bar` (1m,5m,15m,30m,1H,4H,1D), `limit`

### 5. 获取成交
- **端点**: `GET /spot/v1/trades`
- **参数**: `instId`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /spot/v1/order`
- **参数**: `instId`, `side` (buy/sell), `ordType` (market/limit), `sz`, `px`

### 2. 撤单
- **端点**: `POST /spot/v1/cancel-order`
- **参数**: `instId`, `ordId`

### 3. 查询余额
- **端点**: `GET /spot/v1/account/balance`

## 合约 API

### 1. 合约下单
- **端点**: `POST /swap/v1/order`
- **参数**: `instId`, `tdMode`, `side`, `ordType`, `sz`, `px`

### 2. 查询持仓
- **端点**: `GET /swap/v1/positions`

## WebSocket

### 订阅格式
```json
{"op": "subscribe", "args": [{"channel": "books", "instId": "BTCUSDT"}]}
```

### 频道: `books`, `trades`, `tickers`, `candle{period}`

## 特殊说明

- API 风格类似 OKX
- 合约ID格式: `BTCUSDT`
- 现货和合约使用不同的路径前缀 (`/spot/` vs `/swap/`)

## 相关资源

- [Tapbit API 文档](https://www.tapbit.com/openapi-docs)
- [Tapbit 官网](https://www.tapbit.com)
