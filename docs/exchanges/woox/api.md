# WOO X API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.woox.io>

## 交易所基本信息

- 官方名称: WOO X
- 官网: <https://woo.org>
- CMC 衍生品排名: #53
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual)
- 特色: 零手续费(部分交易对)，机构级深度
- 手续费: Maker 0.0%, Taker 0.02% (VIP费率)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (V1) | `https://api.woo.org` | V1 端点 |
| REST (V3) | `https://api.woo.org` | V3 端点(推荐) |
| WebSocket (公共) | `wss://wss.woo.org/ws/stream/{app_id}` | 公共行情 |
| WebSocket (私有) | `wss://wss.woo.org/ws/stream/{app_id}` | 私有(需认证) |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `x-api-key`: API Key
- `x-api-signature`: HMAC SHA256 签名
- `x-api-timestamp`: 毫秒时间戳

**签名字符串**: `timestamp + method + path + body(或sorted_params)`

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.woo.org"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    if params:
        sign_str = f"{urlencode(sorted(params.items()))}&timestamp={timestamp}"
    elif body:
        import json
        sign_str = f"{json.dumps(body)}{timestamp}"
    else:
        sign_str = f"timestamp={timestamp}"
    
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "x-api-key": API_KEY,
        "x-api-signature": signature,
        "x-api-timestamp": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=body, headers=headers).json()
```

## 市场数据 API

### 交易对格式

- 现货: `SPOT_{BASE}_{QUOTE}` (如 `SPOT_BTC_USDT`)
- 永续: `PERP_{BASE}_{QUOTE}` (如 `PERP_BTC_USDT`)

### 1. 获取交易对信息
- **端点**: `GET /v1/public/info`

### 2. 获取 Ticker
- **端点**: `GET /v1/public/market_trades`
- **参数**: `symbol` (必需)

### 3. 获取深度
- **端点**: `GET /v1/public/orderbook/{symbol}`
- **参数**: `max_level` (可选)

### 4. 获取 K 线
- **端点**: `GET /v1/public/kline`
- **参数**: `symbol`, `type` (1m,5m,15m,30m,1h,4h,12h,1d,1w,1mon), `limit`

### 5. 获取最近成交
- **端点**: `GET /v1/public/market_trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /v1/order`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| side | ENUM | 是 | BUY / SELL |
| order_type | ENUM | 是 | LIMIT / MARKET / IOC / FOK / POST_ONLY / ASK / BID |
| order_quantity | DECIMAL | 条件 | 数量 |
| order_price | DECIMAL | 条件 | 价格 |
| reduce_only | BOOL | 否 | 仅减仓 |

### 2. 撤单
- **端点**: `DELETE /v1/order`
- **参数**: `order_id` 或 `client_order_id`, `symbol`

### 3. 查询余额
- **端点**: `GET /v3/balances`

### 4. 查询持仓
- **端点**: `GET /v3/positions`

## WebSocket

### 订阅格式
```json
{"event": "subscribe", "topic": "SPOT_BTC_USDT@orderbook"}
```

### 频道

| 频道 | 格式 |
|------|------|
| 深度 | `{symbol}@orderbook` |
| 成交 | `{symbol}@trade` |
| Ticker | `{symbol}@ticker` |
| K线 | `{symbol}@kline_{interval}` |
| BBO | `{symbol}@bbo` |

### 心跳: 客户端每 10 秒发 `{"event": "ping"}`

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 10次/秒 |
| 下单 | 2次/秒 (V1), 10次/秒 (V3) |
| 撤单 | 2次/秒 (V1), 10次/秒 (V3) |

## 特殊说明

- WOO X 以低手续费闻名(部分交易对零手续费)
- 交易对格式: `SPOT_BTC_USDT` / `PERP_BTC_USDT`
- WebSocket 需要 Application ID
- V3 API 是更新版本，推荐使用

## 相关资源

- [WOO X API 文档](https://docs.woox.io)
- [WOO X 旧文档](https://kronosresearch.github.io/wootrade-documents/)
- [CCXT WOO 实现](https://github.com/ccxt/ccxt)
