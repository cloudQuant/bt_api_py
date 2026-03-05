# IBKR Account Management Web API

> Source: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-account-management/>
> Last Updated: 2026-02-26

## Table of Contents

- [Introduction](#introduction)
- [Authentication](#authentication)
- [Connectivity](#connectivity)
- [Rate Limiting](#rate-limiting)
- [Account Endpoints](#account-endpoints)
- [Funding and Banking](#funding-and-banking)
- [Reporting](#reporting)
- [Single Sign On (SSO)](#single-sign-on-sso)
- [Error Handling](#error-handling)

---

## Introduction

The IBKR Account Management API provides programmatic access to account-related operations for Introducing Brokers, Financial Advisors, and Institutional clients. This API allows you to:

- Register and manage client accounts
- Handle funding and banking operations
- Access account statements and reports
- Manage user authentication and permissions

- *Note**: This API is primarily designed for institutional use. Individual traders should use the Client Portal Gateway instead.

---

## Authentication

### OAuth 2.0 Authentication

IBKR uses OAuth 2.0 with `private_key_jwt` client authentication (RFC 7521/7523).

#### Authentication Flow

1. Generate a signed JWT (`client_assertion`)
2. POST to the authorization server with the JWT
3. Receive an access token
4. Include the access token in subsequent requests

#### Token Endpoint

```
POST /oauth/token

```

- *Request Headers:**

```
Content-Type: application/json

```

- *Request Body:**

```json
{
  "grant_type": "client_credentials",
  "client_id": "YOUR_CLIENT_ID",
  "client_assertion": "SIGNED_JWT_TOKEN",
  "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
}

```

- *Response:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "read write"
}

```

---

## Connectivity

### Base URLs

| Environment | Base URL |

|-------------|----------|

| Production | `<https://api.interactivebrokers.com`> |

| Testing | `<https://api.test.interactivebrokers.com`> |

### Request Format

- **Protocol**: HTTPS only
- **Method**: RESTful HTTP verbs (GET, POST, PUT, PATCH, DELETE)
- **Content-Type**: `application/json`
- **Encoding**: UTF-8

### Response Format

- **Content-Type**: `application/json`
- **Status Codes**: Standard HTTP status codes

| Code | Meaning |

|------|---------|

| 200 | Success |

| 400 | Bad Request |

| 401 | Unauthorized |

| 403 | Forbidden |

| 404 | Not Found |

| 429 | Rate Limit Exceeded |

| 500 | Internal Server Error |

---

## Rate Limiting

### Global Rate Limits

| Endpoint Group | Rate Limit |

|----------------|------------|

| Account Management | 10 requests/second |

| Funding/Banking | 10 requests/second |

| Reporting | 10 requests/second |

When rate limits are exceeded, the API returns HTTP 429.

### Response Headers

```http
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640000000

```

---

## Account Endpoints

### Get Accounts

Retrieve account information for authenticated user.

```
GET /gw/api/v1/accounts

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| status | string | No | Filter by account status (e.g., "A", "N", "O", "C", "P", "R", "Q", "E") |

| limit | integer | No | Maximum number of results |

| offset | integer | No | Pagination offset |

- *Response:**

```json
{
  "accounts": [
    {
      "accountId": "U1234567",
      "accountAlias": "My Account",
      "accountType": "INDIVIDUAL",
      "status": "O",
      "currency": "USD",
      "tradingPermissions": ["STK", "OPT", "FUT"],
      "createdDate": "2020-01-15"
    }
  ],
  "totalCount": 1
}

```

### Account Status Codes

| Code | Description |

|------|-------------|

| A | Abandoned - Application deleted due to inactivity |

| N | New - Pending application with no funding details |

| O | Open - Approved and active |

| C | Closed - Was open, then closed |

| P | Pending - Under review |

| Q | Pending Approval - Awaiting approval |

| R | Reopened - Previously closed, now reopened |

| E | Expired - Temporary status during transition |

### Get Account Details

Retrieve detailed information for a specific account.

```
GET /gw/api/v1/accounts/{accountId}

```

- *Path Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | IBKR Account ID |

- *Response:**

```json
{
  "accountId": "U1234567",
  "accountAlias": "My Account",
  "accountType": "INDIVIDUAL",
  "status": "O",
  "currency": "USD",
  "tradingPermissions": ["STK", "OPT", "FUT"],
  "marginType": "REG",
  "createdDate": "2020-01-15",
  "updatedDate": "2024-01-15",
  "accountOwner": {
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com"
  },
  "beneficiaries": []
}

```

### Update Account

Update account information (requires proper permissions).

```
PATCH /gw/api/v1/accounts/{accountId}

```

- *Request Body:**

```json
{
  "accountAlias": "New Alias",
  "marginType": "PORT",
  "tradingPermissions": ["STK", "OPT", "FUT", "CASH"]
}

```

### Close Account

Close an account (requires cleared balance and specific conditions).

```
POST /gw/api/v1/accounts/{accountId}/close

```

- *Request Body:**

```json
{
  "reason": "Customer request"
}

```

---

## Funding and Banking

### Get Bank Instructions

Retrieve saved bank instructions for withdrawals and deposits.

```
GET /gw/api/v1/bank-instructions/query

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | Account ID |

| method | string | No | "WITHDRAW" or "DEPOSIT" |

- *Response:**

```json
{
  "instructions": [
    {
      "instructionId": "12345",
      "bankName": "Chase Bank",
      "accountNumber": "****1234",
      "accountType": "CHECKING",
      "currency": "USD",
      "defaultInstruction": true
    }
  ]
}

```

### Create Withdraw Request

Create a request to withdraw funds.

```
POST /gw/api/v1/withdraw-request

```

- *Request Body:**

```json
{
  "accountId": "U1234567",
  "amount": 1000.00,
  "currency": "USD",
  "instructionId": "12345",
  "notes": "Monthly withdrawal"
}

```

### Create Deposit Request

Create a request to deposit funds (for notifications/checks).

```
POST /gw/api/v1/deposit-request

```

- *Request Body:**

```json
{
  "accountId": "U1234567",
  "amount": 5000.00,
  "currency": "USD",
  "method": "CHECK",
  "notes": "Initial funding"
}

```

### Internal Transfer

Transfer funds or positions between accounts.

```
POST /gw/api/v1/internal-transfer

```

- *Request Body:**

```json
{
  "fromAccountId": "U1234567",
  "toAccountId": "U7654321",
  "transferType": "CASH",
  "amount": 1000.00,
  "currency": "USD"
}

```

- *For Position Transfers:**

```json
{
  "fromAccountId": "U1234567",
  "toAccountId": "U7654321",
  "transferType": "POSITION",
  "transfers": [
    {
      "conid": 265598,
      "quantity": 100
    }
  ]
}

```

---

## Reporting

### Get Activity Statements

Retrieve available activity statements for an account.

```
GET /gw/api/v1/statements

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | Account ID |

| startDate | string | Yes | Format: YYYY-MM-DD |

| endDate | string | Yes | Format: YYYY-MM-DD |

- *Response:**

```json
{
  "statements": [
    {
      "statementId": "12345",
      "period": "2024-01-01 to 2024-01-31",
      "type": "ACTIVITY",
      "url": "<https://...",>
      "generatedDate": "2024-02-01"
    }
  ]
}

```

### Get Tax Documents

Retrieve available tax documents.

```
GET /gw/api/v1/tax-documents/available

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | Account ID |

| taxYear | integer | Yes | Tax year |

- *Response:**

```json
{
  "documents": [
    {
      "documentType": "1099",
      "taxYear": 2023,
      "availableDate": "2024-02-15",
      "url": "<https://...">
    }
  ]
}

```

### Trade Confirmations

Generate trade confirmation PDFs for a date range.

```
GET /gw/api/v1/trade-confirmations

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | Account ID |

| startDate | string | Yes | Format: YYYY-MM-DD (max 365 day range) |

| endDate | string | Yes | Format: YYYY-MM-DD |

---

## Single Sign On (SSO)

### SSO URL Generation

Generate an SSO URL to redirect users to the IBKR Portal for completing actions not supported via API.

```
GET /gw/api/v1/sso/url

```

- *Query Parameters:**

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| accountId | string | Yes | Account ID |

| targetUrl | string | Yes | Destination URL in Portal |

| showNavBar | boolean | No | Hide navigation bar (for IFRAME embedding) |

- *Response:**

```json
{
  "ssoUrl": "<https://portal.interactivebrokers.com/sso/login?token=...",>
  "expiresIn": 300
}

```

---

## Error Handling

### Error Response Format

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Invalid account ID format",
    "details": {
      "field": "accountId",
      "reason": "Must start with 'U' followed by digits"
    }
  },
  "timestamp": "2024-01-15T10:30:00Z",
  "path": "/gw/api/v1/accounts/INVALID"
}

```

### Common Error Codes

| Code | Description |

|------|-------------|

| INVALID_REQUEST | Malformed request body or parameters |

| UNAUTHORIZED | Missing or invalid authentication |

| FORBIDDEN | Insufficient permissions for requested operation |

| NOT_FOUND | Requested resource does not exist |

| RATE_LIMIT_EXCEEDED | Request rate limit exceeded |

| ACCOUNT_NOT_ELIGIBLE | Account not eligible for requested operation |

| VALIDATION_ERROR | Input validation failed |

| SYSTEM_ERROR | Internal server error |

---

## System Availability

### Maintenance Windows

| Endpoint Group | Maintenance Time (ET) |

|----------------|------------------------|

| Account Management | Daily 6:00 PM - 6:05 PM |

| Funding/Banking | Daily 11:45 PM - 12:30 AM |

| Statements | Sundays & Tuesdays 6:00 PM - 6:30 PM |

---

## Support

### Contact Information

- **Email**: am-api@interactivebrokers.com
- **Documentation**: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-account-management/>
- **API Reference**: <https://www.interactivebrokers.com/api/doc.html>

### Troubleshooting Checklist

Before contacting support:

- [ ] Verify authentication credentials are valid
- [ ] Check account status is "Open" (O)
- [ ] Confirm request is within rate limits
- [ ] Verify account has required permissions
- [ ] Check if currently in maintenance window
