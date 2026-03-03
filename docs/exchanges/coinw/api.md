# CoinW API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://www.coinw.com/api-doc/>

## 交易所基本信息

- 官方名称: CoinW
- 官网: <https://www.coinw.com>
- CMC 衍生品排名: #21
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Swap)
- 手续费: Maker 0.1%, Taker 0.1% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://api.coinw.com` | 现货主端点 |
| REST (合约) | `https://contract.coinw.com` | 合约端点 |
| WebSocket (现货) | `wss://ws.coinw.com/websocket` | 现货行情 |
| WebSocket (合约) | `wss://contract.coinw.com/websocket` | 合约行情 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `X-API-KEY`: API Key
- `X-SIGNATURE`: 签名
- `X-TIMESTAMP`: 时间戳

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.coinw.com"

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
        "X-API-KEY": API_KEY,
        "X-SIGNATURE": signature,
        "X-TIMESTAMP": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取 Ticker
- **端点**: `GET /api/v1/public?command=returnTicker`

### 2. 获取深度
- **端点**: `GET /api/v1/public?command=returnOrderBook`
- **参数**: `symbol`, `size` (可选)

### 3. 获取 K 线
- **端点**: `GET /api/v1/public?command=returnKline`
- **参数**: `symbol`, `type` (1min,5min,15min,30min,1hour,4hour,1day), `size`

### 4. 获取最近成交
- **端点**: `GET /api/v1/public?command=returnTradeHistory`
- **参数**: `symbol`, `size`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/private?command=submitOrder`
- **参数**: `symbol`, `type` (1=买/2=卖), `price`, `amount`

### 2. 撤单
- **端点**: `POST /api/v1/private?command=cancelOrder`
- **参数**: `order_id`

### 3. 查询余额
- **端点**: `POST /api/v1/private?command=returnBalances`

## WebSocket

### 订阅: 基于 Socket.IO
```json
{"event": "subscribe", "channel": "depth_BTC_USDT"}
```

### 频道: `depth_{symbol}`, `trade_{symbol}`, `kline_{symbol}_{period}`

## 特殊说明

- CoinW REST API 使用 `command` 参数区分端点
- WebSocket 基于 Socket.IO 协议
- 合约和现货使用不同的域名
- 交易对格式: `BTC_USDT` (大写下划线)

## 相关资源

- [CoinW API 文档](https://www.coinw.com/api-doc/)
- [CoinW 官网](https://www.coinw.com)
