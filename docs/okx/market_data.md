# Order Book Trading - Market Data

## REST API

### GET / Tickers

Retrieve the latest price snapshot, best bid/ask price, and trading volume in the last 24 hours.

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: IP

```
GET /api/v5/market/tickers

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `SPOT`, `SWAP`, `FUTURES`, `OPTION` |

| uly | String | No | Underlying, e.g. `BTC-USD`. For `FUTURES`/`SWAP`/`OPTION` |

| instFamily | String | No | Instrument family |

### GET / Ticker

Retrieve the latest price snapshot, best bid/ask price, and trading volume of an instrument.

- **Rate Limit**: 20 requests per 2 seconds

```
GET /api/v5/market/ticker

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID, e.g. `BTC-USDT` |

### GET / Order book

Retrieve order book of the instrument.

- **Rate Limit**: 40 requests per 2 seconds

```
GET /api/v5/market/books

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID |

| sz | String | No | Order book depth per side (max 400, default 1) |

### GET / Full order book

Retrieve the full order book of an instrument.

- **Rate Limit**: 10 requests per 2 seconds

```
GET /api/v5/market/books-full

```

### GET / Candlesticks

Retrieve the candlestick charts. Maximum 1,440 data entries returned.

- **Rate Limit**: 40 requests per 2 seconds

```
GET /api/v5/market/candles

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID |

| bar | String | No | Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M` |

| after | String | No | Pagination (older data) |

| before | String | No | Pagination (newer data) |

| limit | String | No | Default 100, max 300 |

### GET / Candlesticks history

Retrieve history candlestick charts from recent years (only for Instrument type SPOT currently).

```
GET /api/v5/market/history-candles

```

### GET / Trades

Retrieve the recent transactions of an instrument.

- **Rate Limit**: 100 requests per 2 seconds

```
GET /api/v5/market/trades

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID |

| limit | String | No | Default 100, max 500 |

### GET / Trades history

Retrieve the recent transactions of an instrument from the last 3 months with pagination.

```
GET /api/v5/market/history-trades

```

### GET / Option trades by instrument family

```
GET /api/v5/market/option/instrument-family-trades

```

### GET / Option trades

```
GET /api/v5/public/option-trades

```

### GET / 24H total volume

```
GET /api/v5/market/platform-24-volume

```

### GET / Call auction details

```
GET /api/v5/market/call-auction-details

```

---

## WebSocket

### WS / Tickers channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `tickers`

Subscribe Example:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "tickers",
    "instId": "BTC-USDT"
  }]
}

```

### WS / Candlesticks channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `candle1m`, `candle3m`, `candle5m`, `candle15m`, `candle30m`, `candle1H`, `candle2H`, `candle4H`, `candle6H`, `candle12H`, `candle1D`, `candle1W`, `candle1M`, `candle3M`

Subscribe Example:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "candle1m",
    "instId": "BTC-USDT"
  }]
}

```

### WS / Trades channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `trades`

### WS / All trades channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `trades-all`

### WS / Order book channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `books`, `books5`, `bbo-tbt`, `books-l2-tbt`, `books50-l2-tbt`

| Channel | Description |

|---------|-------------|

| `books` | 400 depth levels, push every 100ms |

| `books5` | 5 depth levels, push every 100ms |

| `bbo-tbt` | Best bid/ask, push tick-by-tick |

| `books-l2-tbt` | 400 depth levels, push tick-by-tick |

| `books50-l2-tbt` | 50 depth levels, push tick-by-tick |

Subscribe Example:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "books",
    "instId": "BTC-USDT"
  }]
}

```

### WS / Option trades channel

- **Channel**: `opt-trades`

### WS / Call auction details channel

- **Channel**: `call-auction-details`

---

## SBE Market Data

OKX provides SBE (Simple Binary Encoding) market data for ultra-low-latency use cases.

### Overview

SBE is a binary encoding format that provides lower latency compared to JSON. It follows the FIX SBE standard.

### Integration Information

- **WebSocket URL**: `wss://ws.okx.com:8443/ws/v5/public?sbe=true`
- **Supported Channels**: `books-sbe-tbt` (order book tick-by-tick)
- **XML Schema**: Available from OKX documentation

### SBE Order book

Subscribe to the SBE order book for ultra-low latency market data:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "books-sbe-tbt",
    "instId": "BTC-USDT"
  }]
}

```
