# Pionex API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://pionex-doc.gitbook.io/apidocs/>

## 交易所基本信息

- 官方名称: Pionex
- 官网: <https://www.pionex.com>
- CMC 衍生品排名: #20
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual)、内置交易机器人
- 手续费: Maker 0.05%, Taker 0.05% (现货)
- 特色: 内置 16+ 种交易机器人(Grid Bot, DCA Bot 等)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.pionex.com` | 主端点 |
| WebSocket | `wss://ws.pionex.com/wsPub` | 公共行情 |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `PIONEX-KEY`: API Key
- `PIONEX-SIGNATURE`: 签名

**签名步骤**:
1. GET: `METHOD + PATH_URL + sorted_query_string + timestamp`
2. POST: `METHOD + PATH_URL + request_body + timestamp`
3. HMAC SHA256 签名

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.pionex.com"

def signed_request(method, path, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    if method == "GET" and params:
        query = urlencode(sorted(params.items()))
        sign_str = f"GET{path}{query}{timestamp}"
    elif method == "POST" and body:
        import json
        sign_str = f"POST{path}{json.dumps(body)}{timestamp}"
    else:
        sign_str = f"{method}{path}{timestamp}"
    
    signature = hmac.new(
        SECRET_KEY.encode(), sign_str.encode(), sha256
    ).hexdigest()
    
    headers = {
        "PIONEX-KEY": API_KEY,
        "PIONEX-SIGNATURE": signature,
        "timestamp": timestamp,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    else:
        return requests.post(url, json=body, headers=headers).json()
```

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /api/v1/common/symbols`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/market/tickers`
- **参数**: 无(返回全部)

### 3. 获取深度
- **端点**: `GET /api/v1/market/depth`
- **参数**: `symbol` (必需), `limit` (可选)

### 4. 获取 K 线
- **端点**: `GET /api/v1/market/klines`
- **参数**: `symbol`, `interval` (1m,5m,15m,30m,1h,4h,1d), `limit`

### 5. 获取最近成交
- **端点**: `GET /api/v1/market/trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/trade/order`
- **参数**: `symbol`, `side` (BUY/SELL), `type` (LIMIT/MARKET), `size`, `price`

### 2. 撤单
- **端点**: `DELETE /api/v1/trade/order`
- **参数**: `symbol`, `orderId`

### 3. 查询余额
- **端点**: `GET /api/v1/account/balances`

### 4. 查询订单
- **端点**: `GET /api/v1/trade/order`
- **参数**: `symbol`, `orderId`

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 20次/秒 |
| 私有接口 | 10次/秒 |

## 特殊说明

- Pionex 以内置交易机器人闻名
- API 请求时间戳有效期 20000ms
- 交易对格式: `BTC_USDT` (大写下划线分隔)
- 所有私有请求需要 timestamp 参数

## 相关资源

- [Pionex API 文档](https://pionex-doc.gitbook.io/apidocs/)
- [Pionex 官网](https://www.pionex.com)
