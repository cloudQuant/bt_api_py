# Status, Announcement & Error Code

## Status

### GET / Status

Get event status of system upgrade.

- **Rate Limit**: 1 request per 5 seconds

```bash
GET /api/v5/system/status

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| state | String | No | System maintenance status: `scheduled`, `ongoing`, `pre_open`, `completed`, `canceled` |

### WS / Status channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `status`

Subscribe Example:

```json
{
  "op": "subscribe",
  "args": [{
    "channel": "status"
  }]
}

```bash

- --

## Announcement

### GET / Announcements

Get announcements.

```bash
GET /api/v5/support/announcements

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| annType | String | No | Announcement type |

| page | String | No | Page number |

| limit | String | No | Default 100, max 100 |

### GET / Announcement types

Get announcement types.

```bash
GET /api/v5/support/announcement-types

```bash

- --

## Error Code

### REST API Error Codes

#### General

| Code | Message |

|------|---------|

| 0 | Success |

| 1 | Operation failed |

| 2 | Bulk operation partially succeeded |

| 50000 | Body cannot be empty |

| 50001 | Service temporarily unavailable |

| 50002 | JSON syntax error |

| 50004 | API endpoint request timeout |

| 50005 | API is offline or unavailable |

| 50006 | Content-Type must be application/json |

| 50007 | Account blocked |

| 50008 | User not found |

| 50009 | Account is suspended due to ongoing liquidation |

| 50010 | User ID cannot be empty |

| 50011 | Rate limit reached |

| 50012 | Account status invalid |

| 50013 | System is busy |

| 50014 | Parameter {param0} cannot be empty |

| 50015 | Either parameter {param0} or {param1} is required |

| 50016 | Parameter {param0} does not match parameter {param1} |

| 50024 | Parameter {param0} and {param1} cannot exist at the same time |

| 50025 | Parameter {param0} count exceeds the limit {param1} |

| 50026 | System error |

| 50027 | Account restricted from trading |

| 50028 | Unable to take the order |

| 50044 | Must select copyTraderNum |

| 50061 | Sub-account rate limit reached |

#### Account

| Code | Message |

|------|---------|

| 51000 | Parameter {param0} error |

| 51001 | Instrument ID does not exist |

| 51002 | Instrument ID does not match underlying index |

| 51003 | Either client order ID or order ID is required |

| 51004 | Order amount exceeds current tier limit |

| 51005 | Order amount exceeds the limit |

| 51006 | Duplicated order ID |

| 51008 | Order failed, insufficient balance |

| 51009 | Order placement function is temporarily unavailable |

| 51010 | Account level must be the same |

| 51011 | Duplicated client order ID |

| 51012 | Token does not exist |

| 51014 | Index does not exist |

| 51015 | Instrument ID does not match instrument type |

| 51016 | Duplicated client order ID |

| 51020 | Order amount should be greater than the min available amount |

| 51023 | Position does not exist |

| 51024 | Trading is not allowed |

| 51025 | Order count exceeds the limit |

| 51026 | Instrument type does not match |

#### Trade

| Code | Message |

|------|---------|

| 51100 | Trading amount does not meet the min trading amount |

| 51101 | Entered amount exceeds the max pending order amount |

| 51102 | Entered amount exceeds the max pending count |

| 51103 | Entered amount exceeds the max position limit |

| 51104 | Entered amount exceeds the max order amount |

| 51105 | Entered amount exceeds the max holding |

| 51106 | Entered amount less than the min amount |

| 51107 | Entered amount exceeds the max amount |

| 51108 | Position does not exist, add margin only for positions with margin |

| 51109 | No available position for this order |

| 51110 | Insufficient balance |

| 51111 | Cancellation failed as the order does not exist |

| 51112 | Cancellation failed as the order is already canceled |

| 51113 | Cancellation failed as the order is already completed |

| 51115 | Amendment failed as the order does not exist |

| 51116 | Amendment failed as the order type cannot be amended |

#### WebSocket Error Codes

| Code | Message |

|------|---------|

| 60001 | Login failed: invalid OK-ACCESS-KEY |

| 60002 | Login failed: invalid OK-ACCESS-PASSPHRASE |

| 60003 | Login failed: invalid OK-ACCESS-SIGN |

| 60004 | Login failed: invalid OK-ACCESS-TIMESTAMP |

| 60005 | Login failed: invalid SecretKey |

| 60006 | Login failed: no valid request |

| 60007 | Login failed: IP not in whitelist |

| 60008 | Login failed: bulk login is not supported |

| 60009 | Login failed |

| 60010 | Login failed: too many connections |

| 60011 | User not logged in |

| 60012 | Illegal request |

| 60013 | Invalid args |

| 60014 | Requests too frequent |

| 60015 | Connection closed as there was no data for 30s |

| 60016 | Buffer is full, cannot write data |

| 60017 | Invalid URL path |

| 60018 | Subscribe count exceeds the limit |

| 60019 | Requests too frequent |

| 63999 | Internal system error |

| 64008 | Connection will soon be closed for service upgrade |

> **Note**: For the complete and most up-to-date error code list, refer to the [OKX API documentation](<https://www.okx.com/docs-v5/en/#error-code).>
