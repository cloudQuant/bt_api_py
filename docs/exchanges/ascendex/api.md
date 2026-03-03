# AscendEX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://ascendex.github.io/ascendex-pro-api/>

## 交易所基本信息

- 官方名称: AscendEX (原 BitMax)
- 官网: <https://ascendex.com>
- CMC 衍生品排名: #43
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、保证金(Margin)、永续合约(Perpetual Futures)
- 手续费: Maker 0.1%, Taker 0.1% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://ascendex.com/{group}/api/pro/v1` | 现货, group=账户组 |
| REST (合约) | `https://ascendex.com/{group}/api/pro/v2/futures` | 期货 V2 |
| WebSocket | `wss://ascendex.com/{group}/api/pro/v1/stream` | 行情+私有 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `x-auth-key`: API Key
- `x-auth-signature`: HMAC SHA256 签名
- `x-auth-timestamp`: 毫秒时间戳

**签名字符串**: `timestamp + path`

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
ACCOUNT_GROUP = 0  # 从 /api/pro/v1/info 获取
BASE_URL = f"https://ascendex.com/{ACCOUNT_GROUP}/api/pro/v1"

def signed_request(method, path, params=None):
    timestamp = str(int(time.time() * 1000))
    sign_str = f"{timestamp}+{path}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "x-auth-key": API_KEY,
        "x-auth-signature": signature,
        "x-auth-timestamp": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=params, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /api/pro/v1/products`

### 2. 获取 Ticker
- **端点**: `GET /api/pro/v1/ticker`
- **参数**: `symbol` (可选，如 `BTC/USDT`)

### 3. 获取深度
- **端点**: `GET /api/pro/v1/depth`
- **参数**: `symbol` (必需)

### 4. 获取 K 线
- **端点**: `GET /api/pro/v1/barhist`
- **参数**: `symbol`, `interval` (1,5,15,30,60,120,240,360,720,1d,1w,1M), `from`, `to`, `n`

### 5. 获取最近成交
- **端点**: `GET /api/pro/v1/trades`
- **参数**: `symbol` (必需), `n` (数量)

## 交易 API

### 1. 下单
- **端点**: `POST /api/pro/v1/cash/order`
- **参数**: `symbol`, `side` (buy/sell), `orderType` (limit/market), `orderQty`, `orderPrice`, `timeInForce`

### 2. 撤单
- **端点**: `DELETE /api/pro/v1/cash/order`
- **参数**: `orderId` 或 `id`(客户自定义)

### 3. 查询余额
- **端点**: `GET /api/pro/v1/cash/balance`

## 合约 API (V2)

### 1. 合约行情
- **端点**: `GET /api/pro/v2/futures/ticker`

### 2. 合约下单
- **端点**: `POST /api/pro/v2/futures/order`
- **参数**: `symbol`, `side`, `orderType`, `orderQty`, `orderPrice`, `leverage`

### 3. 合约持仓
- **端点**: `GET /api/pro/v2/futures/position`

## WebSocket

### 订阅格式
```json
{"op": "sub", "ch": "depth:BTC/USDT"}
```

### 频道: `depth:{symbol}`, `trades:{symbol}`, `bar:{interval}:{symbol}`, `bbo:{symbol}`
### 私有: `order:{symbol}`, `futures-position`
### 心跳: 服务器发 `ping`，客户端回 `pong`

## 特殊说明

- 需要先获取 account group（通过 `/api/pro/v1/info`）
- 交易对格式: `BTC/USDT` (大写斜杠分隔)
- 现货和合约使用不同的 API 路径前缀
- 支持现货保证金交易

## 相关资源

- [AscendEX Pro API](https://ascendex.github.io/ascendex-pro-api/)
- [AscendEX Futures API V2](https://ascendex.github.io/ascendex-futures-pro-api-v2/)
- [CCXT AscendEX 实现](https://github.com/ccxt/ccxt)
