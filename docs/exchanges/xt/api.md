# XT.COM API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://doc.xt.com>
- GitHub: <https://github.com/XtApis/xt-api>

## 交易所基本信息

- 官方名称: XT.COM
- 官网: <https://www.xt.com>
- CMC 衍生品排名: #8
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(USDT-M / Coin-M)
- 支持的交易对: 800+
- 手续费: Maker 0.1%, Taker 0.1% (现货); Maker 0.02%, Taker 0.05% (合约)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (现货) | `https://sapi.xt.com` | 现货主端点 |
| REST (合约) | `https://fapi.xt.com` | 合约主端点 |
| WebSocket (现货) | `wss://stream.xt.com/public` | 现货公共行情 |
| WebSocket (合约) | `wss://fstream.xt.com/ws/market` | 合约行情 |
| WebSocket (私有) | `wss://fstream.xt.com/ws/user` | 用户私有流 |

## 认证方式

### 签名方式: HMAC SHA256

**请求头**:
- `xt-validate-appkey`: API Key
- `xt-validate-timestamp`: 毫秒级时间戳
- `xt-validate-signature`: HMAC SHA256 签名
- `xt-validate-recvwindow`: 请求有效时间窗口(可选)

**签名步骤**:
1. 拼接签名字符串: `method + "#" + path + "#" + sortedParams + "#" + timestamp`
2. 使用 Secret Key 进行 HMAC SHA256 签名
3. 将签名转为十六进制字符串

```python
import hmac
import time
import requests
from hashlib import sha256
from urllib.parse import urlencode

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
BASE_URL = "https://sapi.xt.com"

def generate_signature(method, path, params, timestamp):
    sorted_params = urlencode(sorted(params.items())) if params else ""
    sign_str = f"{method}#{path}#{sorted_params}#{timestamp}"
    return hmac.new(
        SECRET_KEY.encode('utf-8'),
        sign_str.encode('utf-8'),
        sha256
    ).hexdigest()

def signed_request(method, path, params=None):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    signature = generate_signature(method, path, params, timestamp)
    headers = {
        "xt-validate-appkey": API_KEY,
        "xt-validate-timestamp": timestamp,
        "xt-validate-signature": signature,
    }
    url = f"{BASE_URL}{path}"
    if method == "GET":
        return requests.get(url, params=params, headers=headers).json()
    elif method == "POST":
        return requests.post(url, json=params, headers=headers).json()
    return None
```

## 市场数据 API (现货)

### 1. 获取交易对信息

- **端点**: `GET /v4/public/symbol`
- **参数**: 无需参数（获取全部），或 `symbol` 指定交易对

### 2. 获取 Ticker

- **端点**: `GET /v4/public/ticker/24h`
- **参数**: `symbol` (可选，如 `btc_usdt`)

### 3. 获取深度

- **端点**: `GET /v4/public/depth`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对，如 `btc_usdt` |
| limit | INT | 否 | 深度档位，默认 20 |

### 4. 获取 K 线

- **端点**: `GET /v4/public/kline`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| interval | STRING | 是 | 周期: 1m,5m,15m,30m,1h,4h,1d,1w |
| limit | INT | 否 | 数量，默认 100，最大 1000 |

### 5. 获取最近成交

- **端点**: `GET /v4/public/trade/recent`
- **参数**: `symbol` (必需), `limit` (可选)

## 交易 API (现货)

### 1. 下单

- **端点**: `POST /v4/order`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对 |
| side | ENUM | 是 | BUY / SELL |
| type | ENUM | 是 | LIMIT / MARKET |
| quantity | DECIMAL | 条件 | 数量 |
| price | DECIMAL | 条件 | 价格(LIMIT) |
| timeInForce | ENUM | 否 | GTC / IOC / FOK |

### 2. 撤单

- **端点**: `DELETE /v4/order/{orderId}`

### 3. 查询账户余额

- **端点**: `GET /v4/balances`

## 合约 API

### REST 基础 URL: `https://fapi.xt.com`

### 1. 合约行情

- **端点**: `GET /future/market/v1/public/q/ticker`

### 2. 合约下单

- **端点**: `POST /future/trade/v1/order/create`
- **参数**: `symbol`, `side`, `type`, `positionSide`, `origQty`, `price`

### 3. 合约持仓

- **端点**: `GET /future/trade/v1/order/list-history`

## WebSocket

### 订阅格式

```json
{
  "method": "subscribe",
  "params": ["depth_update@btc_usdt"],
  "id": 1
}
```

### 频道类型

| 频道 | 格式 | 说明 |
|------|------|------|
| 深度 | `depth_update@{symbol}` | 深度增量更新 |
| K线 | `kline@{symbol},{interval}` | K线数据 |
| 成交 | `trade@{symbol}` | 实时成交 |
| Ticker | `ticker@{symbol}` | 24h行情 |

### 心跳

- 服务器发送 `ping`，客户端需回复 `pong`
- 数据使用 gzip 压缩

## 速率限制

| 类别 | 限制 |
|------|------|
| 公共接口 | 20次/秒 |
| 私有接口 | 10次/秒 |
| WebSocket | 5次/秒(订阅) |

## 错误代码

| 错误码 | 描述 |
|--------|------|
| 0 | 成功 |
| -1 | 系统错误 |
| -2 | 参数错误 |
| -3 | 签名验证失败 |
| -4 | 频率限制 |

## 特殊说明

- 交易对格式: `{base}_{quote}` 小写，如 `btc_usdt`
- 时间戳: 毫秒级
- 合约支持 USDT-M 和 Coin-M 两种模式
- WebSocket 数据使用 gzip 压缩 + Base64 编码

## 相关资源

- [XT.COM 官方 API 文档](https://doc.xt.com)
- [XT.COM GitHub](https://github.com/XtApis/xt-api)
- [CCXT XT.COM 实现](https://github.com/ccxt/ccxt)
