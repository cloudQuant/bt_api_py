# Binance Spot REST API

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md>

## General API Information

- Base Endpoints:
  - **<https://api.binance.com**>
  - **<https://api-gcp.binance.com**>
  - **<https://api1.binance.com**~**<https://api4.binance.com**(性能更好但稳定性稍低>)>
  - 纯行情数据:**<https://data-api.binance.vision**>
- 默认返回 JSON 格式，支持 SBE 格式
- 数据按时间顺序返回（除非另有说明）
- 所有时间戳字段默认为**毫秒**，可通过 `X-MBX-TIME-UNIT:MICROSECOND` header 切换为微秒
- 时间戳参数（如 `startTime`、`endTime`、`timestamp`）可传毫秒或微秒
- 支持 HMAC、RSA、Ed25519 密钥
- API 请求处理超时为 10 秒

## HTTP Return Codes

- `4XX` - 请求格式错误（客户端问题）
- `403` - WAF 规则触发
- `409` - cancelReplace 订单部分成功
- `429` - 超出请求频率限制
- `418` - IP 被自动封禁（持续发送请求后）
- `5XX` - 服务器内部错误（执行状态未知）

## Endpoint 参数规则

- `GET` 请求：参数以 query string 发送
- `POST`、`PUT`、`DELETE`：参数可通过 query string 或 request body (`application/x-www-form-urlencoded`)
- 参数可任意顺序发送
- query string 和 body 同时存在同名参数时，query string 优先

## 频率限制 (Rate Limits)

### IP 限制

- 响应头 `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` 显示当前已用权重
- 每个路由有不同的 `weight`
- 超出返回 429，持续违规返回 418（IP 封禁 2 分钟到 3 天）
- `Retry-After` 响应头告知等待秒数

### 下单频率限制

- 响应头 `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)` 显示已下单数
- 已成交订单会释放计数，详见 order_count_decrement

## Request Security

| 安全类型 | 说明 |

|----------|------|

| `NONE` | 公开行情数据 |

| `TRADE` | 交易操作 |

| `USER_DATA` | 私有账户信息 |

| `USER_STREAM` | 管理数据流订阅 |

### SIGNED 请求

- 需要额外的 `signature` 参数
- 需要 `timestamp` 参数（毫秒或微秒）
- 可选 `recvWindow` 参数（默认 5000ms，最大 60000ms）

- --

# Public API Endpoints

## General Endpoints

### Test Connectivity

```bash
GET /api/v3/ping

```bash
测试 REST API 连通性。

- *Weight:**1 |**Security:** NONE

- *Response:**

```json
{}

```bash

### Check Server Time

```bash
GET /api/v3/time

```bash
获取服务器当前时间。

- *Weight:**1 |**Security:** NONE

- *Response:**

```json
{ "serverTime": 1499827319559 }

```bash

### Exchange Information

```bash
GET /api/v3/exchangeInfo

```bash
获取交易规则和交易对信息。

- *Weight:**20 |**Security:** NONE

- *Parameters:**

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | 单个交易对 |

| symbols | ARRAY OF STRING | NO | 多个交易对 |

| permissions | ENUM | NO | 按权限筛选，不能与 symbol/symbols 同时使用 |

| showPermissionSets | BOOLEAN | NO | 是否填充 permissionSets 字段，默认 true |

| symbolStatus | ENUM | NO | 按交易状态筛选: `TRADING`, `HALT`, `BREAK` |

- *Response:**

```json
{
  "timezone": "UTC",
  "serverTime": 1565246363776,
  "rateLimits": [],
  "exchangeFilters": [],
  "symbols": [
    {
      "symbol": "ETHBTC",
      "status": "TRADING",
      "baseAsset": "ETH",
      "baseAssetPrecision": 8,
      "quoteAsset": "BTC",
      "quoteAssetPrecision": 8,
      "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"],
      "icebergAllowed": true,
      "ocoAllowed": true,
      "otoAllowed": true,
      "quoteOrderQtyMarketAllowed": true,
      "allowTrailingStop": false,
      "cancelReplaceAllowed": false,
      "isSpotTradingAllowed": true,
      "isMarginTradingAllowed": true,
      "filters": [],
      "permissionSets": [["SPOT", "MARGIN"]],
      "defaultSelfTradePreventionMode": "NONE",
      "allowedSelfTradePreventionModes": ["NONE"]
    }
  ],
  "sors": [
    { "baseAsset": "BTC", "symbols": ["BTCUSDT", "BTCUSDC"] }
  ]
}

```bash

- --

## Market Data Endpoints

### Order Book

```bash
GET /api/v3/depth

```bash

- *Weight:** 按 limit 调整: 1-100=5, 101-500=25, 501-1000=50, 1001-5000=250

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| limit | INT | NO | 默认 100，最大 5000 |

- *Response:**

```json
{
  "lastUpdateId": 1027024,
  "bids": [["4.00000000", "431.00000000"]],
  "asks": [["4.00000200", "12.00000000"]]
}

```bash

### Recent Trades List

```bash
GET /api/v3/trades

```bash

- *Weight:**25 |**Security:** NONE

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| limit | INT | NO | 默认 500，最大 1000 |

- *Response:**

```json
[
  {
    "id": 28457,
    "price": "4.00000100",
    "qty": "12.00000000",
    "quoteQty": "48.000012",
    "time": 1499865549590,
    "isBuyerMaker": true,
    "isBestMatch": true
  }
]

```bash

### Old Trade Lookup

```bash
GET /api/v3/historicalTrades

```bash

- *Weight:** 25

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| limit | INT | NO | 默认 500，最大 1000 |

| fromId | LONG | NO | 从该 TradeId 开始获取 |

### Compressed/Aggregate Trades List

```bash
GET /api/v3/aggTrades

```bash

- *Weight:** 4

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| fromId | LONG | NO | 起始聚合交易 ID (INCLUSIVE) |

| startTime | LONG | NO | 起始时间戳 (INCLUSIVE) |

| endTime | LONG | NO | 结束时间戳 (INCLUSIVE) |

| limit | INT | NO | 默认 500，最大 1000 |

- *Response:**

```json
[
  {
    "a": 26129,         // Aggregate tradeId
    "p": "0.01633102",  // Price
    "q": "4.70443515",  // Quantity
    "f": 27781,         // First tradeId
    "l": 27781,         // Last tradeId
    "T": 1498793709153, // Timestamp
    "m": true,          // Is the buyer the maker?
    "M": true           // Was the trade the best price match?
  }
]

```bash

### Kline/Candlestick Data

```bash
GET /api/v3/klines

```bash

- *Weight:** 2

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| interval | ENUM | YES | 见下方 |

| startTime | LONG | NO | |

| endTime | LONG | NO | |

| timeZone | STRING | NO | 默认 0 (UTC)，范围 [-12:00, +14:00] |

| limit | INT | NO | 默认 500，最大 1000 |

- *支持的 Kline 间隔:**

| 周期 | 值 |

|------|-----|

| 秒 | `1s` |

| 分钟 | `1m`, `3m`, `5m`, `15m`, `30m` |

| 小时 | `1h`, `2h`, `4h`, `6h`, `8h`, `12h` |

| 天 | `1d`, `3d` |

| 周 | `1w` |

| 月 | `1M` |

- *Response:**

```json
[
  [
    1499040000000,      // Kline open time
    "0.01634790",       // Open price
    "0.80000000",       // High price
    "0.01575800",       // Low price
    "0.01577100",       // Close price
    "148976.11427815",  // Volume
    1499644799999,      // Kline close time
    "2434.19055334",    // Quote asset volume
    308,                // Number of trades
    "1756.87402397",    // Taker buy base asset volume
    "28.46694368",      // Taker buy quote asset volume
    "0"                 // Unused field, ignore
  ]
]

```bash

### UIKlines

```bash
GET /api/v3/uiKlines

```bash
与 klines 参数和响应相同，返回优化的 K 线数据用于图表展示。

- *Weight:** 2

### Current Average Price

```bash
GET /api/v3/avgPrice

```bash

- *Weight:** 2

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

- *Response:**

```json
{
  "mins": 5,
  "price": "9.35751834",
  "closeTime": 1694061154503
}

```bash

### 24hr Ticker Price Change Statistics

```bash
GET /api/v3/ticker/24hr

```bash

- *Weight:** symbol=1 时为 2，省略时为 80

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | 不能与 symbols 同时使用 |

| symbols | STRING | NO | 不能与 symbol 同时使用 |

| type | ENUM | NO | `FULL` (默认) 或 `MINI` |

- *Response (FULL):**

```json
{
  "symbol": "BNBBTC",
  "priceChange": "-94.99999800",
  "priceChangePercent": "-95.960",
  "weightedAvgPrice": "0.29628482",
  "prevClosePrice": "0.10002000",
  "lastPrice": "4.00000200",
  "lastQty": "200.00000000",
  "bidPrice": "4.00000000",
  "bidQty": "100.00000000",
  "askPrice": "4.00000200",
  "askQty": "100.00000000",
  "openPrice": "99.00000000",
  "highPrice": "100.00000000",
  "lowPrice": "0.10000000",
  "volume": "8913.30000000",
  "quoteVolume": "15.30000000",
  "openTime": 1499783499040,
  "closeTime": 1499869899040,
  "firstId": 28385,
  "lastId": 28460,
  "count": 76
}

```bash

### Symbol Price Ticker

```bash
GET /api/v3/ticker/price

```bash

- *Weight:** symbol=1 时为 2，省略时为 4

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | |

| symbols | STRING | NO | |

- *Response:**

```json
{ "symbol": "LTCBTC", "price": "4.00000200" }

```bash

### Symbol Order Book Ticker

```bash
GET /api/v3/ticker/bookTicker

```bash

- *Weight:** symbol=1 时为 2，省略时为 4

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | |

| symbols | STRING | NO | |

- *Response:**

```json
{
  "symbol": "LTCBTC",
  "bidPrice": "4.00000000",
  "bidQty": "431.00000000",
  "askPrice": "4.00000200",
  "askQty": "9.00000000"
}

```bash

### Rolling Window Price Change Statistics

```bash
GET /api/v3/ticker

```bash
滚动窗口价格变化统计。

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | |

| symbols | STRING | NO | |

| windowSize | ENUM | NO | `1m`~`59m`, `1h`~`23h`, `1d`~`7d` 默认 `1d` |

| type | ENUM | NO | `FULL` 或 `MINI` |

- --

## Trading Endpoints

### New Order (TRADE)

```bash
POST /api/v3/order

```bash

- *Weight:**1 |**Unfilled Order Count:** 1

- *Parameters:**

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| side | ENUM | YES | `BUY` / `SELL` |

| type | ENUM | YES | 订单类型 |

| timeInForce | ENUM | NO | `GTC` / `IOC` / `FOK` |

| quantity | DECIMAL | NO | |

| quoteOrderQty | DECIMAL | NO | |

| price | DECIMAL | NO | |

| newClientOrderId | STRING | NO | 自定义订单 ID |

| strategyId | LONG | NO | |

| strategyType | INT | NO | 不能小于 1000000 |

| stopPrice | DECIMAL | NO | 用于 STOP_LOSS, TAKE_PROFIT 等 |

| trailingDelta | LONG | NO | 追踪止损 |

| icebergQty | DECIMAL | NO | 冰山订单数量 |

| newOrderRespType | ENUM | NO | `ACK`, `RESULT`, `FULL` |

| selfTradePreventionMode | ENUM | NO | STP 模式 |

| pegPriceType | ENUM | NO | `PRIMARY_PEG` / `MARKET_PEG` |

| pegOffsetValue | INT | NO | 挂钩价格偏移 (max: 100) |

| pegOffsetType | ENUM | NO | 仅支持 `PRICE_LEVEL` |

| recvWindow | DECIMAL | NO | 最大 60000 |

| timestamp | LONG | YES | |

- *各订单类型必需参数:**

| Type | 必需参数 | 说明 |

|------|----------|------|

| `LIMIT` | `timeInForce`, `quantity`, `price` | |

| `MARKET` | `quantity` 或 `quoteOrderQty` | 按市价买卖 |

| `STOP_LOSS` | `quantity`, `stopPrice`/`trailingDelta` | 达到条件时执行 MARKET 订单 |

| `STOP_LOSS_LIMIT` | `timeInForce`, `quantity`, `price`, `stopPrice`/`trailingDelta` | |

| `TAKE_PROFIT` | `quantity`, `stopPrice`/`trailingDelta` | 达到条件时执行 MARKET 订单 |

| `TAKE_PROFIT_LIMIT` | `timeInForce`, `quantity`, `price`, `stopPrice`/`trailingDelta` | |

| `LIMIT_MAKER` | `quantity`, `price` | POST-ONLY 订单 |

- *Response (FULL):**

```json
{
  "symbol": "BTCUSDT",
  "orderId": 28,
  "orderListId": -1,
  "clientOrderId": "6gCrw2kRUAF9CvJDGP16IP",
  "transactTime": 1507725176595,
  "price": "0.00000000",
  "origQty": "10.00000000",
  "executedQty": "10.00000000",
  "cummulativeQuoteQty": "10.00000000",
  "status": "FILLED",
  "timeInForce": "GTC",
  "type": "MARKET",
  "side": "SELL",
  "workingTime": 1507725176595,
  "selfTradePreventionMode": "NONE",
  "fills": [
    {
      "price": "4000.00000000",
      "qty": "1.00000000",
      "commission": "4.00000000",
      "commissionAsset": "USDT",
      "tradeId": 56
    }
  ]
}

```bash

### Test New Order (TRADE)

```bash
POST /api/v3/order/test

```bash
测试下单（不实际执行）。参数同 `POST /api/v3/order`，额外支持 `computeCommissionRates` 参数。

- *Weight:** 无 computeCommissionRates 时为 1，有时为 20

### Cancel Order (TRADE)

```bash
DELETE /api/v3/order

```bash

- *Weight:** 1

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | NO | orderId 或 origClientOrderId 必须发送一个 |

| origClientOrderId | STRING | NO | |

| newClientOrderId | STRING | NO | |

| cancelRestrictions | ENUM | NO | `ONLY_NEW` / `ONLY_PARTIALLY_FILLED` |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### Cancel All Open Orders on a Symbol (TRADE)

```bash
DELETE /api/v3/openOrders

```bash
取消交易对上所有活跃订单（包括订单列表中的）。

- *Weight:** 1

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### Cancel Replace Order (TRADE)

```bash
POST /api/v3/order/cancelReplace

```bash
取消现有订单并下新单。

- *Weight:** 1

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| side | ENUM | YES | |

| type | ENUM | YES | |

| cancelReplaceMode | ENUM | YES | `STOP_ON_FAILURE` / `ALLOW_FAILURE` |

| cancelOrderId | LONG | NO | |

| cancelOrigClientOrderId | STRING | NO | |

| cancelRestrictions | ENUM | NO | `ONLY_NEW` / `ONLY_PARTIALLY_FILLED` |

| (其他同 New Order 参数) | | | |

### Order Amend Keep Priority (TRADE)

```bash
PUT /api/v3/order/amend/keepPriority

```bash
减少现有挂单的数量（保持队列优先级）。

- *Weight:**4 |**Unfilled Order Count:** 0

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | NO* | orderId 或 origClientOrderId 必须发送一个 |

| origClientOrderId | STRING | NO*| |

| newClientOrderId | STRING | NO | |

| newQty | DECIMAL | YES | 必须 > 0 且 < 当前订单数量 |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### New Order using SOR (TRADE)

```bash
POST /api/v3/sor/order

```bash
使用智能订单路由 (SOR) 下单。仅支持 `LIMIT` 和 `MARKET`。

- *Weight:** 1

### Test New Order using SOR (TRADE)

```bash
POST /api/v3/sor/order/test

```bash
测试 SOR 下单。

- --

## Account Endpoints

### Account Information (USER_DATA)

```bash
GET /api/v3/account

```bash

- *Weight:** 20

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| omitZeroBalances | BOOLEAN | NO | 设为 true 仅返回非零余额，默认 false |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

- *Response:**

```json
{
  "makerCommission": 15,
  "takerCommission": 15,
  "commissionRates": {
    "maker": "0.00150000",
    "taker": "0.00150000",
    "buyer": "0.00000000",
    "seller": "0.00000000"
  },
  "canTrade": true,
  "canWithdraw": true,
  "canDeposit": true,
  "accountType": "SPOT",
  "balances": [
    { "asset": "BTC", "free": "4723846.89208129", "locked": "0.00000000" },
    { "asset": "LTC", "free": "4763368.68006011", "locked": "0.00000000" }
  ],
  "permissions": ["SPOT"],
  "uid": 354937868
}

```bash

### Query Order (USER_DATA)

```bash
GET /api/v3/order

```bash

- *Weight:** 4

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | NO | orderId 或 origClientOrderId 必须发送一个 |

| origClientOrderId | STRING | NO | |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### Current Open Orders (USER_DATA)

```bash
GET /api/v3/openOrders

```bash

- *Weight:** 单交易对为 6，省略为 80

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### All Orders (USER_DATA)

```bash
GET /api/v3/allOrders

```bash
获取所有订单（活跃、已取消、已成交）。

- *Weight:** 20

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | NO | |

| startTime | LONG | NO | |

| endTime | LONG | NO | startTime 和 endTime 间隔不超过 24 小时 |

| limit | INT | NO | 默认 500，最大 1000 |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### Query Order List (USER_DATA)

```bash
GET /api/v3/orderList

```bash

- *Weight:** 4

### Query All Order Lists (USER_DATA)

```bash
GET /api/v3/allOrderList

```bash

- *Weight:** 20

### Query Open Order Lists (USER_DATA)

```bash
GET /api/v3/openOrderList

```bash

- *Weight:** 6

### Account Trade List (USER_DATA)

```bash
GET /api/v3/myTrades

```bash

- *Weight:** 无 orderId 为 20，有 orderId 为 5

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | NO | |

| startTime | LONG | NO | |

| endTime | LONG | NO | startTime 和 endTime 间隔不超过 24 小时 |

| fromId | LONG | NO | 从该 TradeId 开始 |

| limit | INT | NO | 默认 500，最大 1000 |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

- *Response:**

```json
[
  {
    "symbol": "BNBBTC",
    "id": 28457,
    "orderId": 100234,
    "orderListId": -1,
    "price": "4.00000100",
    "qty": "12.00000000",
    "quoteQty": "48.000012",
    "commission": "10.10000000",
    "commissionAsset": "BNB",
    "time": 1499865549590,
    "isBuyer": true,
    "isMaker": false,
    "isBestMatch": true
  }
]

```bash

### Query Unfilled Order Count (USER_DATA)

```bash
GET /api/v3/rateLimit/order

```bash

- *Weight:** 40

### Query Prevented Matches (USER_DATA)

```bash
GET /api/v3/myPreventedMatches

```bash
查询因 STP 过期的订单列表。

- *Weight:** 按 preventedMatchId 查询为 2，按 orderId 查询为 20

### Query Allocations (USER_DATA)

```bash
GET /api/v3/myAllocations

```bash
获取 SOR 订单的分配结果。

- *Weight:** 20

### Query Commission Rates (USER_DATA)

```bash
GET /api/v3/account/commission

```bash

- *Weight:** 20

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

- *Response:**

```json
{
  "symbol": "BTCUSDT",
  "standardCommission": { "maker": "0.00000010", "taker": "0.00000020", "buyer": "0.00000030", "seller": "0.00000040" },
  "taxCommission": { "maker": "0.00000112", "taker": "0.00000114", "buyer": "0.00000118", "seller": "0.00000116" },
  "discount": {
    "enabledForAccount": true,
    "enabledForSymbol": true,
    "discountAsset": "BNB",
    "discount": "0.75000000"
  }
}

```bash

### Query Order Amendments (USER_DATA)

```bash
GET /api/v3/order/amendments

```bash
查询单个订单的所有修改记录。

- *Weight:** 4

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| orderId | LONG | YES | |

| fromExecutionId | LONG | NO | |

| limit | LONG | NO | 默认 500，最大 1000 |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

### Query Relevant Filters (USER_DATA)

```bash
GET /api/v3/myFilters

```bash
获取账户在指定交易对上的相关过滤器（唯一可查看 MAX_ASSET 过滤器的接口）。

- *Weight:** 40

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | |

| recvWindow | DECIMAL | NO | |

| timestamp | LONG | YES | |

- --

## API Endpoints 汇总表

| Method | Endpoint | Security | Weight | Description |

|--------|----------|----------|--------|-------------|

| GET | `/api/v3/ping` | NONE | 1 | 测试连通性 |

| GET | `/api/v3/time` | NONE | 1 | 服务器时间 |

| GET | `/api/v3/exchangeInfo` | NONE | 20 | 交易规则和交易对信息 |

| GET | `/api/v3/depth` | NONE | 5-250 | 深度数据 |

| GET | `/api/v3/trades` | NONE | 25 | 最近成交 |

| GET | `/api/v3/historicalTrades` | NONE | 25 | 历史成交 |

| GET | `/api/v3/aggTrades` | NONE | 4 | 归集成交 |

| GET | `/api/v3/klines` | NONE | 2 | K 线数据 |

| GET | `/api/v3/uiKlines` | NONE | 2 | UI 优化 K 线 |

| GET | `/api/v3/avgPrice` | NONE | 2 | 当前均价 |

| GET | `/api/v3/ticker/24hr` | NONE | 2-80 | 24 小时价格变动 |

| GET | `/api/v3/ticker/price` | NONE | 2-4 | 最新价格 |

| GET | `/api/v3/ticker/bookTicker` | NONE | 2-4 | 最优挂单 |

| GET | `/api/v3/ticker` | NONE | 2-200 | 滚动窗口统计 |

| POST | `/api/v3/order` | TRADE | 1 | 下单 |

| POST | `/api/v3/order/test` | TRADE | 1/20 | 测试下单 |

| DELETE | `/api/v3/order` | TRADE | 1 | 撤单 |

| DELETE | `/api/v3/openOrders` | TRADE | 1 | 撤销全部挂单 |

| POST | `/api/v3/order/cancelReplace` | TRADE | 1 | 撤单并下新单 |

| PUT | `/api/v3/order/amend/keepPriority` | TRADE | 4 | 修改订单(保持优先级) |

| POST | `/api/v3/sor/order` | TRADE | 1 | SOR 下单 |

| POST | `/api/v3/sor/order/test` | TRADE | 1/20 | 测试 SOR 下单 |

| GET | `/api/v3/account` | USER_DATA | 20 | 账户信息 |

| GET | `/api/v3/order` | USER_DATA | 4 | 查询订单 |

| GET | `/api/v3/openOrders` | USER_DATA | 6/80 | 当前挂单 |

| GET | `/api/v3/allOrders` | USER_DATA | 20 | 所有订单 |

| GET | `/api/v3/orderList` | USER_DATA | 4 | 查询订单列表 |

| GET | `/api/v3/allOrderList` | USER_DATA | 20 | 所有订单列表 |

| GET | `/api/v3/openOrderList` | USER_DATA | 6 | 当前挂单列表 |

| GET | `/api/v3/myTrades` | USER_DATA | 5/20 | 成交记录 |

| GET | `/api/v3/rateLimit/order` | USER_DATA | 40 | 未成交订单计数 |

| GET | `/api/v3/myPreventedMatches` | USER_DATA | 2/20 | STP 匹配记录 |

| GET | `/api/v3/myAllocations` | USER_DATA | 20 | SOR 分配记录 |

| GET | `/api/v3/account/commission` | USER_DATA | 20 | 佣金费率 |

| GET | `/api/v3/order/amendments` | USER_DATA | 4 | 订单修改记录 |

| GET | `/api/v3/myFilters` | USER_DATA | 40 | 相关过滤器 |
