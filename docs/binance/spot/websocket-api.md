# Binance Spot WebSocket API

> 来源: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md

WebSocket API 允许通过 WebSocket 连接执行与 REST API 相同的操作（下单、撤单、查询等）。

## General Information

* Base Endpoint: **wss://ws-api.binance.com:443/ws-api/v3**
* Testnet: **wss://testnet.binance.vision/ws-api/v3**
* 请求和响应均为 JSON 格式
* 支持 SBE 响应格式
* 所有时间戳默认为**毫秒**

## Request Format

```json
{
  "id": "unique-request-id",
  "method": "method.name",
  "params": {
    "param1": "value1",
    "param2": "value2"
  }
}
```

* `id` - 请求标识符（64位有符号整数、字母数字字符串或 null）
* `method` - API 方法名
* `params` - 请求参数

## Response Format

```json
{
  "id": "unique-request-id",
  "status": 200,
  "result": {},
  "rateLimits": [
    {
      "rateLimitType": "REQUEST_WEIGHT",
      "interval": "MINUTE",
      "intervalNum": 1,
      "limit": 6000,
      "count": 10
    }
  ]
}
```

### Status Codes

| Status | Description |
|--------|-------------|
| 200 | 请求成功 |
| 4xx | 客户端错误 |
| 429 | 超出频率限制 |
| 5xx | 服务器内部错误 |

## Rate Limits

### Connection Limits
* 每 IP 每 5 分钟最多 **300** 个连接
* 单个连接最多每秒 **5** 条请求
* 连接有效期 24 小时

### IP Limits
* 与 REST API 共享 IP 限制
* 每个响应中包含 `rateLimits` 数组

### Unfilled Order Count
* 与 REST API 共享下单频率限制

## Request Security

与 REST API 相同，支持 `NONE`、`TRADE`、`USER_DATA`、`USER_STREAM`。

### SIGNED 请求
* 需要 `apiKey`、`signature`、`timestamp` 参数
* 支持 HMAC、RSA、Ed25519
* `recvWindow` 默认 5000ms，最大 60000ms

## Authentication Requests

### Log In with API Key (session.logon)

```json
{
  "id": "login-1",
  "method": "session.logon",
  "params": {
    "apiKey": "your-api-key",
    "signature": "computed-signature",
    "timestamp": 1649729878532
  }
}
```

登录后，后续请求自动使用该 API Key，无需每次都发送。

### Query Session Status (session.status)

```json
{
  "id": "status-1",
  "method": "session.status"
}
```

### Log Out (session.logout)

```json
{
  "id": "logout-1",
  "method": "session.logout"
}
```

---

## API Methods

WebSocket API 方法与 REST API 端点一一对应，以下为完整方法映射：

### General

| WebSocket Method | 对应 REST Endpoint | 说明 |
|------------------|-------------------|------|
| `ping` | `GET /api/v3/ping` | 测试连通性 |
| `time` | `GET /api/v3/time` | 服务器时间 |
| `exchangeInfo` | `GET /api/v3/exchangeInfo` | 交易规则和交易对信息 |

### Market Data

| WebSocket Method | 对应 REST Endpoint | 说明 |
|------------------|-------------------|------|
| `depth` | `GET /api/v3/depth` | 深度数据 |
| `trades.recent` | `GET /api/v3/trades` | 最近成交 |
| `trades.historical` | `GET /api/v3/historicalTrades` | 历史成交 |
| `trades.aggregate` | `GET /api/v3/aggTrades` | 归集成交 |
| `klines` | `GET /api/v3/klines` | K线数据 |
| `uiKlines` | `GET /api/v3/uiKlines` | UI优化K线 |
| `avgPrice` | `GET /api/v3/avgPrice` | 当前均价 |
| `ticker.24hr` | `GET /api/v3/ticker/24hr` | 24小时价格变动 |
| `ticker.tradingDay` | `GET /api/v3/ticker/tradingDay` | 交易日Ticker |
| `ticker.price` | `GET /api/v3/ticker/price` | 最新价格 |
| `ticker.book` | `GET /api/v3/ticker/bookTicker` | 最优挂单 |
| `ticker` | `GET /api/v3/ticker` | 滚动窗口统计 |

### Trading

| WebSocket Method | 对应 REST Endpoint | 说明 |
|------------------|-------------------|------|
| `order.place` | `POST /api/v3/order` | 下单 |
| `order.test` | `POST /api/v3/order/test` | 测试下单 |
| `order.status` | `GET /api/v3/order` | 查询订单 |
| `order.cancel` | `DELETE /api/v3/order` | 撤单 |
| `order.cancelReplace` | `POST /api/v3/order/cancelReplace` | 撤单并下新单 |
| `order.amend.keepPriority` | `PUT /api/v3/order/amend/keepPriority` | 修改订单(保持优先级) |
| `openOrders.status` | `GET /api/v3/openOrders` | 当前挂单 |
| `openOrders.cancelAll` | `DELETE /api/v3/openOrders` | 撤销全部挂单 |
| `orderList.place.oco` | OCO 下单 | 下 OCO 订单 |
| `orderList.place.oto` | OTO 下单 | 下 OTO 订单 |
| `orderList.place.otoco` | OTOCO 下单 | 下 OTOCO 订单 |
| `orderList.status` | `GET /api/v3/orderList` | 查询订单列表 |
| `orderList.cancel` | 取消订单列表 | 取消订单列表 |
| `openOrderLists.status` | `GET /api/v3/openOrderList` | 当前挂单列表 |
| `sor.order.place` | `POST /api/v3/sor/order` | SOR下单 |
| `sor.order.test` | `POST /api/v3/sor/order/test` | 测试SOR下单 |

### Account

| WebSocket Method | 对应 REST Endpoint | 说明 |
|------------------|-------------------|------|
| `account.status` | `GET /api/v3/account` | 账户信息 |
| `account.commission` | `GET /api/v3/account/commission` | 佣金费率 |
| `allOrders` | `GET /api/v3/allOrders` | 所有订单 |
| `allOrderLists` | `GET /api/v3/allOrderList` | 所有订单列表 |
| `myTrades` | `GET /api/v3/myTrades` | 成交记录 |
| `myPreventedMatches` | `GET /api/v3/myPreventedMatches` | STP匹配记录 |
| `myAllocations` | `GET /api/v3/myAllocations` | SOR分配记录 |
| `order.amendments` | `GET /api/v3/order/amendments` | 订单修改记录 |
| `account.rateLimit.order` | `GET /api/v3/rateLimit/order` | 未成交订单计数 |
| `myFilters` | `GET /api/v3/myFilters` | 相关过滤器 |

### User Data Stream

| WebSocket Method | 说明 |
|------------------|------|
| `userDataStream.start` | 创建 listenKey |
| `userDataStream.ping` | 延长 listenKey 有效期 |
| `userDataStream.stop` | 关闭 listenKey |
| `userDataStream.subscribe` | 订阅用户数据流 |
| `userDataStream.unsubscribe` | 取消订阅用户数据流 |

---

## 请求示例

### 下单示例

```json
{
  "id": "order-1",
  "method": "order.place",
  "params": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "GTC",
    "quantity": "0.001",
    "price": "50000",
    "apiKey": "your-api-key",
    "signature": "computed-signature",
    "timestamp": 1649729878532
  }
}
```

### 查询订单示例

```json
{
  "id": "query-1",
  "method": "order.status",
  "params": {
    "symbol": "BTCUSDT",
    "orderId": 12345,
    "apiKey": "your-api-key",
    "signature": "computed-signature",
    "timestamp": 1649729878532
  }
}
```

### 撤单示例

```json
{
  "id": "cancel-1",
  "method": "order.cancel",
  "params": {
    "symbol": "BTCUSDT",
    "orderId": 12345,
    "apiKey": "your-api-key",
    "signature": "computed-signature",
    "timestamp": 1649729878532
  }
}
```
