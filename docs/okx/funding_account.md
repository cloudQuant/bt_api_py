# Funding Account

The API endpoints of Funding Account require authentication.

## REST API

### GET / Get currencies

Retrieve a list of all currencies.

- **Rate Limit**: 6 requests per second
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/asset/currencies

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | No | Currency, e.g. `BTC` |

### GET / Get balance

Retrieve the funding account balances of all the assets and the amount that is available to transfer.

- **Rate Limit**: 6 requests per second
- **Permission**: Read

```
GET /api/v5/asset/balances

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | No | Currency, e.g. `BTC,ETH` |

### GET / Get non-tradable assets

```
GET /api/v5/asset/non-tradable-assets

```

### GET / Get account asset valuation

```
GET /api/v5/asset/asset-valuation

```

### POST / Funds transfer

Transfer funds between your funding account and trading account, or between sub-accounts.

- **Rate Limit**: 1 request per second
- **Permission**: Trade

```
POST /api/v5/asset/transfer

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `USDT` |

| amt | String | Yes | Amount to be transferred |

| from | String | Yes | Source account: `6` Funding, `18` Trading |

| to | String | Yes | Destination account: `6` Funding, `18` Trading |

| subAcct | String | No | Sub-account name |

| type | String | No | Transfer type: `0` (within account), `1` (master to sub), `2` (sub to master), etc. |

### GET / Get funds transfer state

```
GET /api/v5/asset/transfer-state

```

### GET / Asset bills details

```
GET /api/v5/asset/bills

```

### GET / Asset bills history

```
GET /api/v5/asset/bills-history

```

### GET / Get deposit address

Retrieve the deposit addresses of currencies, including previously-used addresses.

- **Rate Limit**: 6 requests per second
- **Permission**: Read

```
GET /api/v5/asset/deposit-address

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `BTC` |

### GET / Get deposit history

```
GET /api/v5/asset/deposit-history

```

### POST / Withdrawal

- **Rate Limit**: 6 requests per second
- **Permission**: Withdraw

```
POST /api/v5/asset/withdrawal

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency |

| amt | String | Yes | Withdrawal amount |

| dest | String | Yes | `3` Internal transfer, `4` On-chain withdrawal |

| toAddr | String | Yes | Verified address, email or mobile number |

| fee | String | Yes | Transaction fee |

| chain | String | Conditional | Chain name, e.g. `USDT-ERC20` |

### POST / Cancel withdrawal

```
POST /api/v5/asset/cancel-withdrawal

```

### GET / Get withdrawal history

```
GET /api/v5/asset/withdrawal-history

```

### GET / Get deposit withdraw status

```
GET /api/v5/asset/deposit-withdraw-status

```

### GET / Get exchange list (public)

```
GET /api/v5/asset/exchange-list

```

### POST / Apply for monthly statement (last year)

```
POST /api/v5/asset/monthly-statement

```

### GET / Get monthly statement (last year)

```
GET /api/v5/asset/monthly-statement

```

### GET / Get convert currencies

```
GET /api/v5/asset/convert/currencies

```

### GET / Get convert currency pair

```
GET /api/v5/asset/convert/currency-pair

```

### POST / Estimate quote

```
POST /api/v5/asset/convert/estimate-quote

```

### POST / Convert trade

```
POST /api/v5/asset/convert/trade

```

### GET / Get convert history

```
GET /api/v5/asset/convert/history

```

### GET / Get deposit payment methods

```
GET /api/v5/asset/deposit-payment-methods

```

### GET / Get withdrawal payment methods

```
GET /api/v5/asset/withdrawal-payment-methods

```

### POST / Create withdrawal order

```
POST /api/v5/asset/withdrawal-order

```

### POST / Cancel withdrawal order

```
POST /api/v5/asset/cancel-withdrawal-order

```

### GET / Get withdrawal order history

```
GET /api/v5/asset/withdrawal-order-history

```

### GET / Get withdrawal order detail

```
GET /api/v5/asset/withdrawal-order-detail

```

### GET / Get deposit order history

```
GET /api/v5/asset/deposit-order-history

```

### GET / Get deposit order detail

```
GET /api/v5/asset/deposit-order-detail

```

### GET / Get buy/sell currencies

```
GET /api/v5/asset/buy-sell/currencies

```

### GET / Get buy/sell currency pair

```
GET /api/v5/asset/buy-sell/currency-pair

```

### GET / Get buy/sell quote

```
GET /api/v5/asset/buy-sell/quote

```

### POST / Buy/sell trade

```
POST /api/v5/asset/buy-sell/trade

```

### GET / Get buy/sell trade history

```
GET /api/v5/asset/buy-sell/history

```

---

## WebSocket

### Deposit info channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `deposit-info`

Subscribe Example:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "deposit-info"
  }]
}

```

### Withdrawal info channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `withdrawal-info`
