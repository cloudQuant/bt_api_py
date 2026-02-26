# General Info

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/general-info)

## General API Information

The following base endpoints are available:

- https://api.binance.com
- https://api-gcp.binance.com
- https://api1.binance.com
- https://api2.binance.com
- https://api3.binance.com
- https://api4.binance.com

The last 4 endpoints in the point above (api1-api4) might give better performance but have less stability. Please use whichever works best for your setup.

- All endpoints return either a JSON object or array.
- Data is returned in ascending order. Oldest first, newest last.
- All time and timestamp related fields are in milliseconds.

## HTTP Return Codes

- HTTP `4XX` return codes are used for malformed requests; the issue is on the sender's side.
- HTTP `403` return code is used when the WAF Limit (Web Application Firewall) has been violated.
- HTTP `409` return code is used when a cancelReplace order partially succeeds. (e.g. if the cancellation of the order fails but the new order placement succeeds.)
- HTTP `429` return code is used when breaking a request rate limit.
- HTTP `418` return code is used when an IP has been auto-banned for continuing to send requests after receiving `429` codes.
- HTTP `5XX` return codes are used for internal errors; the issue is on Binance's side. It is important to **NOT** treat this as a failure operation; the execution status is UNKNOWN and could have been a success.

## Error Codes and Messages

If there is an error, the API will return an error with a message of the reason.

The error payload on API and SAPI is as follows:

```json
{
  "code": -1121,
  "msg": "Invalid symbol."
}
```

Specific error codes and messages defined in [Error Codes](./error-code.md).

## General Information on Endpoints

- For `GET` endpoints, parameters must be sent as a **query string**.
- For `POST`, `PUT`, and `DELETE` endpoints, the parameters may be sent as a query string or in the request body with content type `application/x-www-form-urlencoded`. You may mix parameters between both the query string and request body if you wish to do so.
- Parameters may be sent in any order.
- If a parameter sent in both the query string and request body, the query string parameter will be used.

## LIMITS

### General Info on Limits

The following `intervalLetter` values for headers:

- SECOND => S
- MINUTE => M
- HOUR => H
- DAY => D

`intervalNum` describes the amount of the interval. For example, `intervalNum` 5 with `intervalLetter` M means "Every 5 minutes".

The `/api/v3/exchangeInfo` rateLimits array contains objects related to the exchange's `RAW_REQUESTS`, `REQUEST_WEIGHT`, and `ORDERS` rate limits. These are further defined in the ENUM definitions section under Rate limiters (rateLimitType).

A 429 will be returned when either request rate limit or order rate limit is violated.

### IP Limits

- Every request will contain `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` in the response headers which has the current used weight for the IP for all request rate limiters defined.
- Each route has a `weight` which determines for the number of requests each endpoint counts for. Heavier endpoints and endpoints that do operations on multiple symbols will have a heavier weight.
- When a 429 is received, it's your obligation as an API to back off and not spam the API.
- **Repeatedly violating rate limits and/or failing to back off after receiving 429s will result in an automated IP ban (HTTP status 418).**
- IP bans are tracked and **scale in duration** for repeat offenders, from **2 minutes to 3 days**.
- A `Retry-After` header is sent with a 418 or 429 responses and will give the **number of seconds** required to wait, in the case of a 429, to prevent a ban, or, in the case of a 418, until the ban is over.
- The limits on the API are based on the IPs, not the API keys.
- We recommend using the websocket for getting data as much as possible, as this will not count to the request rate limit.

### Order Rate Limits

- Every successful order response will contain a `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)` header which has the current order count for the account for all order rate limiters defined.
- When the order count exceeds the limit, you will receive a 429 error without the `Retry-After` header. Please check the Order Rate Limit rules using `GET api/v3/exchangeInfo` and wait for reactivation accordingly.
- Rejected/unsuccessful orders are not guaranteed to have `X-MBX-ORDER-COUNT-**` headers in the response.
- The order rate limit is counted against each account.
- To monitor order count usage, refer to `GET api/v3/rateLimit/order`

### Websocket Limits

- WebSocket connections have a limit of 5 incoming messages per second. A message is considered:
  - A PING frame
  - A PONG frame
  - A JSON controlled message (e.g. subscribe, unsubscribe)
- A connection that goes beyond the limit will be disconnected; IPs that are repeatedly disconnected may be banned.
- A single connection can listen to a maximum of 1024 streams.
- There is a limit of 300 connections per attempt every 5 minutes per IP.

### /api/ and /sapi/ Limit Introduction

The `/api/*` and `/sapi/*` endpoints adopt either of two access limiting rules, IP limits or UID (account) limits.

**Endpoints related to `/api/*`:**

- According to the two modes of IP and UID (account) limit, each are independent.
- Endpoints share the 6000 per minute limit based on IP.
- Responses contain the header `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`, defining the weight used by the current IP.
- Successful order responses contain the header `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`, defining the order limit used by the UID.

**Endpoints related to `/sapi/*`:**

- Endpoints are marked according to IP or UID limit and their corresponding weight value.
- Each endpoint with IP limits has an independent 12000 per minute limit.
- Each endpoint with UID limits has an independent 180000 per minute limit.
- Responses from endpoints with IP limits contain the header `X-SAPI-USED-IP-WEIGHT-1M`, defining the weight used by the current IP.
- Responses from endpoints with UID limits contain the header `X-SAPI-USED-UID-WEIGHT-1M`, defining the weight used by the current UID.

## Data Sources

The API system is asynchronous, so some delay in the response is normal and expected.

Each endpoint has a data source indicating where the data is being retrieved, and thus which endpoints have the most up-to-date response.

These are the three sources, ordered by which has the most up-to-date response to the one with potential delays in updates:

- **Matching Engine** - the data is from the matching Engine
- **Memory** - the data is from a server's local or external memory
- **Database** - the data is taken directly from a database

Some endpoints can have more than 1 data source. (e.g. Memory => Database). This means that the endpoint will check the first Data Source, and if it cannot find the value it's looking for it will check the next one.

## Request Security

Each endpoint has a security type indicating required API key permissions, shown next to the endpoint name (e.g., New order (TRADE)).

- If unspecified, the security type is NONE.
- Except for NONE, all endpoints with a security type are considered SIGNED requests (i.e. including a signature), except for listenKey management.
- Secure endpoints require a valid API key to be specified and authenticated.
- API keys can be created on the [API Management](https://www.binance.com/en/usercenter/settings/api-management) page of your Binance account.
- Both API key and secret key are sensitive. Never share them with anyone. If you notice unusual activity in your account, immediately revoke all the keys and contact Binance support.
- API keys can be configured to allow access only to certain types of secure endpoints.
- By default, an API key cannot TRADE. You need to enable trading in API Management first.

| Security Type | Description |
|---------------|-------------|
| NONE | Endpoint can be accessed freely. |
| TRADE | Endpoint requires sending a valid API-Key and signature. |
| MARGIN | Endpoint requires sending a valid API-Key and signature. |
| USER_DATA | Endpoint requires sending a valid API-Key and signature. |
| USER_STREAM | Endpoint requires sending a valid API-Key. |
| MARKET_DATA | Endpoint requires sending a valid API-Key. |

TRADE, MARGIN and USER_DATA endpoints are SIGNED endpoints.

## SIGNED Endpoint security

SIGNED endpoints require an additional parameter, `signature`, to be sent in the query string or request body.

### Signature Case Sensitivity

- **HMAC:** Signatures generated using HMAC are not case-sensitive. This means the signature string can be verified regardless of letter casing.
- **RSA:** Signatures generated using RSA are case-sensitive.
- **Ed25519:** Signatures generated using Ed25519 are also case-sensitive.

Please consult SIGNED request example (HMAC), SIGNED request example (RSA), and SIGNED request example (Ed25519) on how to compute signature, depending on which API key type you are using.

### Timing security

- SIGNED requests also require a `timestamp` parameter which should be the current timestamp either in milliseconds or microseconds.
- An additional optional parameter, `recvWindow`, specifies for how long the request stays valid and may only be specified in milliseconds.
- `recvWindow` supports up to three decimal places of precision (e.g., 6000.346) so that microseconds may be specified.
- If `recvWindow` is not sent, it defaults to 5000 milliseconds.
- Maximum `recvWindow` is 60000 milliseconds.

Request processing logic:

```
serverTime = getCurrentTime()
if (timestamp < (serverTime + 1 second) && (serverTime - timestamp) <= recvWindow) {
  // process request
} else {
  // reject request
}
```

> **Serious trading is about timing.** Networks can be unstable and unreliable, which can lead to requests taking varying amounts of time to reach the servers. With `recvWindow`, you can specify that the request must be processed within a certain number of milliseconds or be rejected by the server. It is recommended to use a small recvWindow of 5000 or less!
