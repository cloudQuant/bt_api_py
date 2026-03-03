# BTSE API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://btsecom.github.io/docs/futures/en/>
- GitHub: <https://github.com/btsecom/api-sample>

## 交易所基本信息

- 官方名称: BTSE
- 官网: <https://www.btse.com>
- CMC 衍生品排名: #48
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Futures)、OTC
- 手续费: Maker 0.10%, Taker 0.10% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://api.btse.com/spot` | 现货 |
| REST (合约) | `https://api.btse.com/futures` | 合约 |
| WebSocket (现货) | `wss://ws.btse.com/ws/spot` | 现货行情 |
| WebSocket (合约) | `wss://ws.btse.com/ws/futures` | 合约行情 |
| WebSocket (OTC) | `wss://ws.btse.com/ws/otc` | OTC |

## 认证方式

### HMAC SHA384 签名

**请求头**:
- `btse-api`: API Key
- `btse-sign`: HMAC SHA384 签名
- `btse-nonce`: 毫秒时间戳

**签名字符串**: `path + nonce + body`

```python
import hmac
import time
import json
import requests
from hashlib import sha384

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.btse.com"

def signed_request(method, path, body=None):
    nonce = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""
    sign_str = f"{path}{nonce}{body_str}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha384
    ).hexdigest()
    headers = {
        "btse-api": API_KEY,
        "btse-sign": signature,
        "btse-nonce": nonce,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, headers=headers).json()
    else:
        return requests.post(url, data=body_str, headers=headers).json()
```

## 市场数据 API

### 1. 获取市场摘要
- **端点**: `GET /spot/api/v3.2/market_summary`

### 2. 获取深度
- **端点**: `GET /spot/api/v3.2/orderbook/L2`
- **参数**: `symbol`, `depth` (可选)

### 3. 获取 K 线
- **端点**: `GET /spot/ohlcv`
- **参数**: `symbol`, `resolution` (1,5,15,30,60,120,240,360,720,1440), `start`, `end`

### 4. 获取成交
- **端点**: `GET /spot/api/v3.2/trades`
- **参数**: `symbol`, `count`

## 交易 API

### 1. 下单
- **端点**: `POST /spot/api/v3.2/order`
- **参数**: `symbol`, `side` (BUY/SELL), `type` (LIMIT/MARKET), `size`, `price`, `time_in_force`

### 2. 撤单
- **端点**: `DELETE /spot/api/v3.2/order`
- **参数**: `symbol`, `orderID` 或 `clOrderID`

### 3. 查询钱包
- **端点**: `GET /spot/api/v3.2/user/wallet`

## 合约 API

### 1. 合约下单
- **端点**: `POST /futures/api/v2.1/order`
- **参数**: `symbol`, `side`, `type`, `size`, `price`

### 2. 合约持仓
- **端点**: `GET /futures/api/v2.1/user/positions`

## WebSocket

### 订阅格式
```json
{"op": "subscribe", "args": ["orderBookApi:BTCPFC_0"]}
```

### 频道: `orderBookApi`, `tradeHistory`, `kline`
### 心跳: 每15秒自动发送

## 特殊说明

- 使用 HMAC SHA384 签名（非 SHA256）
- 支持 FIX 协议（机构客户）
- 合约代码: `BTCPFC` (BTC Perpetual Futures Contract)
- 现货和合约使用不同的 base URL 路径

## 相关资源

- [BTSE 现货 API](https://btsecom.github.io/docs/spot/en/)
- [BTSE 合约 API](https://btsecom.github.io/docs/futures/en/)
- [BTSE GitHub](https://github.com/btsecom/api-sample)
