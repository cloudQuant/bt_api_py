# Public Data

Public data endpoints do not require authentication.

## REST API

### GET / Get instruments

Retrieve a list of instruments with open contracts.

- ***Rate Limit**: 20 requests per 2 seconds
- ***Rate limit rule**: IP + Instrument Type

```
GET /api/v5/public/instruments

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instType | String | Yes | `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |
| uly | String | Conditional | Underlying. Required for `FUTURES`/`SWAP`/`OPTION` |
| instFamily | String | No | Instrument family |
| instId | String | No | Instrument ID |

### GET / Get estimated delivery/exercise price

```
GET /api/v5/public/estimated-price

```

### GET / Get delivery/exercise history

```
GET /api/v5/public/delivery-exercise-history

```

### GET / Get estimated future settlement price

```
GET /api/v5/public/estimated-settlement-price

```

### GET / Get futures settlement history

```
GET /api/v5/public/settlement-history

```

### GET / Get funding rate

Retrieve funding rate.

- ***Rate Limit**: 20 requests per 2 seconds

```
GET /api/v5/public/funding-rate

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID, e.g. `BTC-USDT-SWAP` |

### GET / Get funding rate history

```
GET /api/v5/public/funding-rate-history

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID |
| before | String | No | Pagination |
| after | String | No | Pagination |
| limit | String | No | Default 100, max 100 |

### GET / Get open interest

```
GET /api/v5/public/open-interest

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instType | String | Yes | `SWAP`, `FUTURES`, `OPTION` |
| uly | String | No | Underlying |
| instFamily | String | No | Instrument family |
| instId | String | No | Instrument ID |

### GET / Get limit price

```
GET /api/v5/public/price-limit

```

### GET / Get option market data

```
GET /api/v5/public/opt-summary

```

### GET / Get discount rate and interest-free quota

```
GET /api/v5/public/discount-rate-interest-free-quota

```

### GET / Get system time

```
GET /api/v5/public/time

```

### GET / Get mark price

```
GET /api/v5/public/mark-price

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instType | String | Yes | `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |
| uly | String | No | Underlying |
| instFamily | String | No | Instrument family |
| instId | String | No | Instrument ID |

### GET / Get position tiers

```
GET /api/v5/public/position-tiers

```

### GET / Get interest rate and loan quota

```
GET /api/v5/public/interest-rate-loan-quota

```

### GET / Get underlying

```
GET /api/v5/public/underlying

```

### GET / Get security fund

```
GET /api/v5/public/insurance-fund

```

### GET / Unit convert

```
GET /api/v5/public/convert-contract-coin

```

### GET / Get option tick bands

```
GET /api/v5/public/instrument-tick-bands

```

### GET / Get premium history

```
GET /api/v5/public/premium-history

```

### GET / Get index tickers

```
GET /api/v5/market/index-tickers

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| quoteCcy | String | Conditional | Quote currency (e.g. `USD`) |
| instId | String | Conditional | Index, e.g. `BTC-USD` |

### GET / Get index candlesticks

```
GET /api/v5/market/index-candles

```

### GET / Get index candlesticks history

```
GET /api/v5/market/history-index-candles

```

### GET / Get mark price candlesticks

```
GET /api/v5/market/mark-price-candles

```

### GET / Get mark price candlesticks history

```
GET /api/v5/market/history-mark-price-candles

```

### GET / Get exchange rate

```
GET /api/v5/market/exchange-rate

```

### GET / Get index components

```
GET /api/v5/market/index-components

```

### GET / Get economic calendar data

```
GET /api/v5/public/economic-calendar

```

### GET / Get historical market data

```
GET /api/v5/market/history-candles

```

---

## WebSocket

### Instruments channel

- ***URL Path**: `/ws/v5/public`
- ***Channel**: `instruments`

### Open interest channel

- ***Channel**: `open-interest`

### Funding rate channel

- ***Channel**: `funding-rate`

### Price limit channel

- ***Channel**: `price-limit`

### Option summary channel

- ***Channel**: `opt-summary`

### Estimated delivery/exercise/settlement price channel

- ***Channel**: `estimated-price`

### Mark price channel

- ***Channel**: `mark-price`

### Index tickers channel

- ***Channel**: `index-tickers`

### Mark price candlesticks channel

- ***URL Path**: `/ws/v5/business`
- ***Channel**: `mark-price-candle1m`, etc.

### Index candlesticks channel

- ***URL Path**: `/ws/v5/business`
- ***Channel**: `index-candle1m`, etc.

### Liquidation orders channel

- ***URL Path**: `/ws/v5/public`
- ***Channel**: `liquidation-orders`

### ADL warning channel

- ***Channel**: `adl-warning`

### Economic calendar channel

- ***URL Path**: `/ws/v5/business`
- ***Channel**: `economic-calendar`
