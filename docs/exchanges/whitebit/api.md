# WhiteBIT API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.whitebit.com>
- GitHub: <https://github.com/whitebit-exchange/api-docs>

## 交易所基本信息

- 官方名称: WhiteBIT
- 官网: <https://whitebit.com>
- CMC 衍生品排名: #37
- 交易所类型: CEX (中心化交易所)
- 总部: 立陶宛
- 支持的交易类型: 现货(Spot)、保证金(Margin)、永续合约(Perpetual Futures)
- 支持的交易对: 500+
- 手续费: Maker 0.1%, Taker 0.1% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (V4) | `https://whitebit.com/api/v4` | V4 主端点(推荐) |
| REST (V1/V2) | `https://whitebit.com/api/v1` | 旧版本 |
| WebSocket (公共) | `wss://api.whitebit.com/ws` | 公共行情 |
| WebSocket (私有) | `wss://api.whitebit.com/ws` | 私有(需token) |

## 认证方式

### HMAC SHA512 签名 (V4)

**请求头**:
- `X-TXC-APIKEY`: API Key
- `X-TXC-PAYLOAD`: Base64 编码的请求体
- `X-TXC-SIGNATURE`: HMAC SHA512 签名

```python
import hmac
import json
import time
import base64
import requests
from hashlib import sha512

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://whitebit.com"

def signed_request(path, body=None):
    if body is None:
        body = {}
    body["request"] = path
    body["nonce"] = str(int(time.time() * 1000))
    
    payload = base64.b64encode(json.dumps(body).encode())
    signature = hmac.new(
        SECRET_KEY.encode(), payload, sha512
    ).hexdigest()
    
    headers = {
        "X-TXC-APIKEY": API_KEY,
        "X-TXC-PAYLOAD": payload.decode(),
        "X-TXC-SIGNATURE": signature,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    return requests.post(url, json=body, headers=headers).json()
```

## 市场数据 API (V4 公共)

### 1. 获取交易对列表
- **端点**: `GET /api/v4/public/markets`

### 2. 获取 Ticker
- **端点**: `GET /api/v4/public/ticker`
- **描述**: 返回所有交易对24h行情数据

### 3. 获取深度
- **端点**: `GET /api/v4/public/orderbook/{market}`
- **参数**: `limit` (可选, 1-100), `level` (可选, 1/2)

### 4. 获取 K 线
- **端点**: `GET /api/v4/public/kline`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| market | STRING | 是 | 交易对，如 `BTC_USDT` |
| interval | STRING | 是 | 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M |
| start | INT | 否 | 起始时间戳(秒) |
| end | INT | 否 | 结束时间戳(秒) |
| limit | INT | 否 | 数量，默认 500 |

### 5. 获取最近成交
- **端点**: `GET /api/v4/public/trades/{market}`
- **参数**: `type` (sell/buy, 可选)

### 6. 获取合约市场
- **端点**: `GET /api/v4/public/futures`

### 7. 获取资金费率
- **端点**: `GET /api/v4/public/futures/funding/rate`

## 交易 API (V4 私有)

### 1. 查询余额
- **端点**: `POST /api/v4/trade-account/balance`

### 2. 下单
- **端点**: `POST /api/v4/order/new`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| market | STRING | 是 | 交易对 |
| side | ENUM | 是 | buy / sell |
| amount | DECIMAL | 是 | 数量 |
| price | DECIMAL | 条件 | 价格(limit) |
| clientOrderId | STRING | 否 | 自定义ID |

### 3. 撤单
- **端点**: `POST /api/v4/order/cancel`
- **参数**: `market`, `orderId`

### 4. 查询订单
- **端点**: `POST /api/v4/orders`
- **参数**: `market`, `orderId` (可选)

### 5. 查询成交
- **端点**: `POST /api/v4/trade-account/executed-history`

## WebSocket

### 基于 JSON-RPC 协议

```python
import websocket
import json

def on_open(ws):
    # 订阅深度
    ws.send(json.dumps({
        "id": 1,
        "method": "depth_subscribe",
        "params": ["BTC_USDT", 20, "0", True]
    }))
    # 订阅成交
    ws.send(json.dumps({
        "id": 2,
        "method": "trades_subscribe",
        "params": ["BTC_USDT"]
    }))

ws = websocket.WebSocketApp(
    "wss://api.whitebit.com/ws",
    on_open=on_open,
    on_message=lambda ws, msg: print(json.loads(msg))
)
ws.run_forever()
```

### 公共频道方法

| 方法 | 说明 |
|------|------|
| `depth_subscribe` | 深度订阅 |
| `trades_subscribe` | 成交订阅 |
| `kline_subscribe` | K线订阅 |
| `lastprice_subscribe` | 最新价订阅 |
| `market_subscribe` | 市场概览 |

### 私有频道
- 需通过 `GET /api/v4/ws/token` 获取 WebSocket token
- 支持: `balanceSpot_subscribe`, `ordersPending_subscribe`, `deals_subscribe`

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共 V4 | 1000 次/分钟 |
| 私有 V4 | 600 次/分钟 |
| WebSocket | 每连接最多 30 次订阅 |

## 特殊说明

- 欧洲合规交易所（立陶宛）
- V4 是推荐版本，V1/V2 逐步废弃
- 私有接口统一使用 POST 方法
- 交易对格式: `BTC_USDT` (大写下划线)
- 签名使用 HMAC SHA512（非 SHA256）
- WebSocket 使用 JSON-RPC 协议

## 相关资源

- [WhiteBIT API 文档](https://docs.whitebit.com)
- [WhiteBIT GitHub](https://github.com/whitebit-exchange/api-docs)
- [CCXT WhiteBIT 实现](https://github.com/ccxt/ccxt)
