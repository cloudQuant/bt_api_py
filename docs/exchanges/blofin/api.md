# BloFin API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.blofin.com>

## 交易所基本信息

- 官方名称: BloFin
- 官网: <https://blofin.com>
- CMC 衍生品排名: #50
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(Perpetual Futures)、跟单交易(Copy Trading)
- 手续费: Maker 0.02%, Taker 0.06% (合约)
- 最大杠杆: 150x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://openapi.blofin.com` | 主端点 |
| WebSocket (公共) | `wss://openapi.blofin.com/ws/public` | 公共行情 |
| WebSocket (私有) | `wss://openapi.blofin.com/ws/private` | 私有流 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `ACCESS-KEY`: API Key
- `ACCESS-SIGN`: Base64(HMAC SHA256 签名)
- `ACCESS-TIMESTAMP`: ISO 格式时间戳
- `ACCESS-PASSPHRASE`: API Passphrase

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
BASE_URL = "https://openapi.blofin.com"

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
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": PASSPHRASE,
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
- **端点**: `GET /api/v1/market/instruments`
- **参数**: `instType` (可选, SWAP)

### 2. 获取 Ticker
- **端点**: `GET /api/v1/market/tickers`
- **参数**: `instType` (SWAP)

### 3. 获取深度
- **端点**: `GET /api/v1/market/books`
- **参数**: `instId` (必需，如 `BTC-USDT`), `size` (可选, 1-400)

### 4. 获取 K 线
- **端点**: `GET /api/v1/market/candles`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instId | STRING | 是 | 合约ID |
| bar | STRING | 否 | 1m,3m,5m,15m,30m,1H,2H,4H,6H,12H,1D,1W,1M |
| after | STRING | 否 | 起始时间戳 |
| before | STRING | 否 | 结束时间戳 |
| limit | INT | 否 | 默认 100，最大 300 |

### 5. 获取最近成交
- **端点**: `GET /api/v1/market/trades`
- **参数**: `instId` (必需), `limit` (可选)

### 6. 获取资金费率
- **端点**: `GET /api/v1/market/funding-rate`
- **参数**: `instId` (必需)

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/trade/order`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instId | STRING | 是 | 合约ID |
| marginMode | ENUM | 是 | cross / isolated |
| positionSide | ENUM | 是 | net / long / short |
| side | ENUM | 是 | buy / sell |
| orderType | ENUM | 是 | market / limit / post_only / fok / ioc |
| price | DECIMAL | 条件 | 限价 |
| size | DECIMAL | 是 | 数量 |
| reduceOnly | BOOL | 否 | 仅减仓 |

### 2. 撤单
- **端点**: `POST /api/v1/trade/cancel-order`
- **参数**: `instId`, `orderId`

### 3. 批量下单
- **端点**: `POST /api/v1/trade/batch-orders`

### 4. 查询持仓
- **端点**: `GET /api/v1/account/positions`

### 5. 查询余额
- **端点**: `GET /api/v1/account/balance`

### 6. 设置杠杆
- **端点**: `POST /api/v1/account/set-leverage`
- **参数**: `instId`, `leverage`, `marginMode`

## WebSocket

### 订阅格式
```json
{
  "op": "subscribe",
  "args": [{"channel": "books", "instId": "BTC-USDT"}]
}
```

### 公共频道

| 频道 | 说明 |
|------|------|
| `tickers` | Ticker推送 |
| `books` | 深度数据 |
| `books5` | 5档深度 |
| `trades` | 成交推送 |
| `candle{period}` | K线推送 |
| `funding-rate` | 资金费率 |
| `mark-price` | 标记价格 |

### 私有频道认证
```json
{
  "op": "login",
  "args": [{"apiKey": "xxx", "passphrase": "xxx", "timestamp": "xxx", "sign": "xxx"}]
}
```

### 私有频道: `orders`, `positions`, `account`

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 20次/2秒 |
| 下单 | 60次/2秒 |
| 撤单 | 60次/2秒 |
| 查询 | 20次/2秒 |

## 特殊说明

- API 风格类似 OKX
- 合约ID格式: `BTC-USDT` (大写横线)
- 签名使用 Base64(HMAC SHA256)
- 需要 Passphrase（创建 API Key 时设置）
- 仅支持合约交易，无现货
- API Key 未绑定 IP 90天后过期

## 相关资源

- [BloFin API 文档](https://docs.blofin.com)
- [BloFin 官网](https://blofin.com)
