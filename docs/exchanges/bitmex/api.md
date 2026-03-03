# BitMEX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://www.bitmex.com/app/restAPI>

## 交易所基本信息

- 官方名称: BitMEX
- 官网: <https://www.bitmex.com>
- CMC 衍生品排名: #47
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(Perpetual Swap)、期货(Futures)
- 支持的币种: BTC, ETH, SOL, XRP 等
- 手续费: Maker -0.0100%, Taker 0.0500% (永续合约)
- 最大杠杆: 100x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (生产) | `https://www.bitmex.com/api/v1` | 生产环境 |
| REST (测试) | `https://testnet.bitmex.com/api/v1` | 测试网 |
| WebSocket (生产) | `wss://ws.bitmex.com/realtime` | 生产 WebSocket |
| WebSocket (测试) | `wss://ws.testnet.bitmex.com/realtime` | 测试 WebSocket |

## 认证方式

### HMAC SHA256 签名

**请求头**:
- `api-key`: API Key
- `api-expires`: 过期时间戳(秒)
- `api-signature`: HMAC SHA256 签名

**签名步骤**:
1. 签名字符串 = `verb + path + expires + body`
2. 使用 Secret Key 进行 HMAC SHA256 签名

```python
import hmac
import time
import requests
from hashlib import sha256

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://www.bitmex.com/api/v1"

def generate_signature(verb, path, expires, data=""):
    message = verb + path + str(expires) + data
    return hmac.new(
        API_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        sha256
    ).hexdigest()

def signed_request(verb, path, data=None):
    expires = int(time.time()) + 60
    body = ""
    if data:
        import json
        body = json.dumps(data)
    signature = generate_signature(verb, "/api/v1" + path, expires, body)
    headers = {
        "api-key": API_KEY,
        "api-expires": str(expires),
        "api-signature": signature,
        "Content-Type": "application/json",
    }
    url = BASE_URL + path
    if verb == "GET":
        return requests.get(url, headers=headers).json()
    elif verb == "POST":
        return requests.post(url, data=body, headers=headers).json()
    elif verb == "PUT":
        return requests.put(url, data=body, headers=headers).json()
    elif verb == "DELETE":
        return requests.delete(url, data=body, headers=headers).json()
```

## 市场数据 API

### 1. 获取合约列表

- **端点**: `GET /instrument/active`
- **描述**: 获取所有活跃合约

### 2. 获取 Ticker

- **端点**: `GET /instrument`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 合约名称，如 XBTUSD |
| filter | JSON | 否 | 过滤条件 |
| columns | STRING | 否 | 返回字段 |

### 3. 获取深度

- **端点**: `GET /orderBook/L2`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 合约名称 |
| depth | INT | 否 | 深度档位，默认 25 |

### 4. 获取 K 线

- **端点**: `GET /trade/bucketed`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| binSize | STRING | 是 | 1m, 5m, 1h, 1d |
| symbol | STRING | 是 | 合约名称 |
| count | INT | 否 | 数量，默认 100，最大 1000 |
| startTime | STRING | 否 | ISO 8601 格式 |
| endTime | STRING | 否 | ISO 8601 格式 |
| partial | BOOL | 否 | 包含未完成K线 |

### 5. 获取最近成交

- **端点**: `GET /trade`
- **参数**: `symbol`, `count`, `start`, `startTime`, `endTime`

### 6. 获取资金费率

- **端点**: `GET /funding`
- **参数**: `symbol`, `count`, `startTime`, `endTime`

## 交易 API

### 1. 下单

- **端点**: `POST /order`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 合约名称 |
| side | ENUM | 否 | Buy / Sell |
| orderQty | INT | 条件 | 合约数量 |
| price | DECIMAL | 条件 | 限价(Limit) |
| stopPx | DECIMAL | 条件 | 止损/止盈价格 |
| ordType | ENUM | 否 | Market / Limit / Stop / StopLimit / MarketIfTouched / LimitIfTouched |
| timeInForce | ENUM | 否 | Day / GoodTillCancel / ImmediateOrCancel / FillOrKill |
| execInst | STRING | 否 | ParticipateDoNotInitiate(PostOnly), ReduceOnly, Close 等 |
| clOrdID | STRING | 否 | 客户自定义ID |

### 2. 修改订单

- **端点**: `PUT /order`
- **参数**: `orderID` 或 `origClOrdID`, `price`, `orderQty` 等

### 3. 撤单

- **端点**: `DELETE /order`
- **参数**: `orderID` 或 `clOrdID`

### 4. 批量撤单

- **端点**: `DELETE /order/all`
- **参数**: `symbol` (可选), `filter` (可选)

### 5. 查询订单

- **端点**: `GET /order`
- **参数**: `symbol`, `filter`, `count`

### 6. 查询持仓

- **端点**: `GET /position`
- **参数**: `filter`, `columns`, `count`

### 7. 设置杠杆

- **端点**: `POST /position/leverage`
- **参数**: `symbol`, `leverage`

### 8. 查询钱包

- **端点**: `GET /user/wallet`

### 9. 查询保证金

- **端点**: `GET /user/margin`
- **参数**: `currency` (默认 XBt)

## WebSocket

### 连接与订阅

```python
import websocket
import json

def on_open(ws):
    # 公共订阅
    ws.send(json.dumps({
        "op": "subscribe",
        "args": [
            "orderBookL2_25:XBTUSD",
            "trade:XBTUSD",
            "instrument:XBTUSD"
        ]
    }))

def on_message(ws, message):
    data = json.loads(message)
    table = data.get("table")
    action = data.get("action")  # partial, insert, update, delete
    print(f"Table: {table}, Action: {action}")

ws = websocket.WebSocketApp(
    "wss://ws.bitmex.com/realtime",
    on_open=on_open,
    on_message=on_message
)
ws.run_forever()
```

### 私有频道认证

```python
expires = int(time.time()) + 60
signature = generate_signature("GET", "/realtime", expires)
auth_msg = {
    "op": "authKeyExpires",
    "args": [API_KEY, expires, signature]
}
ws.send(json.dumps(auth_msg))
```

### 频道列表

| 频道 | 说明 | 认证 |
|------|------|------|
| `orderBookL2_25` | 25档深度 | 否 |
| `orderBookL2` | 全深度 | 否 |
| `trade` | 实时成交 | 否 |
| `instrument` | 合约信息(含Ticker) | 否 |
| `funding` | 资金费率 | 否 |
| `order` | 用户订单 | 是 |
| `position` | 用户持仓 | 是 |
| `margin` | 保证金 | 是 |
| `wallet` | 钱包余额 | 是 |
| `execution` | 成交 | 是 |

### 数据操作类型

- `partial`: 初始全量快照
- `insert`: 新增
- `update`: 更新
- `delete`: 删除

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| REST | 300次/5分钟 | 响应头含剩余次数 |
| REST (认证) | 60次/分钟 | 下单/撤单 |
| WebSocket | 无明确限制 | 建议节制订阅 |

响应头:
- `X-RateLimit-Limit`: 总限制
- `X-RateLimit-Remaining`: 剩余次数
- `X-RateLimit-Reset`: 重置时间

## 合约命名规则

| 类型 | 格式 | 示例 |
|------|------|------|
| 永续(反向) | `XBT{QUOTE}` | `XBTUSD` |
| 永续(正向) | `{BASE}USDT` | `ETHUSDT` |
| 期货 | `XBT{MONYY}` | `XBTM25` (2025年6月) |

## 特殊说明

- BitMEX 是最早的比特币衍生品交易所之一(2014年成立)
- 使用反向合约(Inverse Contract)和正向合约(Quanto/Linear)
- 支持测试网(testnet)进行免费测试
- WebSocket 使用表差分(table diffing)机制更新数据
- API 返回数据使用 snake_case

## 相关资源

- [BitMEX REST API 文档](https://www.bitmex.com/app/restAPI)
- [BitMEX WebSocket API](https://www.bitmex.com/app/wsAPI)
- [BitMEX API Explorer](https://www.bitmex.com/api/explorer/)
- [BitMEX 测试网](https://testnet.bitmex.com)
- [CCXT BitMEX 实现](https://github.com/ccxt/ccxt)
