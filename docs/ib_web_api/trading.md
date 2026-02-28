# IBKR Trading Web API

> Source: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/>
> Last Updated: 2026-02-26

## Table of Contents

- [Getting Started](#getting-started)
- [Trading Access](#trading-access)
- [API Support](#api-support)
- [Usage and Availability](#usage-and-availability)
- [Trading Sessions](#trading-sessions)
- [Instrument Discovery](#instrument-discovery)
- [Market Data](#market-data)
- [Orders](#orders)
- [Portfolio and Positions](#portfolio-and-positions)
- [FYIs and Alerts](#fyis-and-alerts)

- --

## Getting Started

Clients with **fully open and funded accounts**may use the Web API's Trading features to trade their accounts immediately, without any onboarding or approval process.

The Web API's Trading functionality is accessible via several authentication methods:

- OAuth 2.0 (beta)
- OAuth 1.0a
- SSO (for Financial Advisors and Introducing Brokers)
- Client Portal Gateway (for individual clients)

- --

## Trading Access

### For Organizations

Enterprise and Institutional clients have several authorization methods:

| Method | First-Party | Third-Party | Features |

|--------|-------------|-------------|----------|

| OAuth 2.0 (beta) | Yes | Yes | Account management + Trading |

| OAuth 1.0a | Yes | Yes | Trading only |

| SSO | Yes | No | Account management + Trading |

### For Individuals

Trading requires:

- IBKR username and password
- **IBKR Pro**account type (fully open and funded)
- Can access both live and simulated paper accounts

### For Third Parties

Third-party developers (vendors with no formal relationship to IB clients):

- Must receive Compliance approval
- Only OAuth 1.0a is currently available
- Onboarding process takes approximately**8-14 weeks**

- --

## API Support

Contact options:

- Create a Support Ticket
- Chat Live with API Support
- Get Help by Phone

Troubleshooting checklist before contacting support:

- [ ] Does the issue persist in other platforms (TWS, Client Portal)?
- [ ] When was the issue first observed?
- [ ] Do similar requests yield expected responses?
- [ ] Did the request include all required parameters?

- --

## Usage and Availability

### Scheduled Server Maintenance

The Web API is accessible **24 hours a day during weekdays**, with maintenance only on Saturday evenings.

Brokerage functionality (`/iserver` endpoints) is briefly unavailable each evening:

| Region | Maintenance Onset |

|--------|-------------------|

| North America (NY, Chicago) | 01:00 US/Eastern |

| Europe | 01:00 CEST |

| Asia | 01:00 HKT |

### Pacing Limitations

- *Global rate limit**: 50 requests per second per authenticated username

- *CP Gateway users**: 10 requests per second

When rate limits are exceeded, the API returns `429 Too Many Requests`. Violator IPs may be penalized for 10 minutes.

#### Per-Endpoint Request Rate Limits

| Endpoint | Method | Limit |

|----------|--------|-------|

| `/iserver/marketdata/snapshot` | GET | 10 req/s |

| `/iserver/scanner/params` | GET | 1 req/15 min |

| `/iserver/scanner/run` | POST | 1 req/sec |

| `/iserver/trades` | GET | 1 req/5 sec |

| `/iserver/orders` | GET | 1 req/5 sec |

| `/portfolio/accounts` | GET | 1 req/5 sec |

| `/pa/performance` | POST | 1 req/15 min |

- --

## Trading Sessions

A **brokerage session**is associated with an IB username and permits access to:

- Trading functionality
- Market data consumption
- All `/iserver` endpoints

A single username can only have**one brokerage session active at a time**across all IB platforms.

### Session Types

1.**Read-Only Session**- Required for any Web API request, permits access to non-`/iserver` endpoints
2.**Brokerage Session**- Established after read-only, permits trading and market data via `/iserver` endpoints

- --

## Instrument Discovery

The Web API requires IB's**Contract ID (`conid`)** to uniquely specify instruments.

### Contract IDs for Equities

```bash
GET /trsrv/stocks?symbols=AAPL

```bash
Response:

```json
{
  "AAPL": [
    {
      "name": "APPLE INC",
      "assetClass": "STK",
      "contracts": [
        {
          "conid": 265598,
          "exchange": "NASDAQ",
          "isUS": true
        }
      ]
    }
  ]
}

```bash

### Contract IDs for Futures

```bash
GET /trsrv/futures?symbols=ES

```bash

### Finding Options Chains

- *Step 1**: Get underlier conid and contract months

```bash
GET /iserver/secdef/search?symbol=AAPL&secType=STK

```bash

- *Step 2**: Get valid strike values

```bash
GET /iserver/secdef/strikes?conid=265598&exchange=SMART&sectype=OPT&month=OCT24

```bash
Response:

```json
{
  "call": [212.5, 215.0, 217.5, 220.0],
  "put": [212.5, 215.0, 217.5, 220.0]
}

```bash

- *Step 3**: Get option contract conids

```bash
GET /iserver/secdef/info?conid=265598&exchange=SMART&sectype=OPT&month=OCT24&strike=217.5

```bash

### Finding Event Contracts

Event Contracts are modeled as options (ForecastEx) or futures options (CME Group).

#### CME Group Workflow

```bash
GET /iserver/secdef/search?symbol=NQ&secType=IND

```bash
Then filter by `tradingClass` prefix "EC" for Event Contracts.

#### ForecastEx Workflow

```bash
GET /iserver/secdef/search?symbol=FF
GET /iserver/secdef/strikes?conid={index_conid}&exchange=FORECASTX&sectype=OPT&month=SEP24
GET /iserver/secdef/info?conid={index_conid}&exchange=FORECASTX&sectype=OPT&month=SEP24&strike=3.125

```bash

- --

## Market Data

### Top-of-Book Snapshots

Required:

- Contract ID (`conid`)
- Field identifiers for desired data points
- Username with market data subscriptions
- Brokerage session

- *Pre-flight request** (must be made first to start streaming):

```bash
GET /iserver/marketdata/snapshot?conids=265598,8314&fields=31,7059,84,88,86,85

```bash
First response (no data yet):

```json
[
  {"conid": 265598, "conidEx": "265598"},
  {"conid": 8314, "conidEx": "8314"}
]

```bash
Subsequent requests return data:

```json
[
  {
    "31": "168.42",
    "84": "168.41",
    "85": "600",
    "86": "168.42",
    "88": "1,300",
    "7059": "100",
    "conid": 265598
  }
]

```bash

- *Common field tags**:
- `31` - Last price
- `84` - Bid
- `85` - Bid size
- `86` - Ask
- `88` - Ask size
- `7059` - Volume

### Streaming Top-of-Book Data

Via WebSocket:

```bash
smd+CONID+{"fields":["field_1","field_2",...,"field_n"]}

```bash
Example:

```bash
smd+8314+{"fields":["31","84","85","86","88","7059"]}

```bash
Cancel stream:

```bash
umd+CONID+{}

```bash

- --

## Orders

### New Order

- *Required values**:
- Account ID
- Contract ID (`conid`)
- Order type (`orderType`)
- Side (`BUY` or `SELL`)
- Time in force (`tif`)
- Quantity

```bash
POST /iserver/account/{accountId}/orders

```bash
Request body:

```json
[
  {
    "conid": 265598,
    "side": "BUY",
    "orderType": "LMT",
    "price": 165,
    "quantity": 100,
    "tif": "DAY"
  }
]

```bash
Response:

```json
{
  "order_id": "987654",
  "order_status": "Submitted",
  "encrypt_message": "1"
}

```bash

### Order Reply Messages

Some orders require confirmation before execution:

```json
[
  {
    "id": "07a13a5a-4a48-44a5-bb25-5ab37b79186c",
    "message": ["Order price exceeds constraint. Are you sure?"],
    "messageIds": ["o163"]
  }
]

```bash
Confirm the order:

```bash
POST /iserver/reply/{messageId}
{"confirmed": true}

```bash

### Order Reply Suppression

Suppress specific message types for the session:

```bash
POST /iserver/questions/suppress
{"messageIds": ["o163"]}

```bash
Reset suppression:

```bash
POST /iserver/questions/suppress/reset

```bash

### Modifying Orders

```bash
POST /iserver/account/{accountId}/order/{orderId}

```bash

- *Important**: Must include ALL original order attributes with modified values:

```json
{
  "conid": 265598,
  "side": "BUY",
  "orderType": "LMT",
  "price": 170,
  "quantity": 100,
  "tif": "DAY"
}

```bash

### Canceling Orders

```bash
DELETE /iserver/account/{accountId}/order/{orderId}

```bash
Response:

```json
{
  "msg": "Request was submitted",
  "order_id": 987654
}

```bash

### Bracket Orders

Submit parent and child orders together:

```json
{
  "orders": [
    {
      "acctId": "U1234567",
      "conid": 265598,
      "cOID": "Parent",
      "orderType": "MKT",
      "side": "Buy",
      "quantity": 50
    },
    {
      "acctId": "U1234567",
      "conid": 265598,
      "orderType": "STP",
      "side": "Sell",
      "price": 157.30,
      "quantity": 50,
      "parentId": "Parent"
    },
    {
      "acctId": "U1234567",
      "conid": 265598,
      "orderType": "LMT",
      "side": "Sell",
      "price": 157.00,
      "quantity": 50,
      "parentId": "Parent"
    }
  ]
}

```bash

### Combo/Spread Orders

Use `conidex` field instead of `conid`:

Format: `{spread_conid};;;{leg_conid1}/{ratio},{leg_conid2}/{ratio}`

- *Currency Spread ConIDs**:

| Currency | Spread ConID |

|----------|--------------|

| USD | 28812380 |

| EUR | 61227069 |

| GBP | 58666491 |

| JPY | 61227069 |

| CAD | 61227082 |

| AUD | 61227077 |

Example for US stock combo:

```bash
28812380;;;265598/1,-265599/1

```bash

### Monitoring Live Orders

```bash
GET /iserver/account/orders?filters=filled&force=true&accountId=U1234567

```bash

- --

## Portfolio and Positions

### Querying Accounts

```bash
GET /portfolio/accounts

```bash
For tiered accounts (FA, IBroker):

```bash
GET /portfolio/subaccounts

```bash

### Querying Currency Balances

```bash
GET /portfolio/{accountId}/ledger

```bash
Response includes per-currency:

- Settled cash
- Cash balance
- Net liquidation value
- Unrealized P&L
- Realized P&L

### Querying Equity and Margin

```bash
GET /portfolio/{accountId}/summary

```bash
Returns equity and margin values for:

- Entire U-account (aggregate)
- Regulatory segments (securities vs commodities)

- --

## FYIs and Alerts

### Types of Notifications

- **FYIs**- Portfolio notifications and disclaimers
- **Alerts**- Mobile trading alerts
- **Bulletins** - System announcements

### Get Unread Count

```bash
GET /fyi/unreadnumber

```bash
Response:

```json
{"BN": 1}

```bash

### Get All Notifications

```bash
GET /fyi/notifications

```bash
Response fields:

- `R` - Read status (0 = unread)
- `MS` - Title
- `FC` - FYI code
- `ID` - Unique identifier
- `MD` - HTML-formatted message

### Mark as Read

```bash
PUT /fyi/notifications/{notificationID}

```bash

### Manage Subscriptions

```bash
GET /fyi/settings

```bash

### Accept Disclaimer

```bash
GET /fyi/disclaimer/{typecode}
PUT /fyi/disclaimer/{typecode}

```bash

- --

## Additional Resources

- [Web API Documentation](<https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-documentation/)>
- [Web API Reference](<https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-reference/)>
- [IBKR Campus](<https://www.interactivebrokers.com/campus/)>
