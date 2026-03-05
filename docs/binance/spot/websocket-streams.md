# Binance Spot WebSocket 行情数据流

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md>

## General Information

- Base Endpoint: **wss://stream.binance.com:9443**或**wss://stream.binance.com:443**
- 纯行情数据: **wss://data-stream.binance.vision** (不支持用户数据流)
- 单个流: `/ws/<streamName>`
- 组合流: `/stream?streams=<streamName1>/<streamName2>/<streamName3>`
- 组合流事件格式: `{"stream":"<streamName>","data":<rawPayload>}`
- 所有交易对名称为**小写**
- 单个连接最长有效 24 小时
- 服务器每 20 秒发送 `ping frame`，1 分钟内未收到 `pong` 将断开连接
- 所有时间字段默认为**毫秒**，可通过 `timeUnit=MICROSECOND` 切换为微秒
  - 例: `/stream?streams=btcusdt@trade&timeUnit=MICROSECOND`

## WebSocket 限制

- 每秒最多 5 条入站消息（PING/PONG/JSON 控制消息）
- 单个连接最多监听 **1024**个流
- 每 IP 每 5 分钟最多**300** 个连接

## 订阅/取消订阅

### 订阅

```json
{
  "method": "SUBSCRIBE",
  "params": ["btcusdt@aggTrade", "btcusdt@depth"],
  "id": 1
}

```

### 取消订阅

```json
{
  "method": "UNSUBSCRIBE",
  "params": ["btcusdt@depth"],
  "id": 312
}

```

### 列出当前订阅

```json
{ "method": "LIST_SUBSCRIPTIONS", "id": 3 }

```

### 设置属性

```json
{
  "method": "SET_PROPERTY",
  "params": ["combined", true],
  "id": 5
}

```

---

## 数据流详情

### Aggregate Trade Streams (归集成交)

- *Stream Name:** `<symbol>@aggTrade`

- *Update Speed:** 实时

```json
{
  "e": "aggTrade",    // Event type
  "E": 1672515782136, // Event time
  "s": "BNBBTC",      // Symbol
  "a": 12345,         // Aggregate trade ID
  "p": "0.001",       // Price
  "q": "100",         // Quantity
  "f": 100,           // First trade ID
  "l": 105,           // Last trade ID
  "T": 1672515782136, // Trade time
  "m": true,          // Is the buyer the market maker?
  "M": true           // Ignore
}

```

### Trade Streams (逐笔成交)

- *Stream Name:** `<symbol>@trade`

- *Update Speed:** 实时

```json
{
  "e": "trade",       // Event type
  "E": 1672515782136, // Event time
  "s": "BNBBTC",      // Symbol
  "t": 12345,         // Trade ID
  "p": "0.001",       // Price
  "q": "100",         // Quantity
  "T": 1672515782136, // Trade time
  "m": true,          // Is the buyer the market maker?
  "M": true           // Ignore
}

```

### Kline/Candlestick Streams (K 线)

- *Stream Name:** `<symbol>@kline_<interval>`

- *带时区偏移:** `<symbol>@kline_<interval>@+08:00`

- *Update Speed:** `1s` 间隔为 1000ms，其他为 2000ms

- *支持的间隔:** `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`

```json
{
  "e": "kline",
  "E": 1672515782136,
  "s": "BNBBTC",
  "k": {
    "t": 1672515780000, // Kline start time
    "T": 1672515839999, // Kline close time
    "s": "BNBBTC",      // Symbol
    "i": "1m",          // Interval
    "f": 100,           // First trade ID
    "L": 200,           // Last trade ID
    "o": "0.0010",      // Open price
    "c": "0.0020",      // Close price
    "h": "0.0025",      // High price
    "l": "0.0015",      // Low price
    "v": "1000",        // Base asset volume
    "n": 100,           // Number of trades
    "x": false,         // Is this kline closed?
    "q": "1.0000",      // Quote asset volume
    "V": "500",         // Taker buy base asset volume
    "Q": "0.500",       // Taker buy quote asset volume
    "B": "123456"       // Ignore
  }
}

```

### Individual Symbol Mini Ticker Stream

- *Stream Name:** `<symbol>@miniTicker`

- *Update Speed:** 1000ms

```json
{
  "e": "24hrMiniTicker",
  "E": 1672515782136,
  "s": "BNBBTC",
  "c": "0.0025",  // Close price
  "o": "0.0010",  // Open price
  "h": "0.0025",  // High price
  "l": "0.0010",  // Low price
  "v": "10000",   // Total traded base asset volume
  "q": "18"       // Total traded quote asset volume
}

```

### All Market Mini Tickers Stream

- *Stream Name:** `!miniTicker@arr`

- *Update Speed:** 1000ms

### Individual Symbol Ticker Streams (24hr)

- *Stream Name:** `<symbol>@ticker`

- *Update Speed:** 1000ms

```json
{
  "e": "24hrTicker",
  "E": 1672515782136,
  "s": "BNBBTC",
  "p": "0.0015",  // Price change
  "P": "250.00",  // Price change percent
  "w": "0.0018",  // Weighted average price
  "x": "0.0009",  // First trade(F)-1 price
  "c": "0.0025",  // Last price
  "Q": "10",      // Last quantity
  "b": "0.0024",  // Best bid price
  "B": "10",      // Best bid quantity
  "a": "0.0026",  // Best ask price
  "A": "100",     // Best ask quantity
  "o": "0.0010",  // Open price
  "h": "0.0025",  // High price
  "l": "0.0010",  // Low price
  "v": "10000",   // Total traded base asset volume
  "q": "18",      // Total traded quote asset volume
  "O": 0,         // Statistics open time
  "C": 86400000,  // Statistics close time
  "F": 0,         // First trade ID
  "L": 18150,     // Last trade Id
  "n": 18151      // Total number of trades
}

```

### Individual Symbol Rolling Window Statistics Streams

- *Stream Name:** `<symbol>@ticker_<window_size>`

- *Window Sizes:** `1h`, `4h`, `1d`

- *Update Speed:** 1000ms

### All Market Rolling Window Statistics Streams

- *Stream Name:** `!ticker_<window-size>@arr`

### Individual Symbol Book Ticker Streams (最优挂单)

- *Stream Name:** `<symbol>@bookTicker`

- *Update Speed:** 实时

```json
{
  "u": 400900217,    // order book updateId
  "s": "BNBUSDT",    // symbol
  "b": "25.35190000", // best bid price
  "B": "31.21000000", // best bid qty
  "a": "25.36520000", // best ask price
  "A": "40.66000000"  // best ask qty
}

```

### Average Price (均价)

- *Stream Name:** `<symbol>@avgPrice`

- *Update Speed:** 1000ms

```json
{
  "e": "avgPrice",
  "E": 1693907033000,
  "s": "BTCUSDT",
  "i": "5m",
  "w": "25776.86000000",
  "T": 1693907032213
}

```

### Partial Book Depth Streams (部分深度)

- *Stream Name:** `<symbol>@depth<levels>` 或 `<symbol>@depth<levels>@100ms`

- *Valid levels:** 5, 10, 20

- *Update Speed:** 1000ms 或 100ms

```json
{
  "lastUpdateId": 160,
  "bids": [["0.0024", "10"]],
  "asks": [["0.0026", "100"]]
}

```

### Diff. Depth Stream (增量深度)

- *Stream Name:** `<symbol>@depth` 或 `<symbol>@depth@100ms`

- *Update Speed:** 1000ms 或 100ms

```json
{
  "e": "depthUpdate",
  "E": 1672515782136,
  "s": "BNBBTC",
  "U": 157,           // First update ID in event
  "u": 160,           // Final update ID in event
  "b": [["0.0024", "10"]],  // Bids
  "a": [["0.0026", "100"]]  // Asks
}

```

## 本地维护 Order Book 的正确方法

1. 打开 WebSocket 连接到 `wss://stream.binance.com:9443/ws/bnbbtc@depth`
2. 缓存收到的事件，记录第一个事件的 `U`
3. 获取深度快照 `<https://api.binance.com/api/v3/depth?symbol=BNBBTC&limit=5000`>
4. 如果快照的 `lastUpdateId` < 步骤 2 的 `U`，返回步骤 3
5. 丢弃 `u <= lastUpdateId` 的缓存事件
6. 将本地 order book 设置为快照，更新 ID 为 `lastUpdateId`
7. 对所有后续事件应用更新：
   - 如果事件的 `u < 本地更新 ID`，忽略
   - 如果事件的 `U > 本地更新 ID + 1`，重新开始
   - 正常情况下，下一事件的 `U == 上一事件的 u + 1`

---

## 数据流汇总表

| Stream Name | 说明 | 更新速度 |

|-------------|------|----------|

| `<symbol>@aggTrade` | 归集成交 | 实时 |

| `<symbol>@trade` | 逐笔成交 | 实时 |

| `<symbol>@kline_<interval>` | K 线数据 | 1s/2s |

| `<symbol>@kline_<interval>@+08:00` | K 线(UTC+8) | 1s/2s |

| `<symbol>@miniTicker` | Mini Ticker | 1s |

| `!miniTicker@arr` | 全市场 Mini Ticker | 1s |

| `<symbol>@ticker` | 24hr Ticker | 1s |

| `<symbol>@ticker_<window>` | 滚动窗口统计 | 1s |

| `!ticker_<window>@arr` | 全市场滚动窗口 | 1s |

| `<symbol>@bookTicker` | 最优挂单 | 实时 |

| `<symbol>@avgPrice` | 均价 | 1s |

| `<symbol>@depth<levels>` | 部分深度 | 1s/100ms |

| `<symbol>@depth` | 增量深度 | 1s/100ms |
