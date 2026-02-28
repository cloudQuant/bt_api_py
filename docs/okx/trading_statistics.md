# Trading Statistics

Public endpoints for trading data statistics. No authentication required.

## REST API

### GET / Get support coin

Retrieve the currencies supported by the trading data endpoints.

```bash
GET /api/v5/rubik/stat/trading-data/support-coin

```bash

### GET / Get contract open interest history

```bash
GET /api/v5/rubik/stat/contracts/open-interest-history

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID, e.g. `BTC-USDT-SWAP` |

| period | String | No | `5m`, `1H`, `1D` (default `5m`) |

### GET / Get taker volume

Retrieve the taker volume for both buyers and sellers.

```bash
GET /api/v5/rubik/stat/taker-volume

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency |

| instType | String | Yes | `SPOT` |

| begin | String | No | Begin timestamp |

| end | String | No | End timestamp |

| period | String | No | `5m`, `1H`, `1D` |

### GET / Get contract taker volume

```bash
GET /api/v5/rubik/stat/taker-volume-contract

```bash

### GET / Get margin long/short ratio

```bash
GET /api/v5/rubik/stat/margin/loan-ratio

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `BTC` |

| begin | String | No | Begin timestamp |

| end | String | No | End timestamp |

| period | String | No | `5m`, `1H`, `1D` |

### GET / Get top traders contract long/short ratio

```bash
GET /api/v5/rubik/stat/contracts/long-short-account-ratio

```bash

### GET / Get top traders contract long/short ratio (by position)

```bash
GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader

```bash

### GET / Get contract long/short ratio

```bash
GET /api/v5/rubik/stat/contracts/open-interest-volume-ratio

```bash

### GET / Get long/short ratio

```bash
GET /api/v5/rubik/stat/option/open-interest-volume-ratio

```bash

### GET / Get contracts open interest and volume

```bash
GET /api/v5/rubik/stat/contracts/open-interest-volume

```bash

### GET / Get options open interest and volume

```bash
GET /api/v5/rubik/stat/option/open-interest-volume

```bash

### GET / Get put/call ratio

```bash
GET /api/v5/rubik/stat/option/open-interest-volume-ratio

```bash

### GET / Get open interest and volume (expiry)

```bash
GET /api/v5/rubik/stat/option/open-interest-volume-expiry

```bash

### GET / Get open interest and volume (strike)

```bash
GET /api/v5/rubik/stat/option/open-interest-volume-strike

```bash

### GET / Get taker flow

```bash
GET /api/v5/rubik/stat/option/taker-block-volume

```bash
