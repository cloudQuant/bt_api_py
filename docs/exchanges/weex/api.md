# WEEX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: WEEX
- 官网: <https://www.weex.com>
- CMC 衍生品排名: #22
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(USDT-M Perpetual)、跟单交易
- 最大杠杆: 200x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.weex.com` | 主端点 |
| WebSocket (公共) | `wss://ws.weex.com/ws/public` | 公共行情 |
| WebSocket (私有) | `wss://ws.weex.com/ws/private` | 私有流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `ACCESS-KEY`: API Key
- `ACCESS-SIGN`: Base64(HMAC SHA256)
- `ACCESS-TIMESTAMP`: ISO 格式时间戳
- `ACCESS-PASSPHRASE`: Passphrase

**签名字符串**: `timestamp + method + requestPath + body`

```python
import hmac, time, base64, requests, json
from hashlib import sha256
from datetime import datetime, timezone

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
PASSPHRASE = "your_passphrase"
BASE_URL = "https://api.weex.com"

def signed_request(method, path, body=None):
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    body_str = json.dumps(body) if body else ""
    sign_str = f"{timestamp}{method.upper()}{path}{body_str}"
    signature = base64.b64encode(
        hmac.new(SECRET_KEY.encode(), sign_str.encode(), sha256).digest()
    ).decode()
    headers = {
        "ACCESS-KEY": API_KEY, "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp, "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, headers=headers).json()
    return requests.post(url, data=body_str, headers=headers).json()
```

## 市场数据 API

### 1. 获取合约列表
- **端点**: `GET /api/v1/market/contracts`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/market/tickers`
- **参数**: `productType` (umcbl/dmcbl)

### 3. 获取深度
- **端点**: `GET /api/v1/market/depth`
- **参数**: `symbol`, `limit`

### 4. 获取 K 线
- **端点**: `GET /api/v1/market/candles`
- **参数**: `symbol`, `granularity` (1m,5m,15m,30m,1H,4H,1D), `limit`

### 5. 获取最近成交
- **端点**: `GET /api/v1/market/fills`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/trade/orders`
- **参数**: `symbol`, `marginMode`, `side`, `orderType`, `size`, `price`

### 2. 撤单
- **端点**: `POST /api/v1/trade/cancel-order`
- **参数**: `symbol`, `orderId`

### 3. 查询余额
- **端点**: `GET /api/v1/account/accounts`

### 4. 查询持仓
- **端点**: `GET /api/v1/position/allPosition`

## WebSocket

### 订阅格式
```json
{"op": "subscribe", "args": [{"channel": "books", "instId": "BTCUSDT"}]}
```

### 频道: `books`, `trades`, `tickers`, `candle{period}`

## 特殊说明

- API 风格类似 Bitget/OKX
- 需要 Passphrase
- 签名使用 Base64(HMAC SHA256)
- 合约格式: `BTCUSDT`

## 相关资源

- [WEEX 官网](https://www.weex.com)
