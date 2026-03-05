# Trading Statistics

Public endpoints for trading data statistics. No authentication required.

## REST API

### GET / Get support coin

Retrieve the currencies supported by the trading data endpoints.

```
GET /api/v5/rubik/stat/trading-data/support-coin

```

### GET / Get contract open interest history

```
GET /api/v5/rubik/stat/contracts/open-interest-history

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Yes | Instrument ID, e.g. `BTC-USDT-SWAP` |

| period | String | No | `5m`, `1H`, `1D` (default `5m`) |

### GET / Get taker volume

Retrieve the taker volume for both buyers and sellers.

```
GET /api/v5/rubik/stat/taker-volume

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency |

| instType | String | Yes | `SPOT` |

| begin | String | No | Begin timestamp |

| end | String | No | End timestamp |

| period | String | No | `5m`, `1H`, `1D` |

### GET / Get contract taker volume

```
GET /api/v5/rubik/stat/taker-volume-contract

```

### GET / Get margin long/short ratio

```
GET /api/v5/rubik/stat/margin/loan-ratio

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `BTC` |

| begin | String | No | Begin timestamp |

| end | String | No | End timestamp |

| period | String | No | `5m`, `1H`, `1D` |

### GET / Get top traders contract long/short ratio

```
GET /api/v5/rubik/stat/contracts/long-short-account-ratio

```

### GET / Get top traders contract long/short ratio (by position)

```
GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader

```

### GET / Get contract long/short ratio

```
GET /api/v5/rubik/stat/contracts/open-interest-volume-ratio

```

### GET / Get long/short ratio

```
GET /api/v5/rubik/stat/option/open-interest-volume-ratio

```

### GET / Get contracts open interest and volume

```
GET /api/v5/rubik/stat/contracts/open-interest-volume

```

### GET / Get options open interest and volume

```
GET /api/v5/rubik/stat/option/open-interest-volume

```

### GET / Get put/call ratio

```
GET /api/v5/rubik/stat/option/open-interest-volume-ratio

```

### GET / Get open interest and volume (expiry)

```
GET /api/v5/rubik/stat/option/open-interest-volume-expiry

```

### GET / Get open interest and volume (strike)

```
GET /api/v5/rubik/stat/option/open-interest-volume-strike

```

### GET / Get taker flow

```
GET /api/v5/rubik/stat/option/taker-block-volume

```
