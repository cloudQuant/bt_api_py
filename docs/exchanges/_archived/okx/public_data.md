# Public Data

Public data endpoints do not require authentication.

## REST API

### GET / Get instruments

Retrieve a list of instruments with open contracts.

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: IP + Instrument Type

```bash
GET /api/v5/public/instruments

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| uly | String | Conditional | Underlying. Required for `FUTURES`/`SWAP`/`OPTION` |

| instFamily | String | No | Instrument family |

| instId | String | No | Instrument ID |

### GET / Get estimated delivery/exercise price

```bash
GET /api/v5/public/estimated-price

```bash

### GET / Get delivery/exercise history

```bash
GET /api/v5/public/delivery-exercise-history

```bash

### GET / Get estimated future settlement price

```bash
GET /api/v5/public/estimated-settlement-price

```bash

### GET / Get futures settlement history

```bash
GET /api/v5/public/settlement-history

```bash

### GET / Get funding rate

Retrieve funding rate.

- **Rate Limit**: 20 requests per 2 seconds

```bash
GET /api/v5/public/funding-rate

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID, e.g. `BTC-USDT-SWAP` |

### GET / Get funding rate history

```bash
GET /api/v5/public/funding-rate-history

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID |

| before | String | No | Pagination |

| after | String | No | Pagination |

| limit | String | No | Default 100, max 100 |

### GET / Get open interest

```bash
GET /api/v5/public/open-interest

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `SWAP`, `FUTURES`, `OPTION` |

| uly | String | No | Underlying |

| instFamily | String | No | Instrument family |

| instId | String | No | Instrument ID |

### GET / Get limit price

```bash
GET /api/v5/public/price-limit

```bash

### GET / Get option market data

```bash
GET /api/v5/public/opt-summary

```bash

### GET / Get discount rate and interest-free quota

```bash
GET /api/v5/public/discount-rate-interest-free-quota

```bash

### GET / Get system time

```bash
GET /api/v5/public/time

```bash

### GET / Get mark price

```bash
GET /api/v5/public/mark-price

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| uly | String | No | Underlying |

| instFamily | String | No | Instrument family |

| instId | String | No | Instrument ID |

### GET / Get position tiers

```bash
GET /api/v5/public/position-tiers

```bash

### GET / Get interest rate and loan quota

```bash
GET /api/v5/public/interest-rate-loan-quota

```bash

### GET / Get underlying

```bash
GET /api/v5/public/underlying

```bash

### GET / Get security fund

```bash
GET /api/v5/public/insurance-fund

```bash

### GET / Unit convert

```bash
GET /api/v5/public/convert-contract-coin

```bash

### GET / Get option tick bands

```bash
GET /api/v5/public/instrument-tick-bands

```bash

### GET / Get premium history

```bash
GET /api/v5/public/premium-history

```bash

### GET / Get index tickers

```bash
GET /api/v5/market/index-tickers

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| quoteCcy | String | Conditional | Quote currency (e.g. `USD`) |

| instId | String | Conditional | Index, e.g. `BTC-USD` |

### GET / Get index candlesticks

```bash
GET /api/v5/market/index-candles

```bash

### GET / Get index candlesticks history

```bash
GET /api/v5/market/history-index-candles

```bash

### GET / Get mark price candlesticks

```bash
GET /api/v5/market/mark-price-candles

```bash

### GET / Get mark price candlesticks history

```bash
GET /api/v5/market/history-mark-price-candles

```bash

### GET / Get exchange rate

```bash
GET /api/v5/market/exchange-rate

```bash

### GET / Get index components

```bash
GET /api/v5/market/index-components

```bash

### GET / Get economic calendar data

```bash
GET /api/v5/public/economic-calendar

```bash

### GET / Get historical market data

```bash
GET /api/v5/market/history-candles

```bash

- --

## WebSocket

### Instruments channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `instruments`

### Open interest channel

- **Channel**: `open-interest`

### Funding rate channel

- **Channel**: `funding-rate`

### Price limit channel

- **Channel**: `price-limit`

### Option summary channel

- **Channel**: `opt-summary`

### Estimated delivery/exercise/settlement price channel

- **Channel**: `estimated-price`

### Mark price channel

- **Channel**: `mark-price`

### Index tickers channel

- **Channel**: `index-tickers`

### Mark price candlesticks channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `mark-price-candle1m`, etc.

### Index candlesticks channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `index-candle1m`, etc.

### Liquidation orders channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `liquidation-orders`

### ADL warning channel

- **Channel**: `adl-warning`

### Economic calendar channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `economic-calendar`
