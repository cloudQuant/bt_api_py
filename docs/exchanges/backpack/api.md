# Backpack Exchange API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.backpack.exchange>

## 交易所基本信息

- 官方名称: Backpack Exchange
- 官网: <https://backpack.exchange>
- CMC 衍生品排名: #46
- 交易所类型: CEX (中心化交易所)
- 特色: Solana 生态交易所，MPC 自托管钱包
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual)
- 手续费: Maker 0.08%, Taker 0.08% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.backpack.exchange` | 主端点 |
| WebSocket | `wss://ws.backpack.exchange` | 行情+私有 |

## 认证方式

### Ed25519 签名

Backpack 使用 Ed25519 签名（非传统 HMAC）。

**请求头**:
- `X-API-Key`: API Key
- `X-Signature`: Ed25519 签名
- `X-Timestamp`: 毫秒时间戳
- `X-Window`: 请求有效窗口(毫秒)

```python
import time
import requests
import base64
from nacl.signing import SigningKey

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key_base64"

BASE_URL = "https://api.backpack.exchange"

def signed_request(method, path, params=None):
    timestamp = int(time.time() * 1000)
    window = 5000
    
    # 构建签名字符串
    if params:
        from urllib.parse import urlencode
        query = urlencode(sorted(params.items()))
        sign_str = f"instruction={path}&{query}&timestamp={timestamp}&window={window}"
    else:
        sign_str = f"instruction={path}&timestamp={timestamp}&window={window}"
    
    # Ed25519 签名
    signing_key = SigningKey(base64.b64decode(SECRET_KEY))
    signed = signing_key.sign(sign_str.encode())
    signature = base64.b64encode(signed.signature).decode()
    
    headers = {
        "X-API-Key": API_KEY,
        "X-Signature": signature,
        "X-Timestamp": str(timestamp),
        "X-Window": str(window),
    }
    url = f"{BASE_URL}/api/v1/{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /api/v1/markets`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/ticker`
- **参数**: `symbol` (必需，如 `BTC_USDT`)

### 3. 获取深度
- **端点**: `GET /api/v1/depth`
- **参数**: `symbol` (必需)

### 4. 获取 K 线
- **端点**: `GET /api/v1/klines`
- **参数**: `symbol`, `interval` (1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M), `startTime`, `endTime`

### 5. 获取最近成交
- **端点**: `GET /api/v1/trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/order`
- **参数**: `symbol`, `side` (Bid/Ask), `orderType` (Limit/Market), `quantity`, `price`, `timeInForce`

### 2. 撤单
- **端点**: `DELETE /api/v1/order`
- **参数**: `orderId`, `symbol`

### 3. 查询余额
- **端点**: `GET /api/v1/capital`

### 4. 查询持仓
- **端点**: `GET /api/v1/positions`

### 5. 获取成交记录
- **端点**: `GET /api/v1/fills`
- **参数**: `symbol`, `orderId`

## WebSocket

### 订阅格式
```json
{"method": "SUBSCRIBE", "params": ["trades.BTC_USDT"]}
```

### 频道
| 频道 | 说明 |
|------|------|
| `trades.{symbol}` | 实时成交 |
| `depth.{symbol}` | 深度数据 |
| `kline.{symbol}.{interval}` | K线 |
| `ticker.{symbol}` | Ticker |

## 特殊说明

- 使用 Ed25519 签名（非 HMAC SHA256）
- Solana 生态背景，支持 SPL Token
- 买卖方向使用 `Bid`/`Ask`（非 BUY/SELL）
- 交易对格式: `BTC_USDT` (大写下划线)
- 支持 70+ REST API 端点

## 相关资源

- [Backpack Exchange API](https://docs.backpack.exchange)
- [Backpack Python SDK](https://github.com/solomeowl/backpack_exchange_sdk)
