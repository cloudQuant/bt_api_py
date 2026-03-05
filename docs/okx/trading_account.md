# Trading Account

The API endpoints of Trading Account require authentication.

## REST API

### Get instruments

Retrieve available instruments info of current account.

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: User ID + Instrument Type
- **Permission**: Read

```
GET /api/v5/account/instruments

```
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

```

### Get balance

Retrieve a list of assets (with non-zero balance), remaining balance, and available amount in the trading account.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/balance

```
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

```
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

```

### Get positions

Retrieve information on your positions.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/positions

```
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

```

### Get positions history

Retrieve the updated position data for the last 3 months.

- **Rate Limit**: 10 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/positions-history

```
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

```
GET /api/v5/account/account-position-risk

```

### Get bills details (last 7 days)

Retrieve the bills of the account. Pagination supported, sorted with most recent first.

- **Rate Limit**: 5 requests per second
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/bills

```

### Get bills details (last 3 months)

- **Rate Limit**: 5 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/bills-archive

```

### Apply bills details (since 2021)

```
POST /api/v5/account/bills-history-archive

```

### Get bills details (since 2021)

```
GET /api/v5/account/bills-history-archive

```

### Get account configuration

Retrieve current account configuration.

- **Rate Limit**: 5 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/account/config

```
Python Example:

```python
accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)
result = accountAPI.get_account_config()
print(result)

```
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

```
POST /api/v5/account/set-position-mode

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| posMode | String | Yes | `long_short_mode` or `net_mode` |

### Set leverage

- **Rate limit**: 20 requests per 2 seconds
- **Permission**: Trade

```
POST /api/v5/account/set-leverage

```

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

```
GET /api/v5/account/max-size

```

### Get maximum available balance/equity

```
GET /api/v5/account/max-avail-size

```

### Increase/decrease margin

```
POST /api/v5/account/position/margin-balance

```

### Get leverage

- **Rate Limit**: 20 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/account/leverage-info

```

### Get leverage estimated info

```
GET /api/v5/account/adjust-leverage-info

```

### Get the maximum loan of instrument

```
GET /api/v5/account/max-loan

```

### Get fee rates

- **Rate Limit**: 5 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/account/trade-fee

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| instType | String | Yes | `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |

| instId | String | No | Instrument ID |

| uly | String | No | Underlying |

| instFamily | String | No | Instrument family |

### Get interest accrued data

```
GET /api/v5/account/interest-accrued

```

### Get interest rate

```
GET /api/v5/account/interest-rate

```

### Set fee type

```
POST /api/v5/account/set-fee-type

```

### Set greeks (PA/BS)

```
POST /api/v5/account/set-greeks

```

### Isolated margin trading settings

```
POST /api/v5/account/set-isolated-mode

```

### Get maximum withdrawals

```
GET /api/v5/account/max-withdrawal

```

### Get account risk state

Only applicable to Portfolio margin account.

```
GET /api/v5/account/risk-state

```

### Get borrow interest and limit

```
GET /api/v5/account/interest-limits

```

### Manual borrow / repay

```
POST /api/v5/account/borrow-repay

```

### Set auto repay

```
POST /api/v5/account/set-auto-repay

```

### Get borrow/repay history

```
GET /api/v5/account/borrow-repay-history

```

### Position builder (new)

```
POST /api/v5/account/position-builder

```

### Position builder trend graph

```
POST /api/v5/account/position-builder-trend

```

### Set risk offset amount

```
POST /api/v5/account/set-riskOffset-amt

```

### Get Greeks

```
GET /api/v5/account/greeks

```

### Get PM position limitation

```
GET /api/v5/account/position-tiers

```

### Activate option

```
POST /api/v5/account/activate-option

```

### Set auto loan

Only applicable to Multi-currency margin and Portfolio margin.

```
POST /api/v5/account/set-auto-loan

```

### Preset account mode switch

```
POST /api/v5/account/account-level-switch-preset

```

### Precheck account mode switch

```
GET /api/v5/account/account-level-switch-precheck

```

### Set account mode

```
POST /api/v5/account/set-account-level

```

### Set collateral assets

```
POST /api/v5/account/set-collateral-assets

```

### Get collateral assets

```
GET /api/v5/account/collateral-assets

```

### Reset MMP Status

```
POST /api/v5/account/mmp-reset

```

### Set MMP

```
POST /api/v5/account/mmp-config

```

### GET MMP Config

```
GET /api/v5/account/mmp-config

```

### Move positions

```
POST /api/v5/account/position/move-positions

```

### Get move positions history

```
GET /api/v5/account/position/move-positions-history

```

### Set auto earn

```
POST /api/v5/account/set-auto-earn

```

### Set settle currency

```
POST /api/v5/account/set-settle-currency

```

### Set trading config

```
POST /api/v5/account/set-trading-config

```

### Precheck set delta neutral

```
POST /api/v5/account/set-delta-neutral-precheck

```

---

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

```
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

```

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
