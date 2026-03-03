# Toobit API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://toobit-docs.github.io/apidocs/spot/v1/en/>

## 交易所基本信息

- 官方名称: Toobit
- 官网: <https://www.toobit.com>
- CMC 衍生品排名: #19
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、USDT永续合约(USDT-M Perpetual)
- 手续费: Maker 0.1%, Taker 0.1% (现货); Maker 0.02%, Taker 0.06% (合约)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://api.toobit.com` | 现货端点 |
| REST (合约) | `https://api.toobit.com` | 合约端点(同域) |
| WebSocket (现货) | `wss://stream.toobit.com/quote/ws/v1` | 现货行情 |
| WebSocket (合约) | `wss://stream.toobit.com/contract/ws/v1` | 合约行情 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-BB-APIKEY`: API Key

**签名步骤**:
1. 将所有参数按 ASCII 排序拼接
2. 添加 `timestamp` 参数
3. 使用 Secret Key 进行 HMAC SHA256 签名
4. 签名作为 `signature` 参数

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.toobit.com"

def signed_request(method, path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(sorted(params.items()))
    signature = hmac.new(
        SECRET_KEY.encode(), query.encode(), sha256
    ).hexdigest()
    params["signature"] = signature
    headers = {"X-BB-APIKEY": API_KEY}
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, params=params, headers=headers).json()
```

## 市场数据 API (现货)

### 1. 获取交易对信息
- **端点**: `GET /api/v1/exchangeInfo`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/ticker/24hr`
- **参数**: `symbol` (可选)

### 3. 获取深度
- **端点**: `GET /api/v1/depth`
- **参数**: `symbol` (必需), `limit` (可选，默认100)

### 4. 获取 K 线
- **端点**: `GET /api/v1/klines`
- **参数**: `symbol`, `interval` (1m,5m,15m,30m,1h,4h,1d,1w), `limit`

### 5. 获取最近成交
- **端点**: `GET /api/v1/trades`
- **参数**: `symbol` (必需), `limit` (可选)

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/order`
- **参数**: `symbol`, `side` (BUY/SELL), `type` (LIMIT/MARKET), `quantity`, `price`

### 2. 撤单
- **端点**: `DELETE /api/v1/order`
- **参数**: `orderId` 或 `clientOrderId`

### 3. 查询余额
- **端点**: `GET /api/v1/account`

## 合约 API

### 1. 合约行情
- **端点**: `GET /api/v1/contract/ticker`

### 2. 合约下单
- **端点**: `POST /api/v1/contract/order`
- **参数**: `symbol`, `side`, `orderType`, `quantity`, `price`, `leverage`

## WebSocket

### 订阅格式
```json
{"symbol": "BTCUSDT", "topic": "depth", "event": "sub"}
```

### 频道: `depth`, `trade`, `kline_{interval}`, `realtimes`
### 心跳: 服务器发 `ping`，客户端回 `pong`

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 20次/秒 |
| 私有接口 | 10次/秒 |

## 特殊说明

- API 风格类似 Binance/Bybit
- 交易对格式: `BTCUSDT` (大写无分隔符)
- 支持现货和 USDT-M 永续合约

## 相关资源

- [Toobit 现货 API](https://toobit-docs.github.io/apidocs/spot/v1/en/)
- [Toobit 合约 API](https://toobit-docs.github.io/apidocs/usdt_swap/v1/en/)
