# Sub-account

Sub-account management APIs require authentication.

## REST API

### GET / Get sub-account list

Retrieve sub-account list.

- ***Rate Limit**: 2 requests per 2 seconds
- ***Rate limit rule**: User ID
- ***Permission**: Read

```
GET /api/v5/users/subaccount/list

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| enable | String | No | Sub-account status |
| subAcct | String | No | Sub-account name |
| after | String | No | Pagination |
| before | String | No | Pagination |
| limit | String | No | Default 100, max 100 |

### POST / Create sub-account

```
POST /api/v5/users/subaccount/create-subaccount

```

### POST / Create an API Key for a sub-account

```
POST /api/v5/users/subaccount/apikey

```

### GET / Query the API Key of a sub-account

```
GET /api/v5/users/subaccount/apikey

```

### POST / Reset the API Key of a sub-account

```
POST /api/v5/users/subaccount/modify-apikey

```

### POST / Delete the API Key of sub-accounts

```
POST /api/v5/users/subaccount/delete-apikey

```

### GET / Get sub-account trading balance

Retrieve the trading balance of a sub-account.

- ***Rate Limit**: 6 requests per 2 seconds
- ***Permission**: Read

```
GET /api/v5/account/subaccount/balances

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| subAcct | String | Yes | Sub-account name |

### GET / Get sub-account funding balance

```
GET /api/v5/asset/subaccount/balances

```

### GET / Get sub-account maximum withdrawals

```
GET /api/v5/account/subaccount/max-withdrawal

```

### GET / Get history of sub-account transfer

```
GET /api/v5/asset/subaccount/bills

```

### GET / Get history of managed sub-account transfer

```
GET /api/v5/asset/subaccount/managed-subaccount-bills

```

### POST / Master accounts manage the transfers between sub-accounts

```
POST /api/v5/asset/subaccount/transfer

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| ccy | String | Yes | Currency |
| amt | String | Yes | Transfer amount |
| from | String | Yes | `6` Funding, `18` Trading |
| to | String | Yes | `6` Funding, `18` Trading |
| fromSubAccount | String | Yes | Source sub-account name |
| toSubAccount | String | Yes | Target sub-account name |

### POST / Set permission of transfer out

```
POST /api/v5/users/subaccount/set-transfer-out

```

### GET / Get custody trading sub-account list

```
GET /api/v5/users/subaccount/entrust-subaccount-list

```
