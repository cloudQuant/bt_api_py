# Overview

Welcome to OKX API documentation. OKX provides REST and WebSocket APIs to suit your trading needs.

- For users who complete registration on my.okx.com, please visit [https://my.okx.com/docs-v5/en/](https://my.okx.com/docs-v5/en/) for the API documentation.
- For users who complete registration on app.okx.com, please visit [https://app.okx.com/docs-v5/en/](https://app.okx.com/docs-v5/en/) for the API documentation.

## API Resources and Support

### Tutorials
- Learn how to trade with API: [Best practice to OKX's API](https://www.okx.com/docs-v5/trick_en/)
- Learn python spot trading step by step: [Python Spot Trading Tutorial](https://www.okx.com/help/how-can-i-do-spot-trading-with-the-jupyter-notebook)
- Learn python derivatives trading step by step: [Python Derivatives Trading Tutorial](https://www.okx.com/help/how-can-i-do-derivatives-trading-with-the-jupyter-notebook)

### Python libraries
- Use [Python SDK](https://pypi.org/project/python-okx/) for easier integration
- Get access to our market maker python sample code [Python market maker sample](https://github.com/okxapi/okx-sample-market-maker)

### Customer service
- Please take 1 minute to help us improve: [API Satisfaction Survey](https://forms.gle/Ehou2xFv5GE1xUGr9)
- If you have any API-related inquiries, you can reach out to us by scanning the code below via the OKX APP.

## API Key Creation

Please refer to [my api page](https://www.okx.com/account/my-api) regarding API Key creation.

### Generating an API key

Create an API key on the website before signing any requests. After creating an API key, keep the following information safe:
- API key
- Secret key
- Passphrase

The system returns randomly-generated API keys and SecretKeys. You will need to provide the Passphrase to access the API. We store the salted hash of your Passphrase for authentication. We cannot recover the Passphrase if you have lost it. You will need to create a new set of API key.

### API key permissions

There are three permissions below that can be associated with an API key. One or more permission can be assigned to any key.
- **Read**: Can request and view account info such as bills and order history which need read permission
- **Trade**: Can place and cancel orders, funding transfer, make settings which need write permission
- **Withdraw**: Can make withdrawals

### API key security

- Each API key can bind up to 20 IP addresses, which support IPv4/IPv6 and network segment formats.
- Only when the user calls an API that requires API key authentication will it be considered as the API key is used.
- Calling an API that does not require API key authentication will not be considered used even if API key information is passed in.
- For websocket, only operation of logging in will be considered to have used the API key. Any operation though the connection after logging in (such as subscribing/placing an order) will not be considered to have used the API key.

Users can get the usage records of the API key with `trade` or `withdraw` permissions but unlinked to any IP address through [Security Center](https://www.okx.com/account/security).

## REST Authentication

### Making Requests

All private REST requests must contain the following headers:
- `OK-ACCESS-KEY` The API key as a String.
- `OK-ACCESS-SIGN` The Base64-encoded signature (see Signing Messages subsection for details).
- `OK-ACCESS-TIMESTAMP` The UTC timestamp of your request. e.g.: `2020-12-08T09:08:57.715Z`
- `OK-ACCESS-PASSPHRASE` The passphrase you specified when creating the API key.

Request bodies should have content type `application/json` and be in valid JSON format.

### Signature

The `OK-ACCESS-SIGN` header is generated as follows:

1. Create a pre-hash string of `timestamp + method + requestPath + body` (where + represents String concatenation).
2. Prepare the SecretKey.
3. Sign the pre-hash string with the SecretKey using the HMAC SHA256.
4. Encode the signature in the Base64 format.

Example:
```
sign=CryptoJS.enc.Base64.stringify(CryptoJS.HmacSHA256(timestamp + 'GET' + '/api/v5/account/balance?ccy=BTC', SecretKey))
```

The `timestamp` value is the same as the `OK-ACCESS-TIMESTAMP` header with millisecond ISO format, e.g. `2020-12-08T09:08:57.715Z`.

The request method should be in UPPERCASE: e.g. `GET` and `POST`.

The `requestPath` is the path of requesting an endpoint.
Example: `/api/v5/account/balance`

The `body` refers to the String of the request body. It can be omitted if there is no request body (frequently the case for `GET` requests).
Example: `{"instId":"BTC-USDT","lever":"5","mgnMode":"isolated"}`

The SecretKey is generated when you create an API key.
Example: `22582BD0CFF14C41EDBF1AB98506286D`

## WebSocket

### Overview

WebSocket is a new HTML5 protocol that achieves full-duplex data transmission between the client and server, allowing data to be transferred effectively in both directions. A connection between the client and server can be established with just one handshake. Its advantages include:
- The WebSocket request header size for data transmission between client and server is only 2 bytes.
- Either the client or server can initiate data transmission.
- There's no need to repeatedly create and delete TCP connections, saving resources on bandwidth and server.

### Connect

Connection limit: 3 requests per second (based on IP)

When subscribing to a public channel, use the address of the public service. When subscribing to a private channel, use the address of the private service.

Request limit: The total number of `subscribe`/`unsubscribe`/`login` requests per connection is limited to 480 times per hour.

If there's a network problem, the system will automatically disable the connection. The connection will break automatically if the subscription is not established or data has not been pushed for more than 30 seconds.

To keep the connection stable:
1. Set a timer of N seconds whenever a response message is received, where N is less than 30.
2. If the timer is triggered, which means that no new message is received within N seconds, send the String `'ping'`.
3. Expect a `'pong'` as a response. If the response message is not received within N seconds, please raise an error or reconnect.

### Connection count limit

The limit will be set at 30 WebSocket connections per specific WebSocket channel per sub-account. Each WebSocket connection is identified by the unique `connId`.

The WebSocket channels subject to this limitation are:
1. Orders channel
2. Account channel
3. Positions channel
4. Balance and positions channel
5. Position risk warning channel
6. Account greeks channel

If users subscribe to the same channel through the same WebSocket connection through multiple arguments (e.g. `{"channel": "orders", "instType": "ANY"}` and `{"channel": "orders", "instType": "SWAP"}`), it will be counted once only.

Connection count update example:
```json
{
  "event": "channel-conn-count",
  "channel": "orders",
  "connCount": "2",
  "connId": "abcd1234"
}
```

Connection limit error example:
```json
{
  "event": "channel-conn-count-error",
  "channel": "orders",
  "connCount": "30",
  "connId": "a4d3ae55"
}
```

### Login

Request Example:
```json
{
  "op": "login",
  "args": [
    {
      "apiKey": "******",
      "passphrase": "******",
      "timestamp": "1538054050",
      "sign": "7L+zFQ+CEgGu5rzCj4+BdV2/uUHGqddA9pI6ztsRRPs="
    }
  ]
}
```

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| op | String | Yes | Operation, `login` |
| args | Array | Yes | Request parameters |
| > apiKey | String | Yes | API Key |
| > passphrase | String | Yes | API Key password |
| > timestamp | String | Yes | Unix Epoch time in seconds, e.g. `1704876947` |
| > sign | String | Yes | Signature string |

Successful Response:
```json
{
  "event": "login",
  "code": "0",
  "msg": "",
  "connId": "a4d3ae55"
}
```

Failure Response:
```json
{
  "event": "error",
  "code": "60009",
  "msg": "Login failed.",
  "connId": "a4d3ae55"
}
```

**Sign generation:**
1. Concatenate `timestamp`, `method`, `requestPath` strings
2. Use HMAC SHA256 method to encrypt with SecretKey
3. Perform Base64 encoding

- method: always `'GET'`
- requestPath: always `'/users/self/verify'`

Example: `sign=CryptoJS.enc.Base64.stringify(CryptoJS.HmacSHA256(timestamp + 'GET' + '/users/self/verify', secretKey))`

### Subscribe

WebSocket channels are divided into two categories:
- **Public channels** — No authentication required, includes tickers, K-Line, limit price, order book, mark price channels, etc.
- **Private channels** — Including account, order, and position channels, etc. — require login.

Users can choose to subscribe to one or more channels, and the total length of multiple channels cannot exceed 64 KB.

Request Example:
```json
{
  "id": "1512",
  "op": "subscribe",
  "args": [
    {
      "channel": "tickers",
      "instId": "BTC-USDT"
    }
  ]
}
```

Response Example:
```json
{
  "id": "1512",
  "event": "subscribe",
  "arg": {
    "channel": "tickers",
    "instId": "BTC-USDT"
  },
  "connId": "accb8e21"
}
```

### Unsubscribe

Request Example:
```json
{
  "op": "unsubscribe",
  "args": [
    {
      "channel": "tickers",
      "instId": "BTC-USDT"
    }
  ]
}
```

Response Example:
```json
{
  "event": "unsubscribe",
  "arg": {
    "channel": "tickers",
    "instId": "BTC-USDT"
  },
  "connId": "d0b44253"
}
```

### Notification

WebSocket has introduced a new message type (`event = notice`).

Client will receive the information in the following scenarios:
- **Websocket disconnect for service upgrade**: 60 seconds prior to the upgrade, the notification message will be sent indicating that the connection will soon be disconnected.

Response Example:
```json
{
  "event": "notice",
  "code": "64008",
  "msg": "The connection will soon be closed for a service upgrade. Please reconnect.",
  "connId": "a4d3ae55"
}
```

The feature is supported by WebSocket Public (`/ws/v5/public`) and Private (`/ws/v5/private`) for now.

## Account Mode

To facilitate your trading experience, please set the appropriate account mode before starting trading.

In the trading account trading system, 4 account modes are supported:
- `Spot mode`
- `Futures mode`
- `Multi-currency margin mode`
- `Portfolio margin mode`

You need to set on the Web/App for the first set of every account mode.

## Production Trading Services

| Service | URL |
|---------|-----|
| REST | `https://www.okx.com` |
| Public WebSocket | `wss://ws.okx.com:8443/ws/v5/public` |
| Private WebSocket | `wss://ws.okx.com:8443/ws/v5/private` |
| Business WebSocket | `wss://ws.okx.com:8443/ws/v5/business` |

## Demo Trading Services

Currently, the API works for Demo Trading, but some functions are not supported, such as `withdraw`, `deposit`, `purchase/redemption`, etc.

| Service | URL |
|---------|-----|
| REST | `https://www.okx.com` |
| Public WebSocket | `wss://wspap.okx.com:8443/ws/v5/public` |
| Private WebSocket | `wss://wspap.okx.com:8443/ws/v5/private` |
| Business WebSocket | `wss://wspap.okx.com:8443/ws/v5/business` |

OKX account can be used for login on Demo Trading. Start API Demo Trading by: Login OKX → Trade → Demo Trading → Personal Center → Demo Trading API → Create Demo Trading API Key → Start your Demo Trading

Http Header Example:
```
Content-Type: application/json
OK-ACCESS-KEY: 37c541a1-****-****-****-10fe7a038418
OK-ACCESS-SIGN: leaVRETrtaoEQ3yI9qEtI1CZ82ikZ4xSG5Kj8gnl3uw=
OK-ACCESS-PASSPHRASE: 1****6
OK-ACCESS-TIMESTAMP: 2020-03-28T12:21:41.274Z
x-simulated-trading: 1
```

### Demo Trading Explorer

Try [demo trading explorer](https://www.okx.com/demo-trading-explorer/v5/en)

## General Info

The rules for placing orders at the exchange level:
- Maximum pending orders (including post only, limit and taker orders being processed): **4,000**
- Maximum pending orders per trading symbol: **500**, applies to:
  - Limit
  - Market
  - Post only
  - Fill or Kill (FOK)
  - Immediate or Cancel (IOC)
  - Market order with IOC (optimal limit IOC)
  - Take Profit / Stop Loss (TP/SL)
  - Limit and market orders triggered under: TP/SL, Trigger, Trailing stop, Arbitrage, Iceberg, TWAP, Recurring buy

- Maximum pending spread orders: **500** across all spreads
- Maximum pending algo orders:
  - TP/SL order: 100 per instrument
  - Trigger order: 500
  - Trailing order: 50
  - Iceberg order: 100
  - TWAP order: 20
- Maximum grid trading:
  - Spot grid: 100
  - Contract grid: 100

Trading rules:
- When maker orders matched with a taker order exceeds 1000, the taker order will be canceled.
  - Limit orders: executed with a portion corresponding to 1000 maker orders, remainder canceled.
  - FOK orders: canceled directly.

Return data rules:
- `code` and `msg` represent the request result when the return data has `code` and not `sCode`
- `sCode` and `sMsg` represent the request result when the return data has `sCode`

**instFamily and uly parameter explanation:**
- `uly` is the index (e.g. "BTC-USD"), has one-to-many relationship with `settleCcy`
- `instFamily` is the trading instrument family (e.g. "BTC-USD_UM"), has one-to-one relationship with `settleCcy`

> Note: USDⓈ represents USD and multiple USD stable coins (USDC, USDG). The settlement and margin currency refers to the `settleCcy` field returned by the Get instruments endpoint.

## Transaction Timeouts

Orders may not be processed in time due to network delay or busy servers. You can configure the expiry time using `expTime` if you want the order request to be discarded after a specific time.

### REST API

Set `expTime` in the request header (Unix timestamp in milliseconds, e.g. `1597026383085`).

Supported endpoints:
- Place order
- Place multiple orders
- Amend order
- Amend multiple orders
- Place sub order (under signal bot trading)

Request Example:
```bash
curl -X 'POST' \
  'https://www.okx.com/api/v5/trade/order' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'OK-ACCESS-KEY: *****' \
  -H 'OK-ACCESS-SIGN: *****' \
  -H 'OK-ACCESS-TIMESTAMP: *****' \
  -H 'OK-ACCESS-PASSPHRASE: *****' \
  -H 'expTime: 1597026383085' \
  -d '{
    "instId": "BTC-USDT",
    "tdMode": "cash",
    "side": "buy",
    "ordType": "limit",
    "px": "1000",
    "sz": "0.01"
  }'
```

### WebSocket

Set `expTime` in the request body (Unix timestamp in milliseconds).

Supported endpoints:
- Place order
- Place multiple orders
- Amend order
- Amend multiple orders

Request Example:
```json
{
  "id": "1512",
  "op": "order",
  "expTime": "1597026383085",
  "args": [{
    "side": "buy",
    "instId": "BTC-USDT",
    "tdMode": "isolated",
    "ordType": "market",
    "sz": "100"
  }]
}
```

## Rate Limits

REST and WebSocket APIs use rate limits to protect against malicious usage. When a request is rejected due to rate limits, error code `50011` is returned.

Rate limit definitions:
- **WebSocket login and subscription** rate limits are based on connection.
- **Public unauthenticated REST** rate limits are based on IP address.
- **Private REST** rate limits are based on User ID (sub-accounts have individual User IDs).
- **WebSocket order management** rate limits are based on User ID.

### Trading-related APIs

For Trading-related APIs (place order, cancel order, amend order):
- Rate limits are shared across REST and WebSocket channels.
- Rate limits for placing, amending, and cancelling orders are independent from each other.
- Rate limits are defined on the Instrument ID level (except Options).
- Rate limits for Options are defined based on Instrument Family level.
- Rate limits for multiple and single order endpoints are independent, except when only one order is sent to a multiple order endpoint.

### Sub-account rate limit

Maximum 1000 order requests per 2 seconds at the sub-account level. Only new order and amendment order requests are counted. Error code `50061` is returned when exceeded.

Applicable endpoints:
- POST / Place order
- POST / Place multiple orders
- POST / Amend order
- POST / Amend multiple orders
- WS / Place order
- WS / Place multiple orders
- WS / Amend order
- WS / Amend multiple orders

### Fill ratio based sub-account rate limit

Only applicable to >= VIP5 customers. Higher sub-account rate limits for clients with high trade fill ratio.

The exchange calculates two ratios based on transaction data from the past 7 days at 00:00 UTC:
1. **Sub-account fill ratio** = (trade volume in USDT) / (sum of order count per symbol × symbol multiplier)
2. **Master account aggregated fill ratio** = (trade volume in USDT on master level) / (sum of all sub-accounts' order count × symbol multiplier)

Rate limit tiers are determined at 08:00 UTC using the maximum of the two ratios.

Key notes:
- Block trading, spread trading, MMP and fiat orders are excluded from order count
- Block trading and spread trading are excluded from trade volume
- Only successful order requests (sCode=0) are considered
- If fill ratio improves, uplift takes effect immediately; if it decreases, a one-day grace period applies
- If 7-day trading volume < 1,000,000 USDT, master account fill ratio is applied
- Block trading, spread trading, MMP and spot/margin orders are exempted from the sub-account rate limit

Use the GET / Account rate limit endpoint for ratio and rate limit data (updated daily at 8am UTC).

### Best practices

If you require a higher request rate, set up different sub-accounts to batch request rate limits. Throttle or space out requests to maximize each account's rate limit and avoid disconnections or rejections.

## Market Maker Program

High-caliber trading teams are welcomed to work with OKX as market makers. OKX market makers could enjoy favourable fees in return for meeting market making obligations.

Prerequisites (satisfy any condition):
- VIP 2 or above on fee schedule
- Qualified Market Maker on other exchange

Interested parties: [https://okx.typeform.com/contact-sales](https://okx.typeform.com/contact-sales)

## Broker Program

If your business platform offers cryptocurrency services, you can apply to join the OKX Broker Program, become a partner broker, enjoy exclusive broker services, and earn high rebates.

- [Click to apply](https://www.okx.com/broker/home)
- [Broker rules](https://www.okx.com/help/introduction-of-rules-on-okx-brokers)
