# LBank API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://www.lbank.com/docs/>
- GitHub: <https://github.com/LBank-exchange/lbank-official-api-docs>

## 交易所基本信息

- 官方名称: LBank
- 官网: <https://www.lbank.com>
- CMC 衍生品排名: #17
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual)
- 支持的交易对: 600+
- 手续费: Maker 0.1%, Taker 0.1% (现货)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货 V2) | `https://api.lbkex.com` | 现货 V2 主端点 |
| REST (合约) | `https://lbkperp.lbank.com` | 合约端点 |
| WebSocket (现货) | `wss://www.lbkex.net/ws/V2/` | 现货行情 |
| WebSocket (合约) | `wss://lbkperp.lbank.com/ws` | 合约行情 |

## 认证方式

### RSA / HMAC-MD5 签名

**签名步骤** (V2):
1. 将参数按字典排序拼接成查询字符串
2. 使用 HMAC-MD5 或 RSA 签名
3. 将签名转为大写十六进制字符串

**请求头**:
- `Content-Type`: `application/x-www-form-urlencoded`

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://api.lbkex.com"

def sign(params):
    sorted_params = urlencode(sorted(params.items()))
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        sorted_params.encode('utf-8'),
        hashlib.md5
    ).hexdigest().upper()
    return signature

def signed_request(method, path, params=None):
    if params is None:
        params = {}
    params["api_key"] = API_KEY
    params["sign"] = sign(params)
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params).json()
    else:
        return requests.post(url, data=params).json()

def public_request(path, params=None):
    url = f"{BASE_URL}{path}"
    return requests.get(url, params=params or {}).json()
```

## 市场数据 API (现货 V2)

### 1. 获取交易对列表

- **端点**: `GET /v2/accuracy.do`
- **描述**: 获取所有交易对精度信息

### 2. 获取 Ticker

- **端点**: `GET /v2/ticker/24hr.do`
- **参数**: `symbol` (必需，如 `btc_usdt`)

### 3. 获取深度

- **端点**: `GET /v2/depth.do`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对，如 `btc_usdt` |
| size | INT | 否 | 深度档位，默认 60 |

### 4. 获取 K 线

- **端点**: `GET /v2/kline.do`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| type | STRING | 是 | minute1,minute5,minute15,minute30,hour1,hour4,hour8,day1,week1,month1 |
| size | INT | 否 | 数量，默认 100 |
| time | STRING | 否 | 起始时间 |

### 5. 获取最近成交

- **端点**: `GET /v2/trades.do`
- **参数**: `symbol` (必需), `size` (可选，默认 100)

## 交易 API

### 1. 查询账户余额

- **端点**: `POST /v2/user_info.do`

### 2. 下单

- **端点**: `POST /v2/create_order.do`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| type | ENUM | 是 | buy / sell |
| price | DECIMAL | 条件 | 价格(限价) |
| amount | DECIMAL | 是 | 数量 |

### 3. 撤单

- **端点**: `POST /v2/cancel_order.do`
- **参数**: `symbol`, `order_id`

### 4. 查询订单

- **端点**: `POST /v2/orders_info.do`
- **参数**: `symbol`, `order_id`

## WebSocket

### 订阅格式

```json
{
  "action": "subscribe",
  "subscribe": "depth",
  "depth": "btc_usdt",
  "length": 25
}
```

### 频道类型

| 频道 | 说明 |
|------|------|
| depth | 深度数据 |
| trade | 实时成交 |
| kbar | K线数据 |
| tick | 24h行情 |

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 10次/秒 |
| 私有接口 | 10次/秒 |

## 特殊说明

- 交易对格式: `{base}_{quote}` 小写，如 `btc_usdt`
- V2 API 是当前推荐版本
- 签名方式支持 HMAC-MD5 和 RSA
- 合约和现货使用不同的 base URL

## 相关资源

- [LBank 官方 API 文档](https://www.lbank.com/docs/)
- [LBank GitHub](https://github.com/LBank-exchange/lbank-official-api-docs)
- [CCXT LBank 实现](https://github.com/ccxt/ccxt)
