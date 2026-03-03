# DigiFinex API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.digifinex.com>
- GitHub: <https://github.com/DigiFinex/api>

## 交易所基本信息

- 官方名称: DigiFinex
- 官网: <https://www.digifinex.com>
- CMC 衍生品排名: #23
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Swap)
- 手续费: Maker 0.2%, Taker 0.2% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货 V3) | `https://openapi.digifinex.com/v3` | 现货 V3 端点 |
| REST (合约 V2) | `https://openapi.digifinex.com/swap/v2` | 合约 V2 端点 |
| WebSocket | `wss://openapi.digifinex.com/ws/v1/` | 行情 WebSocket |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `ACCESS-KEY`: API Key
- `ACCESS-SIGN`: HMAC SHA256 签名
- `ACCESS-TIMESTAMP`: 时间戳(秒)

**签名步骤**:
1. 签名字符串 = `timestamp + method + path + body`
2. 使用 Secret Key 进行 HMAC SHA256 签名

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://openapi.digifinex.com/v3"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time()))
    if method == "GET" and params:
        from urllib.parse import urlencode
        sign_str = f"{timestamp}{method}{path}?{urlencode(params)}"
    elif body:
        import json
        sign_str = f"{timestamp}{method}{path}{json.dumps(body)}"
    else:
        sign_str = f"{timestamp}{method}{path}"
    
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=body, headers=headers).json()
```

## 市场数据 API (现货 V3)

### 1. 获取交易对信息
- **端点**: `GET /v3/markets`

### 2. 获取 Ticker
- **端点**: `GET /v3/ticker`
- **参数**: `symbol` (可选，如 `btc_usdt`)

### 3. 获取深度
- **端点**: `GET /v3/order_book`
- **参数**: `symbol` (必需), `limit` (可选，5/10/20/50/150)

### 4. 获取 K 线
- **端点**: `GET /v3/kline`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| period | INT | 是 | 周期(分钟): 1,5,15,30,60,240,720,1440,10080,43200 |
| start_time | INT | 否 | 起始时间戳(秒) |
| end_time | INT | 否 | 结束时间戳(秒) |
| limit | INT | 否 | 数量，默认 500 |

### 5. 获取最近成交
- **端点**: `GET /v3/trades`
- **参数**: `symbol` (必需), `limit` (可选)

## 交易 API

### 1. 下单
- **端点**: `POST /v3/spot/order/new`
- **参数**: `symbol`, `type` (buy/sell), `amount`, `price`, `market_type` (可选)

### 2. 撤单
- **端点**: `POST /v3/spot/order/cancel`
- **参数**: `order_id`

### 3. 查询余额
- **端点**: `GET /v3/spot/assets`

### 4. 查询订单
- **端点**: `GET /v3/spot/order/current`
- **参数**: `symbol`, `order_id`

## 合约 API (Swap V2)

### Base URL: `https://openapi.digifinex.com/swap/v2`

### 1. 合约行情
- **端点**: `GET /swap/v2/public/ticker`

### 2. 合约下单
- **端点**: `POST /swap/v2/trade/order_place`
- **参数**: `instrument_id`, `side`, `type`, `size`, `price`

## WebSocket

### 订阅格式
```json
{"method": "depth.subscribe", "params": ["BTC_USDT", 20], "id": 1}
```

### 频道: `depth`, `trades`, `kline`, `ticker`
### 心跳: 客户端需定期发送 `ping`

## 速率限制

| 类别 | 限制 |
|------|------|
| IP 限制 | 每分钟按权重计算 |
| 账户限制 | 每分钟按权重计算 |
| 不同端点权重不同 | 详见官方文档 |

## 特殊说明

- 交易对格式: `btc_usdt` (小写下划线)
- V3 是现货推荐版本
- 合约使用 Swap V2 API
- 时间戳单位: 秒（非毫秒）

## 相关资源

- [DigiFinex API 文档](https://docs.digifinex.com)
- [DigiFinex GitHub](https://github.com/DigiFinex/api)
- [CCXT DigiFinex 实现](https://github.com/ccxt/ccxt)
