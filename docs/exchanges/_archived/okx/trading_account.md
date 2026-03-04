# Trading Account

The API endpoints of Trading Account require authentication.

## REST API

### Get instruments

Retrieve available instruments info of current account.

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: User ID + Instrument Type
- **Permission**: Read

```bash
GET /api/v5/account/instruments

```bash
Request Parameters:

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| uly | String | No | Underlying. Required for `FUTURES`/`SWAP`/`OPTION` |

| instFamily | String | No | Instrument family. Required for `OPTION` |

| instId | String | No | Instrument ID |

Python Example:

```python
import okx.Account as Account

apikey = "YOUR_API_KEY"
secretkey = "YOUR_SECRET_KEY"
passphrase = "YOUR_PASSPHRASE"
flag = "1"  # Production trading: 0, Demo trading: 1

accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
result = accountAPI.get_instruments(instType="SPOT")
print(result)

```bash

### Get balance

Retrieve a list of assets (with non-zero balance), remaining balance, and available amount in the trading account.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/balance

```bash
Request Parameters:

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | No | Currency, e.g. `BTC` or `BTC,ETH` |

Python Example:

```python
import okx.Account as Account

accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
result = accountAPI.get_account_balance()
print(result)

```bash
Response Example:

```json
{
  "code": "0",
  "data": [{
    "adjEq": "55415.624719833286",
    "availEq": "55415.624719833286",
    "totalEq": "55837.43556134779",
    "details": [{
      "availBal": "4834.317093622894",
      "availEq": "4834.3170936228935",
      "cashBal": "4850.435693622894",
      "ccy": "USDT",
      "eq": "4992.890093622894",
      "eqUsd": "4991.542013297616",
      "frozenBal": "158.573",
      "interest": "0",
      "liab": "0",
      "upl": "-7.545600000000006"
    }]
  }],
  "msg": ""
}

```bash

### Get positions

Retrieve information on your positions.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/positions

```bash
Request Parameters:

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | No | `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| instId | String | No | Instrument ID, e.g. `BTC-USDT-SWAP` |

| posId | String | No | Position ID |

Python Example:

```python
accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
result = accountAPI.get_positions()
print(result)

```bash

### Get positions history

Retrieve the updated position data for the last 3 months.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/positions-history

```bash
Request Parameters:

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | No | `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| instId | String | No | Instrument ID |

| mgnMode | String | No | `cross`, `isolated` |

| type | String | No | Close type: `1` Partially closed, `2` Fully closed, `3` Liquidation, `4` Partial liquidation, `5` ADL, `6` Auto-deleveraging |

| after | String | No | Pagination |

| before | String | No | Pagination |

| limit | String | No | Default 100, max 100 |

### Get account and position risk

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/account-position-risk

```bash

### Get bills details (last 7 days)

Retrieve the bills of the account. Pagination supported, sorted with most recent first.

- **Rate Limit**: 5 requests per second
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/bills

```bash

### Get bills details (last 3 months)

- **Rate Limit**: 5 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/bills-archive

```bash

### Apply bills details (since 2021)

```bash
POST /api/v5/account/bills-history-archive

```bash

### Get bills details (since 2021)

```bash
GET /api/v5/account/bills-history-archive

```bash

### Get account configuration

Retrieve current account configuration.

- **Rate Limit**: 5 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/account/config

```bash
Python Example:

```python
accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
result = accountAPI.get_account_config()
print(result)

```bash
Key Response Fields:

| Field | Description |

|-------|-------------|

| acctLv | Account level: `1` Spot mode, `2` Futures mode, `3` Multi-currency margin, `4` Portfolio margin |

| posMode | Position mode: `long_short_mode`, `net_mode` |

| autoLoan | Auto-loan: `true`, `false` |

| greeksType | Greeks display: `PA` (price), `BS` (Black-Scholes) |

| uid | User ID |

| mainUid | Main account UID |

| level | Fee level, e.g. `Lv1` |

### Set position mode

- **Rate Limit**: 5 requests per 2 seconds
- **Permission**: Trade

```bash
POST /api/v5/account/set-position-mode

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| posMode | String | Yes | `long_short_mode` or `net_mode` |

### Set leverage

- **Rate limit**: 20 requests per 2 seconds
- **Permission**: Trade

```bash
POST /api/v5/account/set-leverage

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instId | String | Conditional | Instrument ID |

| ccy | String | Conditional | Currency |

| lever | String | Yes | Leverage |

| mgnMode | String | Yes | `cross` or `isolated` |

| posSide | String | Conditional | `long`, `short` (required for isolated long/short mode) |

### Get maximum order quantity

- **Rate Limit**: 20 requests per 2 seconds
- **Permission**: Read

```bash
GET /api/v5/account/max-size

```bash

### Get maximum available balance/equity

```bash
GET /api/v5/account/max-avail-size

```bash

### Increase/decrease margin

```bash
POST /api/v5/account/position/margin-balance

```bash

### Get leverage

- **Rate Limit**: 20 requests per 2 seconds
- **Permission**: Read

```bash
GET /api/v5/account/leverage-info

```bash

### Get leverage estimated info

```bash
GET /api/v5/account/adjust-leverage-info

```bash

### Get the maximum loan of instrument

```bash
GET /api/v5/account/max-loan

```bash

### Get fee rates

- **Rate Limit**: 5 requests per 2 seconds
- **Permission**: Read

```bash
GET /api/v5/account/trade-fee

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| instId | String | No | Instrument ID |

| uly | String | No | Underlying |

| instFamily | String | No | Instrument family |

### Get interest accrued data

```bash
GET /api/v5/account/interest-accrued

```bash

### Get interest rate

```bash
GET /api/v5/account/interest-rate

```bash

### Set fee type

```bash
POST /api/v5/account/set-fee-type

```bash

### Set greeks (PA/BS)

```bash
POST /api/v5/account/set-greeks

```bash

### Isolated margin trading settings

```bash
POST /api/v5/account/set-isolated-mode

```bash

### Get maximum withdrawals

```bash
GET /api/v5/account/max-withdrawal

```bash

### Get account risk state

Only applicable to Portfolio margin account.

```bash
GET /api/v5/account/risk-state

```bash

### Get borrow interest and limit

```bash
GET /api/v5/account/interest-limits

```bash

### Manual borrow / repay

```bash
POST /api/v5/account/borrow-repay

```bash

### Set auto repay

```bash
POST /api/v5/account/set-auto-repay

```bash

### Get borrow/repay history

```bash
GET /api/v5/account/borrow-repay-history

```bash

### Position builder (new)

```bash
POST /api/v5/account/position-builder

```bash

### Position builder trend graph

```bash
POST /api/v5/account/position-builder-trend

```bash

### Set risk offset amount

```bash
POST /api/v5/account/set-riskOffset-amt

```bash

### Get Greeks

```bash
GET /api/v5/account/greeks

```bash

### Get PM position limitation

```bash
GET /api/v5/account/position-tiers

```bash

### Activate option

```bash
POST /api/v5/account/activate-option

```bash

### Set auto loan

Only applicable to Multi-currency margin and Portfolio margin.

```bash
POST /api/v5/account/set-auto-loan

```bash

### Preset account mode switch

```bash
POST /api/v5/account/account-level-switch-preset

```bash

### Precheck account mode switch

```bash
GET /api/v5/account/account-level-switch-precheck

```bash

### Set account mode

```bash
POST /api/v5/account/set-account-level

```bash

### Set collateral assets

```bash
POST /api/v5/account/set-collateral-assets

```bash

### Get collateral assets

```bash
GET /api/v5/account/collateral-assets

```bash

### Reset MMP Status

```bash
POST /api/v5/account/mmp-reset

```bash

### Set MMP

```bash
POST /api/v5/account/mmp-config

```bash

### GET MMP Config

```bash
GET /api/v5/account/mmp-config

```bash

### Move positions

```bash
POST /api/v5/account/position/move-positions

```bash

### Get move positions history

```bash
GET /api/v5/account/position/move-positions-history

```bash

### Set auto earn

```bash
POST /api/v5/account/set-auto-earn

```bash

### Set settle currency

```bash
POST /api/v5/account/set-settle-currency

```bash

### Set trading config

```bash
POST /api/v5/account/set-trading-config

```bash

### Precheck set delta neutral

```bash
POST /api/v5/account/set-delta-neutral-precheck

```bash

- --

## WebSocket

### Account channel

Retrieve account information. Data pushed on events (placing/canceling/execution).

- **URL Path**: `/ws/v5/private` (requires login)
- **Channel**: `account`

Subscribe Example:

```json
{
  "id": "1512",
  "op": "subscribe",
  "args": [{
    "channel": "account",
    "ccy": "BTC"
  }]
}

```bash
Python Example:

```python
import asyncio
from okx.websocket.WsPrivateAsync import WsPrivateAsync

def callbackFunc(message):
    print(message)

async def main():
    ws = WsPrivateAsync(
        apiKey="YOUR_API_KEY",
        passphrase="YOUR_PASSPHRASE",
        secretKey="YOUR_SECRET_KEY",
        url="wss://ws.okx.com:8443/ws/v5/private",
        useServerTime=False
    )
    await ws.start()
    args = [{"channel": "account", "ccy": "BTC"}]
    await ws.subscribe(args, callback=callbackFunc)
    await asyncio.sleep(10)
    await ws.unsubscribe(args, callback=callbackFunc)

asyncio.run(main())

```bash

### Positions channel

Retrieve position information. Data pushed on events.

- **URL Path**: `/ws/v5/private`
- **Channel**: `positions`

### Balance and position channel

- **URL Path**: `/ws/v5/private`
- **Channel**: `balance_and_position`

### Position risk warning

- **URL Path**: `/ws/v5/private`
- **Channel**: `liquidation-warning`

### Account greeks channel

- **URL Path**: `/ws/v5/private`
- **Channel**: `account-greeks`
