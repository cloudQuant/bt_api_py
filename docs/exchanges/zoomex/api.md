# Zoomex API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: Bybit V5 兼容 API

## 交易所基本信息

- 官方名称: Zoomex
- 官网: <https://www.zoomex.com>
- CMC 衍生品排名: #27
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(USDT-M / Inverse Perpetual)、跟单交易
- 最大杠杆: 150x
- 特色: 无 KYC 要求

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.zoomex.com` | 主端点 |
| WebSocket (公共) | `wss://stream.zoomex.com/v5/public/linear` | USDT永续公共 |
| WebSocket (私有) | `wss://stream.zoomex.com/v5/private` | 私有流 |

## 认证方式

### HMAC SHA256 签名 (兼容 Bybit V5)

**请求头**:
- `X-BAPI-API-KEY`: API Key
- `X-BAPI-SIGN`: HMAC SHA256 签名
- `X-BAPI-TIMESTAMP`: 毫秒时间戳
- `X-BAPI-RECV-WINDOW`: 接收窗口(毫秒)

**签名步骤**:
1. 签名字符串 = `timestamp + api_key + recv_window + query_string(或body)`
2. HMAC SHA256 签名

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.zoomex.com"
RECV_WINDOW = "5000"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    if method == "GET" and params:
        query = urlencode(params)
        sign_str = f"{timestamp}{API_KEY}{RECV_WINDOW}{query}"
    elif body:
        import json
        sign_str = f"{timestamp}{API_KEY}{RECV_WINDOW}{json.dumps(body)}"
    else:
        sign_str = f"{timestamp}{API_KEY}{RECV_WINDOW}"
    
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        import json
        return requests.post(url, data=json.dumps(body), headers=headers).json()
```

## 市场数据 API (V5 兼容)

### 1. 获取合约列表
- **端点**: `GET /v5/market/instruments-info`
- **参数**: `category` (linear/inverse)

### 2. 获取 Ticker
- **端点**: `GET /v5/market/tickers`
- **参数**: `category`, `symbol` (可选)

### 3. 获取深度
- **端点**: `GET /v5/market/orderbook`
- **参数**: `category`, `symbol`, `limit` (可选, 1-200)

### 4. 获取 K 线
- **端点**: `GET /v5/market/kline`
- **参数**: `category`, `symbol`, `interval` (1,3,5,15,30,60,120,240,360,720,D,W,M), `limit`

### 5. 获取最近成交
- **端点**: `GET /v5/market/recent-trade`
- **参数**: `category`, `symbol`, `limit`

### 6. 获取资金费率
- **端点**: `GET /v5/market/funding/history`
- **参数**: `category`, `symbol`

## 交易 API

### 1. 下单
- **端点**: `POST /v5/order/create`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| category | STRING | 是 | linear / inverse |
| symbol | STRING | 是 | 合约名称 |
| side | ENUM | 是 | Buy / Sell |
| orderType | ENUM | 是 | Market / Limit |
| qty | DECIMAL | 是 | 数量 |
| price | DECIMAL | 条件 | 限价 |
| positionIdx | INT | 否 | 0=单向, 1=买方双向, 2=卖方双向 |
| reduceOnly | BOOL | 否 | 仅减仓 |
| timeInForce | ENUM | 否 | GTC / IOC / FOK / PostOnly |

### 2. 撤单
- **端点**: `POST /v5/order/cancel`
- **参数**: `category`, `symbol`, `orderId`

### 3. 查询持仓
- **端点**: `GET /v5/position/list`
- **参数**: `category`, `symbol`

### 4. 查询钱包余额
- **端点**: `GET /v5/account/wallet-balance`
- **参数**: `accountType` (UNIFIED/CONTRACT)

### 5. 设置杠杆
- **端点**: `POST /v5/position/set-leverage`
- **参数**: `category`, `symbol`, `buyLeverage`, `sellLeverage`

## WebSocket

### 订阅格式 (V5)
```json
{"op": "subscribe", "args": ["orderbook.50.BTCUSDT", "tickers.BTCUSDT"]}
```

### 频道

| 频道 | 格式 | 说明 |
|------|------|------|
| 深度 | `orderbook.{depth}.{symbol}` | depth: 1,50,200,500 |
| 成交 | `publicTrade.{symbol}` | 实时成交 |
| Ticker | `tickers.{symbol}` | Ticker |
| K线 | `kline.{interval}.{symbol}` | K线 |
| 清算 | `liquidation.{symbol}` | 强平推送 |

### 私有频道: `order`, `position`, `wallet`
### 心跳: 每 20 秒发 `{"op": "ping"}`

## 速率限制

| 类别 | 限制 |
|------|------|
| 下单 | 10次/秒 |
| 撤单 | 10次/秒 |
| 查询 | 50次/秒(GET) |

## 特殊说明

- Zoomex API 完全兼容 Bybit V5 API
- 如已实现 Bybit V5，可复用大部分代码，仅需更换 base URL
- 无 KYC 要求
- 支持 USDT-M 和 Inverse 永续合约
- 交易对格式: `BTCUSDT` (大写无分隔符)

## 相关资源

- [Zoomex 官网](https://www.zoomex.com)
- [Bybit V5 API 文档](https://bybit-exchange.github.io/docs/v5/intro) (兼容)
