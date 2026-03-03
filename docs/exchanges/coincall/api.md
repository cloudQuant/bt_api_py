# Coincall API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.coincall.com>
- GitHub: <https://github.com/Coincall-exchange/python-coincall>

## 交易所基本信息

- 官方名称: Coincall
- 官网: <https://www.coincall.com>
- CMC 衍生品排名: #111
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 期权(Options)、永续合约(Perpetual Futures)、现货(Spot)
- 手续费: Maker 0.02%, Taker 0.05% (合约); 期权另计

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.coincall.com` | 主端点 |
| WebSocket (公共) | `wss://ws.coincall.com/public` | 公共行情 |
| WebSocket (私有) | `wss://ws.coincall.com/private` | 私有流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-CC-APIKEY`: API Key
- `sign`: HMAC SHA256 签名
- `timestamp`: 毫秒时间戳

**签名字符串**: `timestamp + method + path + body`

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.coincall.com"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = ""
    if body:
        import json
        body_str = json.dumps(body)
    sign_str = f"{timestamp}{method.upper()}{path}{body_str}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "X-CC-APIKEY": API_KEY,
        "sign": signature,
        "timestamp": timestamp,
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
- **端点**: `GET /open/option/market/instruments` (期权)
- **端点**: `GET /open/futures/market/instruments` (合约)

### 2. 获取 Ticker
- **端点**: `GET /open/futures/market/ticker`
- **参数**: `symbol` (必需)

### 3. 获取深度
- **端点**: `GET /open/futures/market/orderbook`
- **参数**: `symbol` (必需), `depth` (可选)

### 4. 获取 K 线
- **端点**: `GET /open/futures/market/kline`
- **参数**: `symbol`, `period` (1m,5m,15m,30m,1h,4h,1d), `startTime`, `endTime`

### 5. 获取最近成交
- **端点**: `GET /open/futures/market/trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 合约下单
- **端点**: `POST /open/futures/trade/order`
- **参数**: `symbol`, `side` (1=买/2=卖), `type` (1=limit/3=market), `qty`, `price`

### 2. 期权下单
- **端点**: `POST /open/option/trade/order`
- **参数**: `symbol`, `side`, `type`, `qty`, `price`

### 3. 撤单
- **端点**: `POST /open/futures/trade/cancel`
- **参数**: `orderId`

### 4. 查询余额
- **端点**: `GET /open/account/balance`

### 5. 查询持仓
- **端点**: `GET /open/futures/position/list`

## WebSocket

### 订阅格式
```json
{"op": "subscribe", "args": [{"channel": "orderbook", "instId": "BTCUSDT"}]}
```

### 频道: `orderbook`, `trades`, `ticker`, `kline`
### 心跳: 每 30 秒 ping/pong

## 特殊说明

- 同时支持期权和期货
- 期权和期货使用不同的 API 路径
- side 使用数字(1=买, 2=卖)
- 每用户最多 20 个 API Key，每 Key 绑定 20 个 IP

## 相关资源

- [Coincall API 文档](https://docs.coincall.com)
- [Coincall Python SDK](https://github.com/Coincall-exchange/python-coincall)
