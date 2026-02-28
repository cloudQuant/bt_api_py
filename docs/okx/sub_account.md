# Sub-account

Sub-account management APIs require authentication.

## REST API

### GET / Get sub-account list

Retrieve sub-account list.

- **Rate Limit**: 2 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```bash
GET /api/v5/users/subaccount/list

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| enable | String | No | Sub-account status |

| subAcct | String | No | Sub-account name |

| after | String | No | Pagination |

| before | String | No | Pagination |

| limit | String | No | Default 100, max 100 |

### POST / Create sub-account

```bash
POST /api/v5/users/subaccount/create-subaccount

```bash

### POST / Create an API Key for a sub-account

```bash
POST /api/v5/users/subaccount/apikey

```bash

### GET / Query the API Key of a sub-account

```bash
GET /api/v5/users/subaccount/apikey

```bash

### POST / Reset the API Key of a sub-account

```bash
POST /api/v5/users/subaccount/modify-apikey

```bash

### POST / Delete the API Key of sub-accounts

```bash
POST /api/v5/users/subaccount/delete-apikey

```bash

### GET / Get sub-account trading balance

Retrieve the trading balance of a sub-account.

- **Rate Limit**: 6 requests per 2 seconds
- **Permission**: Read

```bash
GET /api/v5/account/subaccount/balances

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| subAcct | String | Yes | Sub-account name |

### GET / Get sub-account funding balance

```bash
GET /api/v5/asset/subaccount/balances

```bash

### GET / Get sub-account maximum withdrawals

```bash
GET /api/v5/account/subaccount/max-withdrawal

```bash

### GET / Get history of sub-account transfer

```bash
GET /api/v5/asset/subaccount/bills

```bash

### GET / Get history of managed sub-account transfer

```bash
GET /api/v5/asset/subaccount/managed-subaccount-bills

```bash

### POST / Master accounts manage the transfers between sub-accounts

```bash
POST /api/v5/asset/subaccount/transfer

```bash

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| ccy | String | Yes | Currency |

| amt | String | Yes | Transfer amount |

| from | String | Yes | `6` Funding, `18` Trading |

| to | String | Yes | `6` Funding, `18` Trading |

| fromSubAccount | String | Yes | Source sub-account name |

| toSubAccount | String | Yes | Target sub-account name |

### POST / Set permission of transfer out

```bash
POST /api/v5/users/subaccount/set-transfer-out

```bash

### GET / Get custody trading sub-account list

```bash
GET /api/v5/users/subaccount/entrust-subaccount-list

```bash
