# Funding Account

The API endpoints of Funding Account require authentication.

## REST API

### GET / Get currencies

Retrieve a list of all currencies.

- **Rate Limit**: 6 requests per second
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/asset/currencies

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | No | Currency, e.g. `BTC` |

### GET / Get balance

Retrieve the funding account balances of all the assets and the amount that is available to transfer.

- **Rate Limit**: 6 requests per second
- **Permission**: Read

```bash
GET /api/v5/asset/balances

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | No | Currency, e.g. `BTC,ETH` |

### GET / Get non-tradable assets

```bash
GET /api/v5/asset/non-tradable-assets

```bash

### GET / Get account asset valuation

```bash
GET /api/v5/asset/asset-valuation

```bash

### POST / Funds transfer

Transfer funds between your funding account and trading account, or between sub-accounts.

- **Rate Limit**: 1 request per second
- **Permission**: Trade

```bash
POST /api/v5/asset/transfer

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `USDT` |

| amt | String | Yes | Amount to be transferred |

| from | String | Yes | Source account: `6` Funding, `18` Trading |

| to | String | Yes | Destination account: `6` Funding, `18` Trading |

| subAcct | String | No | Sub-account name |

| type | String | No | Transfer type: `0` (within account), `1` (master to sub), `2` (sub to master), etc. |

### GET / Get funds transfer state

```bash
GET /api/v5/asset/transfer-state

```bash

### GET / Asset bills details

```bash
GET /api/v5/asset/bills

```bash

### GET / Asset bills history

```bash
GET /api/v5/asset/bills-history

```bash

### GET / Get deposit address

Retrieve the deposit addresses of currencies, including previously-used addresses.

- **Rate Limit**: 6 requests per second
- **Permission**: Read

```bash
GET /api/v5/asset/deposit-address

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency, e.g. `BTC` |

### GET / Get deposit history

```bash
GET /api/v5/asset/deposit-history

```bash

### POST / Withdrawal

- **Rate Limit**: 6 requests per second
- **Permission**: Withdraw

```bash
POST /api/v5/asset/withdrawal

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency |

| amt | String | Yes | Withdrawal amount |

| dest | String | Yes | `3` Internal transfer, `4` On-chain withdrawal |

| toAddr | String | Yes | Verified address, email or mobile number |

| fee | String | Yes | Transaction fee |

| chain | String | Conditional | Chain name, e.g. `USDT-ERC20` |

### POST / Cancel withdrawal

```bash
POST /api/v5/asset/cancel-withdrawal

```bash

### GET / Get withdrawal history

```bash
GET /api/v5/asset/withdrawal-history

```bash

### GET / Get deposit withdraw status

```bash
GET /api/v5/asset/deposit-withdraw-status

```bash

### GET / Get exchange list (public)

```bash
GET /api/v5/asset/exchange-list

```bash

### POST / Apply for monthly statement (last year)

```bash
POST /api/v5/asset/monthly-statement

```bash

### GET / Get monthly statement (last year)

```bash
GET /api/v5/asset/monthly-statement

```bash

### GET / Get convert currencies

```bash
GET /api/v5/asset/convert/currencies

```bash

### GET / Get convert currency pair

```bash
GET /api/v5/asset/convert/currency-pair

```bash

### POST / Estimate quote

```bash
POST /api/v5/asset/convert/estimate-quote

```bash

### POST / Convert trade

```bash
POST /api/v5/asset/convert/trade

```bash

### GET / Get convert history

```bash
GET /api/v5/asset/convert/history

```bash

### GET / Get deposit payment methods

```bash
GET /api/v5/asset/deposit-payment-methods

```bash

### GET / Get withdrawal payment methods

```bash
GET /api/v5/asset/withdrawal-payment-methods

```bash

### POST / Create withdrawal order

```bash
POST /api/v5/asset/withdrawal-order

```bash

### POST / Cancel withdrawal order

```bash
POST /api/v5/asset/cancel-withdrawal-order

```bash

### GET / Get withdrawal order history

```bash
GET /api/v5/asset/withdrawal-order-history

```bash

### GET / Get withdrawal order detail

```bash
GET /api/v5/asset/withdrawal-order-detail

```bash

### GET / Get deposit order history

```bash
GET /api/v5/asset/deposit-order-history

```bash

### GET / Get deposit order detail

```bash
GET /api/v5/asset/deposit-order-detail

```bash

### GET / Get buy/sell currencies

```bash
GET /api/v5/asset/buy-sell/currencies

```bash

### GET / Get buy/sell currency pair

```bash
GET /api/v5/asset/buy-sell/currency-pair

```bash

### GET / Get buy/sell quote

```bash
GET /api/v5/asset/buy-sell/quote

```bash

### POST / Buy/sell trade

```bash
POST /api/v5/asset/buy-sell/trade

```bash

### GET / Get buy/sell trade history

```bash
GET /api/v5/asset/buy-sell/history

```bash

- --

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

```bash

### Withdrawal info channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `withdrawal-info`
