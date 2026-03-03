# FameEX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://api-doc.fameex.com>

## 交易所基本信息

- 官方名称: FameEX
- 官网: <https://www.fameex.com>
- CMC 衍生品排名: #34
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)
- 最大杠杆: 200x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.fameex.com` | 主端点 |
| WebSocket | `wss://ws.fameex.com/ws` | 行情流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `AccessKey`: API Key
- `Signature`: HMAC SHA256 签名
- `Timestamp`: 毫秒时间戳

```python
import hmac, time, requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.fameex.com"

def signed_request(method, path, params=None):
    timestamp = str(int(time.time() * 1000))
    sign_str = f"{timestamp}{method.upper()}{path}"
    if params:
        from urllib.parse import urlencode
        sign_str += urlencode(sorted(params.items()))
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "AccessKey": API_KEY,
        "Signature": signature,
        "Timestamp": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /v1/common/symbols`

### 2. 获取 Ticker
- **端点**: `GET /v1/market/ticker`
- **参数**: `symbol`

### 3. 获取深度
- **端点**: `GET /v1/market/depth`
- **参数**: `symbol`, `limit`

### 4. 获取 K 线
- **端点**: `GET /v1/market/kline`
- **参数**: `symbol`, `period`, `size`

### 5. 获取成交
- **端点**: `GET /v1/market/trades`
- **参数**: `symbol`, `size`

## 交易 API

### 1. 下单
- **端点**: `POST /v1/order/place`
- **参数**: `symbol`, `side`, `type`, `amount`, `price`

### 2. 撤单
- **端点**: `POST /v1/order/cancel`
- **参数**: `orderId`

### 3. 查询余额
- **端点**: `GET /v1/account/balance`

## WebSocket

### 订阅: `{"sub": "market.BTCUSDT.depth.step0"}`
### 频道: `depth`, `trade`, `kline`, `ticker`

## 特殊说明

- 支持网格交易、跟单交易等特色功能
- 交易对格式: `BTCUSDT`

## 相关资源

- [FameEX API 文档](https://api-doc.fameex.com)
- [FameEX 官网](https://www.fameex.com)
