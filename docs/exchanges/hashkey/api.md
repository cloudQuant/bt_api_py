# HashKey Global API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://hashkeyglobal-apidoc.readme.io>

## 交易所基本信息

- 官方名称: HashKey Global
- 官网: <https://global.hashkey.com>
- CMC 衍生品排名: #42
- 交易所类型: CEX (中心化交易所)
- 牌照: 香港持牌虚拟资产交易平台
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual)
- 手续费: Maker 0.05%, Taker 0.10% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api-pro.hashkey.com` | 主端点 |
| WebSocket | `wss://stream-pro.hashkey.com` | 行情/私有流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-HK-APIKEY`: API Key
- `X-HK-SIGN`: HMAC SHA256 签名
- `X-HK-TIMESTAMP`: 毫秒时间戳

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api-pro.hashkey.com"

def signed_request(method, path, params=None):
    timestamp = str(int(time.time() * 1000))
    query = urlencode(sorted(params.items())) if params else ""
    sign_str = f"{timestamp}{method.upper()}{path}{query}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "X-HK-APIKEY": API_KEY,
        "X-HK-SIGN": signature,
        "X-HK-TIMESTAMP": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /api/v1/exchangeInfo`

### 2. 获取 Ticker
- **端点**: `GET /quote/v1/ticker/24hr`
- **参数**: `symbol` (可选)

### 3. 获取深度
- **端点**: `GET /quote/v1/depth`
- **参数**: `symbol` (必需), `limit` (可选, 5/10/20/50/100)

### 4. 获取 K 线
- **端点**: `GET /quote/v1/klines`
- **参数**: `symbol`, `interval` (1m,5m,15m,30m,1h,4h,1d,1w), `limit`

### 5. 获取最近成交
- **端点**: `GET /quote/v1/trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/spot/order`
- **参数**: `symbol`, `side` (BUY/SELL), `type` (LIMIT/MARKET), `quantity`, `price`

### 2. 撤单
- **端点**: `DELETE /api/v1/spot/order`
- **参数**: `orderId`

### 3. 查询余额
- **端点**: `GET /api/v1/account`

## WebSocket

### 订阅格式
```json
{"sub": "market.BTCUSDT.depth.step0", "id": "1"}
```

### 频道: `depth`, `trade`, `kline`, `ticker`

## 特殊说明

- 香港合规持牌交易所
- API 风格类似 Binance
- 同时支持 REST 和 WebSocket
- 提供 FIX 协议接入（机构客户）

## 相关资源

- [HashKey Global API 文档](https://hashkeyglobal-apidoc.readme.io)
- [HashKey 官网](https://global.hashkey.com)
