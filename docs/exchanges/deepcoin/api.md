# Deepcoin API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://www.deepcoin.com/docs>

## 交易所基本信息

- 官方名称: Deepcoin
- 官网: <https://www.deepcoin.com>
- CMC 衍生品排名: #25
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(USDT-M Perpetual)
- 最大杠杆: 125x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.deepcoin.com` | 主端点 |
| WebSocket (公共) | `wss://ws.deepcoin.com/ws/public` | 公共行情 |
| WebSocket (私有) | `wss://ws.deepcoin.com/ws/private` | 私有流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `DC-ACCESS-KEY`: API Key
- `DC-ACCESS-SIGN`: Base64(HMAC SHA256 签名)
- `DC-ACCESS-TIMESTAMP`: ISO 格式时间戳
- `DC-ACCESS-PASSPHRASE`: API Passphrase

**签名字符串**: `timestamp + method + requestPath + body`

```python
import hmac
import time
import base64
import requests
from hashlib import sha256
from datetime import datetime, timezone

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
PASSPHRASE = "your_passphrase"
BASE_URL = "https://api.deepcoin.com"

def signed_request(method, path, body=None):
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    body_str = ""
    if body:
        import json
        body_str = json.dumps(body)
    sign_str = f"{timestamp}{method.upper()}{path}{body_str}"
    signature = base64.b64encode(
        hmac.new(SECRET_KEY.encode(), sign_str.encode(), sha256).digest()
    ).decode()
    headers = {
        "DC-ACCESS-KEY": API_KEY,
        "DC-ACCESS-SIGN": signature,
        "DC-ACCESS-TIMESTAMP": timestamp,
        "DC-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, headers=headers).json()
    else:
        return requests.post(url, data=body_str, headers=headers).json()
```

## 市场数据 API

### 1. 获取合约列表
- **端点**: `GET /deepcoin/market/instruments`
- **参数**: `instType` (SWAP/SPOT)

### 2. 获取 Ticker
- **端点**: `GET /deepcoin/market/tickers`
- **参数**: `instType` (必需)

### 3. 获取深度
- **端点**: `GET /deepcoin/market/books`
- **参数**: `instId` (必需，如 `BTC-USDT-SWAP`), `sz` (可选)

### 4. 获取 K 线
- **端点**: `GET /deepcoin/market/candles`
- **参数**: `instId`, `bar` (1m,5m,15m,30m,1H,4H,1D,1W), `limit`

### 5. 获取最近成交
- **端点**: `GET /deepcoin/market/trades`
- **参数**: `instId`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /deepcoin/trade/order`
- **参数**: `instId`, `tdMode` (cross/isolated), `side` (buy/sell), `ordType` (market/limit), `sz`, `px`

### 2. 撤单
- **端点**: `POST /deepcoin/trade/cancel-order`
- **参数**: `instId`, `ordId`

### 3. 查询余额
- **端点**: `GET /deepcoin/account/balance`

### 4. 查询持仓
- **端点**: `GET /deepcoin/account/positions`

## WebSocket

### 订阅格式
```json
{"op": "subscribe", "args": [{"channel": "books", "instId": "BTC-USDT-SWAP"}]}
```

### 频道: `books`, `trades`, `tickers`, `candle{period}`
### 心跳: ping/pong

## 特殊说明

- API 风格类似 OKX
- 合约ID格式: `BTC-USDT-SWAP` (大写横线)
- 需要 Passphrase
- 签名使用 Base64(HMAC SHA256)

## 相关资源

- [Deepcoin API 文档](https://www.deepcoin.com/docs)
- [Deepcoin 官网](https://www.deepcoin.com)
