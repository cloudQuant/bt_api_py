# Delta Exchange API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.delta.exchange>
- GitHub: <https://github.com/delta-exchange/python-rest-client>

## 交易所基本信息

- 官方名称: Delta Exchange
- 官网: <https://www.delta.exchange>
- CMC 衍生品排名: #58
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(Perpetual)、期权(Options)、期货(Futures)、利率互换
- 手续费: Maker 0.02%, Taker 0.05% (期货)
- 最大杠杆: 100x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (全球) | `https://api.delta.exchange/v2` | 全球端点 |
| REST (印度) | `https://api.india.delta.exchange/v2` | 印度端点 |
| REST (测试) | `https://cdn-testnet.delta.exchange/v2` | 测试网 |
| WebSocket (全球) | `wss://socket.delta.exchange` | WebSocket |
| WebSocket (印度) | `wss://socket.india.delta.exchange` | 印度WS |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `api-key`: API Key
- `signature`: HMAC SHA256 签名
- `timestamp`: 秒级时间戳

**签名字符串**: `method + timestamp + path + query_string + body`

```python
import hmac
import time
import json
import requests
from hashlib import sha256

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.delta.exchange/v2"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time()))
    query = ""
    if params:
        from urllib.parse import urlencode
        query = "?" + urlencode(params)
    body_str = json.dumps(body) if body else ""
    sign_str = f"{method}{timestamp}{'/v2' + path}{query}{body_str}"
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    headers = {
        "api-key": API_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}{query}"
    if method == "GET":
        return requests.get(url, headers=headers).json()
    else:
        return requests.post(url, data=body_str, headers=headers).json()
```

## 市场数据 API

### 1. 获取产品列表
- **端点**: `GET /v2/products`
- **参数**: `contract_types` (可选: perpetual_futures, call_options, put_options, futures)

### 2. 获取 Ticker
- **端点**: `GET /v2/tickers`
- **参数**: `contract_types` (可选)

### 3. 获取深度
- **端点**: `GET /v2/l2orderbook/{symbol}`
- **参数**: `depth` (可选)

### 4. 获取 K 线
- **端点**: `GET /v2/history/candles`
- **参数**: `resolution` (1m,3m,5m,15m,30m,1h,2h,4h,6h,1d,7d,30d,1w,2w), `symbol`, `start`, `end`

### 5. 获取最近成交
- **端点**: `GET /v2/trades/{symbol}`
- **参数**: 无

### 6. 获取资金费率
- **端点**: `GET /v2/funding_rates`

## 交易 API

### 1. 下单
- **端点**: `POST /v2/orders`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | INT | 是 | 产品ID |
| size | INT | 是 | 合约数量 |
| side | ENUM | 是 | buy / sell |
| order_type | ENUM | 是 | limit_order / market_order |
| limit_price | DECIMAL | 条件 | 限价 |
| post_only | BOOL | 否 | 仅Maker |
| reduce_only | BOOL | 否 | 仅减仓 |
| time_in_force | ENUM | 否 | gtc / ioc / fok |

### 2. 撤单
- **端点**: `DELETE /v2/orders/{order_id}`

### 3. 批量撤单
- **端点**: `DELETE /v2/orders/all`
- **参数**: `product_id` (可选)

### 4. 查询持仓
- **端点**: `GET /v2/positions`

### 5. 查询钱包
- **端点**: `GET /v2/wallet/balances`

### 6. 设置杠杆
- **端点**: `PUT /v2/orders/leverage`

## WebSocket

### 订阅格式
```json
{
  "type": "subscribe",
  "payload": {
    "channels": [
      {"name": "l2_orderbook", "symbols": ["BTCUSDT"]},
      {"name": "all_trades", "symbols": ["BTCUSDT"]}
    ]
  }
}
```

### 频道

| 频道 | 说明 |
|------|------|
| `l2_orderbook` | 深度数据 |
| `all_trades` | 实时成交 |
| `candlestick_{resolution}` | K线 |
| `mark_price` | 标记价格 |
| `funding_rate` | 资金费率 |
| `orders` | 用户订单(需认证) |
| `positions` | 用户持仓(需认证) |

### 心跳: 每 30 秒发 `{"type": "ping"}`

## 速率限制

| 类别 | 限制 |
|------|------|
| 下单 | 50次/秒 |
| 查询 | 50次/秒 |
| 公共接口 | 100次/秒 |

## 特殊说明

- 同时支持期货和期权交易
- 使用 product_id (整数) 而非字符串标识产品
- 提供印度专用端点
- 提供测试网环境
- Python SDK: `delta-rest-client`

## 相关资源

- [Delta Exchange API 文档](https://docs.delta.exchange)
- [Delta Exchange Python SDK](https://github.com/delta-exchange/python-rest-client)
- [CCXT Delta 实现](https://github.com/ccxt/ccxt)
