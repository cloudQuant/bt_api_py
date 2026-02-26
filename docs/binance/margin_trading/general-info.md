
# General Info


## General API Information​

- The following base endpoints are available:
  - **https://api.binance.com**
  - **https://api-gcp.binance.com**
  - **https://api1.binance.com**
  - **https://api2.binance.com**
  - **https://api3.binance.com**
  - **https://api4.binance.com**
- The last 4 endpoints in the point above ( `api1` - `api4` ) might give better performance but have less stability. Please use whichever works best for your setup.
- All endpoints return either a JSON object or array.
- Data is returned in **ascending** order. Oldest first, newest last.
- All time and timestamp related fields are in **milliseconds** .

### HTTP Return Codes​

- HTTP `4XX` return codes are used for malformed requests;
the issue is on the sender's side.
- HTTP `403` return code is used when the WAF Limit (Web Application Firewall) has been violated.
- HTTP `409` return code is used when a cancelReplace order partially succeeds. (e.g. if the cancellation of the order fails but the new order placement succeeds.)
- HTTP `429` return code is used when breaking a request rate limit.
- HTTP `418` return code is used when an IP has been auto-banned for continuing to send requests after receiving `429` codes.
- HTTP `5XX` return codes are used for internal errors; the issue is on
Binance's side.
It is important to **NOT** treat this as a failure operation; the execution status is **UNKNOWN** and could have been a success.

### Error Codes and Messages​

- If there is an error, the API will return an error with a message of the reason.

> The error payload on API and SAPI is as follows:


```
{  "code": -1121,  "msg": "Invalid symbol."}
```

- Specific error codes and messages defined in [Error Codes](/docs/margin_trading/general-info#error-codes) .

### General Information on Endpoints​

- For `GET` endpoints, parameters must be sent as a `query string` .
- For `POST` , `PUT` , and `DELETE` endpoints, the parameters may be sent as a `query string` or in the `request body` with content type `application/x-www-form-urlencoded` . You may mix parameters between both the `query string` and `request body` if you wish to do so.
- Parameters may be sent in any order.
- If a parameter sent in both the `query string` and `request body` , the `query string` parameter will be used.

---


## LIMITS​


### General Info on Limits​

- The following `intervalLetter` values for headers:
  - SECOND => S
  - MINUTE => M
  - HOUR => H
  - DAY => D
- `intervalNum` describes the amount of the interval. For example, `intervalNum` 5 with `intervalLetter` M means "Every 5 minutes".
- The `/api/v3/exchangeInfo` `rateLimits` array contains objects related to the exchange's `RAW_REQUESTS` , `REQUEST_WEIGHT` , and `ORDERS` rate limits. These are further defined in the `ENUM definitions` section under `Rate limiters (rateLimitType)` .
- A 429 will be returned when either request rate limit or order rate limit is violated.

### IP Limits​

- Every request will contain `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` in the response headers which has the current used weight for the IP for all request rate limiters defined.
- Each route has a `weight` which determines for the number of requests each endpoint counts for. Heavier endpoints and endpoints that do operations on multiple symbols will have a heavier `weight` .
- When a 429 is received, it's your obligation as an API to back off and not spam the API.
- **Repeatedly violating rate limits and/or failing to back off after receiving 429s will result in an automated IP ban (HTTP status 418).**
- IP bans are tracked and **scale in duration** for repeat offenders, **from 2 minutes to 3 days** .
- A `Retry-After` header is sent with a 418 or 429 responses and will give the **number of seconds** required to wait, in the case of a 429, to prevent a ban, or, in the case of a 418, until the ban is over.
- **The limits on the API are based on the IPs, not the API keys.**
We recommend using the websocket for getting data as much as possible, as this will not count to the request rate limit.

### Order Rate Limits​

- Every successful order response will contain a `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)` header which has the current order count for the account for all order rate limiters defined.
- When the order count exceeds the limit, you will receive a 429 error without the `Retry-After` header. Please check the Order Rate Limit rules using `GET api/v3/exchangeInfo` and wait for reactivation accordingly.
- Rejected/unsuccessful orders are not guaranteed to have `X-MBX-ORDER-COUNT-**` headers in the response.
- **The order rate limit is counted against each account**.
- To monitor order count usage, refer to GET `api/v3/rateLimit/order`

### Websocket Limits​

- WebSocket connections have a limit of 5 incoming messages per second. A message is considered:
  - A PING frame
  - A PONG frame
  - A JSON controlled message (e.g. subscribe, unsubscribe)
- A connection that goes beyond the limit will be disconnected; IPs that are repeatedly disconnected may be banned.
- A single connection can listen to a maximum of 1024 streams.
- There is a limit of **300 connections per attempt every 5 minutes per IP** .

### /api/ and /sapi/ Limit Introduction​


The `/api/*` and `/sapi/*` endpoints adopt either of two access limiting rules, IP limits or UID (account) limits.

- Endpoints related to `/api/*`:
  - According to the two modes of IP and UID (account) limit, each are independent.
  - Endpoints share the 6000 per minute limit based on IP.
  - Responses contain the header `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` , defining the weight used by the current IP.
  - Successful order responses contain the header `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)` , defining the order limit used by the UID.
- Endpoints related to `/sapi/*`:
  - Endpoints are marked according to IP or UID limit and their corresponding weight value.
  - Each endpoint with IP limits has an independent 12000 per minute limit.
  - Each endpoint with UID limits has an independent 180000 per minute limit.
  - Responses from endpoints with IP limits contain the header `X-SAPI-USED-IP-WEIGHT-1M` , defining the weight used by the current IP.
  - Responses from endpoints with UID limits contain the header `X-SAPI-USED-UID-WEIGHT-1M` , defining the weight used by the current UID.

---


## Data Sources​

- The API system is asynchronous, so some delay in the response is normal and expected.
- Each endpoint has a data source indicating where the data is being retrieved, and thus which endpoints have the most up-to-date response.

These are the three sources, ordered by which is has the most up-to-date response to the one with potential delays in updates.

- **Matching Engine** - the data is from the matching Engine
- **Memory** - the data is from a server's local or external memory
- **Database** - the data is taken directly from a database
Some endpoints can have more than 1 data source. (e.g. Memory => Database) 
This means that the endpoint will check the first Data Source, and if it cannot find the value it's looking for it will check the next one.

[]()


## Request Security​

- Each endpoint has a security type indicating required API key permissions, shown next to the endpoint name (e.g., [New order (TRADE)](/docs/margin_trading/general-info#place-new-order-trade) ).
- If unspecified, the security type is `NONE` .
- Except for `NONE` , all endpoints with a security type are considered `SIGNED` requests (i.e. including a `signature` ), except for [listenKey management](/docs/margin_trading/general-info#user-data-stream-requests) .
- Secure endpoints require a valid API key to be specified and authenticated.
  - API keys can be created on the [API Management](https://www.binance.com/en/support/faq/360002502072) page of your Binance account.
  - **Both API key and secret key are sensitive.** Never share them with anyone.
If you notice unusual activity in your account, immediately revoke all the keys and contact Binance support.
- API keys can be configured to allow access only to certain types of secure endpoints.
  - For example, you can have an API key with `TRADE` permission for trading,
while using a separate API key with `USER_DATA` permission to monitor your order status.
  - By default, an API key cannot `TRADE` . You need to enable trading in API Management first.

| Security Type | Description |
| --- | --- |
| NONE | Endpoint can be accessed freely. |
| TRADE | Endpoint requires sending a valid API-Key and signature. |
| MARGIN | Endpoint requires sending a valid API-Key and signature. |
| USER_DATA | Endpoint requires sending a valid API-Key and signature. |
| USER_STREAM | Endpoint requires sending a valid API-Key. |
| MARKET_DATA | Endpoint requires sending a valid API-Key. |

- `TRADE` , `MARGIN` and `USER_DATA` endpoints are `SIGNED` endpoints.

### SIGNED Endpoint security​

- `SIGNED` endpoints require an additional parameter, `signature` , to be sent in the `query string` or `request body` .

#### Signature Case Sensitivity​

- **HMAC:** Signatures generated using HMAC are **not case-sensitive** . This means the signature string can be verified regardless of letter casing.
- **RSA:** Signatures generated using RSA are **case-sensitive** .
- **Ed25519:** Signatures generated using Ed25519 are also **case-sensitive**

Please consult [SIGNED request example (HMAC)](/docs/margin_trading/general-info#hmac-keys), [SIGNED request example (RSA)](/docs/margin_trading/general-info#rsa-keys), and [SIGNED request example (Ed25519)](/docs/margin_trading/general-info#ed25519-keys) on how to compute signature, depending on which API key type you are using.


[]()


### Timing security​

- `SIGNED` requests also require a `timestamp` parameter which should be the current timestamp either in milliseconds or microseconds. (See [General API Information](/docs/margin_trading/general-info#general-api-information) )
- An additional optional parameter, `recvWindow` , specifies for how long the request stays valid and may only be specified in milliseconds.
  - `recvWindow` supports up to three decimal places of precision (e.g., 6000.346) so that microseconds may be specified.
  - If `recvWindow` is not sent, **it defaults to 5000 milliseconds** .
  - Maximum `recvWindow` is 60000 milliseconds.
- Request processing logic is as follows:

```
serverTime = getCurrentTime()if (timestamp < (serverTime + 1 second) && (serverTime - timestamp) <= recvWindow) {  // begin processing request  serverTime = getCurrentTime()  if (serverTime - timestamp) <= recvWindow {    // forward request to Matching Engine  } else {    // reject request  }  // finish processing request} else {  // reject request}
```


**Serious trading is about timing.** Networks can be unstable and unreliable,
which can lead to requests taking varying amounts of time to reach the
servers. With `recvWindow`, you can specify that the request must be
processed within a certain number of milliseconds or be rejected by the
server.


**It is recommended to use a small recvWindow of 5000 or less! The max cannot go beyond 60,000!**


### SIGNED Endpoint Examples for POST /api/v3/order​


#### HMAC Keys​


The signature payload of your request is the query string concatenated without separator to the HTTP body. Any non-ASCII character must be percent-encoded before signing.


Here is a step-by-step example of how to send a valid signed payload from the Linux command line using `echo`, `openssl`, and `curl`. There is one example with a symbol name comprised entirely of ASCII characters and one example with a symbol name containing non-ASCII characters.


Example API key and secret key:


| Key | Value |
| --- | --- |
| apiKey | vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A |
| secretKey | NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j |


**WARNING: DO NOT SHARE YOUR API KEY AND SECRET KEY WITH ANYONE.**


The example keys are provided here only for illustrative purposes.


Example of request with a symbol name comprised entirely of ASCII characters:


| Parameter | Value |
| --- | --- |
| symbol | LTCBTC |
| side | BUY |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.1 |
| recvWindow | 5000 |
| timestamp | 1499827319559 |


Example of a request with a symbol name containing non-ASCII characters:


| Parameter | Value |
| --- | --- |
| symbol | １２３４５６ |
| side | BUY |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.1 |
| recvWindow | 5000 |
| timestamp | 1499827319559 |


**Step 1: Construct the signature payload**

1. Format parameters as `parameter=value` pairs separated by `&` .
2. Percent-encode the string.

For the first set of example parameters (ASCII only), the `parameter=value` string should look like this:


```
symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559
```


After percent-encoding, the signature payload should look like this:


```
symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559
```


For the second set of example parameters (some non-ASCII characters), the `parameter=value` string should look like this:


```
symbol=１２３４５６&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559
```


After percent-encoding, the signature payload should look like this:


```
symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559
```


**Step 2: Compute the signature**

1. Use the `secretKey` of your API key as the signing key for the HMAC-SHA-256 algorithm.
2. Sign the signature payload constructed in Step 1.
3. Encode the HMAC-SHA-256 output as a hex string.

Note that `secretKey` and the payload are **case-sensitive**, while the resulting signature value is case-insensitive.


**Example commands**


For the first set of example parameters (ASCII only):


```
$ echo -n "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559" | openssl dgst -sha256 -hmac "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71
```


For the second set of example parameters (some non-ASCII characters):


```
$ echo -n "symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559" | openssl dgst -sha256 -hmac "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"e1353ec6b14d888f1164ae9af8228a3dbd508bc82eb867db8ab6046442f33ef3
```


**Step 3: Add signature to the request**


Complete the request by adding the `signature` parameter to the query string.


For the first set of example parameters (ASCII only):


```
curl -s -v -H "X-MBX-APIKEY: $apiKey" -X POST "https://api.binance.com/api/v3/order?symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559&signature=c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
```


For the second set of example parameters (some non-ASCII characters)


```
curl -s -v -H "X-MBX-APIKEY: $apiKey" -X POST "https://api.binance.com/api/v3/order?symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559&signature=e1353ec6b14d888f1164ae9af8228a3dbd508bc82eb867db8ab6046442f33ef3"
```


Here is a sample Bash script performing all the steps above:


```
apiKey="vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"secretKey="NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"payload="symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"# Sign the requestsignature=$(echo -n "$payload" | openssl dgst -sha256 -hmac "$secretKey")signature=${signature#*= }    # Keep only the part after the "= "# Send the requestcurl -H "X-MBX-APIKEY: $apiKey" -X POST "https://api.binance.com/api/v3/order?$payload&signature=$signature"
```


#### RSA Keys​


The signature payload of your request is the query string concatenated without separator to the HTTP body. Any non-ASCII character must be percent-encoded before signing.


To get your API key, you need to upload your RSA Public Key to your account and a corresponding API key will be provided for you.


Only `PKCS#8` keys are supported.


There is one example with a symbol name comprised entirely of ASCII characters and one example with a symbol name containing non-ASCII characters.


These examples assume the private key is stored in the file `./test-prv-key.pem`.


| Key | Value |
| --- | --- |
| apiKey | CAvIjXy3F44yW6Pou5k8Dy1swsYDWJZLeoK2r8G4cFDnE9nosRppc2eKc1T8TRTQ |


Example of request with a symbol name comprised entirely of ASCII characters:


| Parameter | Value |
| --- | --- |
| symbol | BTCUSDT |
| side | SELL |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.2 |
| timestamp | 1668481559918 |
| recvWindow | 5000 |


Example of a request with a symbol name containing non-ASCII characters:


| Parameter | Value |
| --- | --- |
| symbol | １２３４５６ |
| side | SELL |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.2 |
| timestamp | 1668481559918 |
| recvWindow | 5000 |


**Step 1: Construct the signature payload**

1. Format parameters as `parameter=value` pairs separated by `&` .
2. Percent-encode the string.

For the first set of example parameters (ASCII only), the `parameter=value` string should look like this:


```
symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


After percent-encoding, the signature payload should look like this:


```
symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


For the second set of example parameters (some non-ASCII characters), the `parameter=value` string should look like this:


```
symbol=１２３４５６=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


After percent-encoding, the signature payload should look like this:


```
symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


**Step 2: Compute the signature**

1. Sign the signature payload constructed in Step 1 using the RSASSA-PKCS1-v1_5 algorithm with SHA-256 hash function.
2. Encode the output in base64.

Note that the payload and the resulting `signature` are **case-sensitive**.


For the first set of example parameters (ASCII only):


```
$  echo -n 'symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000' | openssl dgst -sha256 -sign ./test-prv-key.pem | openssl enc -base64 -A | tr -d '\n'HZ8HOjiJ1s/igS9JA+n7+7Ti/ihtkRF5BIWcPIEluJP6tlbFM/Bf44LfZka/iemtahZAZzcO9TnI5uaXh3++lrqtNonCwp6/245UFWkiW1elpgtVAmJPbogcAv6rSlokztAfWk296ZJXzRDYAtzGH0gq7CgSJKfH+XxaCmR0WcvlKjNQnp12/eKXJYO4tDap8UCBLuyxDnR7oJKLHQHJLP0r0EAVOOSIbrFang/1WOq+Jaq4Efc4XpnTgnwlBbWTmhWDR1pvS9iVEzcSYLHT/fNnMRxFc7u+j3qI//5yuGuu14KR0MuQKKCSpViieD+fIti46sxPTsjSemoUKp0oXA==
```


For the second set of example parameters (some non-ASCII characters):


```
$  echo -n 'symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000' | openssl dgst -sha256 -sign ./test-prv-key.pem | openssl enc -base64 -A | tr -d '\n'qJtv66wyp/1mZE+mIFAAMUoTe8xkmLN7/eAZjuC9x1ocxovItHLl/sNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M+JNIMz5UFxfeA53rXjFlvsyH1Sig+OuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C/QMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt/GuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng==
```

1. Percent-encode the base64 string.

For the first set of example parameters (ASCII only):


```
HZ8HOjiJ1s%2FigS9JA%2Bn7%2B7Ti%2FihtkRF5BIWcPIEluJP6tlbFM%2FBf44LfZka%2FiemtahZAZzcO9TnI5uaXh3%2B%2BlrqtNonCwp6%2F245UFWkiW1elpgtVAmJPbogcAv6rSlokztAfWk296ZJXzRDYAtzGH0gq7CgSJKfH%2BXxaCmR0WcvlKjNQnp12%2FeKXJYO4tDap8UCBLuyxDnR7oJKLHQHJLP0r0EAVOOSIbrFang%2F1WOq%2BJaq4Efc4XpnTgnwlBbWTmhWDR1pvS9iVEzcSYLHT%2FfNnMRxFc7u%2Bj3qI%2F%2F5yuGuu14KR0MuQKKCSpViieD%2BfIti46sxPTsjSemoUKp0oXA%3D%3D
```


For the second set of example parameters (some non-ASCII characters):


```
qJtv66wyp%2F1mZE%2BmIFAAMUoTe8xkmLN7%2FeAZjuC9x1ocxovItHLl%2FsNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M%2BJNIMz5UFxfeA53rXjFlvsyH1Sig%2BOuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C%2FQMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt%2FGuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng%3D%3D
```


**Step 3: Add signature to the request**


Complete the request by adding the `signature` parameter to the query string.


For the first set of example parameters (ASCII only):


```
curl -H "X-MBX-APIKEY: CAvIjXy3F44yW6Pou5k8Dy1swsYDWJZLeoK2r8G4cFDnE9nosRppc2eKc1T8TRTQ" -X POST 'https://api.binance.com/api/v3/order?symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000&signature=HZ8HOjiJ1s%2FigS9JA%2Bn7%2B7Ti%2FihtkRF5BIWcPIEluJP6tlbFM%2FBf44LfZka%2FiemtahZAZzcO9TnI5uaXh3%2B%2BlrqtNonCwp6%2F245UFWkiW1elpgtVAmJPbogcAv6rSlokztAfWk296ZJXzRDYAtzGH0gq7CgSJKfH%2BXxaCmR0WcvlKjNQnp12%2FeKXJYO4tDap8UCBLuyxDnR7oJKLHQHJLP0r0EAVOOSIbrFang%2F1WOq%2BJaq4Efc4XpnTgnwlBbWTmhWDR1pvS9iVEzcSYLHT%2FfNnMRxFc7u%2Bj3qI%2F%2F5yuGuu14KR0MuQKKCSpViieD%2BfIti46sxPTsjSemoUKp0oXA%3D%3D'
```


For the second set of example parameters (some non-ASCII characters):


```
curl -H "X-MBX-APIKEY: CAvIjXy3F44yW6Pou5k8Dy1swsYDWJZLeoK2r8G4cFDnE9nosRppc2eKc1T8TRTQ" -X POST 'https://api.binance.com/api/v3/order?symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000&signature=qJtv66wyp%2F1mZE%2BmIFAAMUoTe8xkmLN7%2FeAZjuC9x1ocxovItHLl%2FsNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M%2BJNIMz5UFxfeA53rXjFlvsyH1Sig%2BOuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C%2FQMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt%2FGuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng%3D%3D'
```


Here is a sample Bash script performing all the steps above:


```
function rawurlencode {  local string="${1}"  local strlen=${#string}  local encoded=""  local pos c o  for (( pos=0 ; pos<strlen ; pos++ )); do     c=${string:$pos:1}     case "$c" in        [-_.~a-zA-Z0-9] ) o="${c}" ;;        * )               printf -v o '%%%02x' "'$c"     esac     encoded+="${o}"  done  echo "${encoded}"}API_KEY="put your own API Key here"PRIVATE_KEY_PATH="test-prv-key.pem"# Set up the request:API_METHOD="POST"API_CALL="api/v3/order"API_PARAMS="symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2"# Sign the request:timestamp=$(date +%s000)api_params_with_timestamp="$API_PARAMS&timestamp=$timestamp"rawSignature=$(echo -n $api_params_with_timestamp | openssl dgst -keyform PEM -sha256 -sign $PRIVATE_KEY_PATH | openssl enc -base64 | tr -d '\n')# Percent-encode the signaturesignature=$(rawurlencode "$rawSignature")# Send the request:curl -H "X-MBX-APIKEY: $API_KEY" -X "$API_METHOD" \    "https://api.binance.com/$API_CALL?$api_params_with_timestamp" \    --data-urlencode "signature=$signature"
```


#### Ed25519 Keys​


**Note: It is highly recommended to use Ed25519 API keys as it should provide the best performance and security out of all supported key types.**


The signature payload of your request is the query string concatenated without separator to the HTTP body. Any non-ASCII character must be percent-encoded before signing.


There is one example with a symbol name comprised entirely of ASCII characters and one example with a symbol name containing non-ASCII characters.


These examples assume the private key is stored in the file `./test-prv-key.pem`.


| Key | Value |
| --- | --- |
| apiKey | 4yNzx3yWC5bS6YTwEkSRaC0nRmSQIIStAUOh1b6kqaBrTLIhjCpI5lJH8q8R8WNO |


Example of request with a symbol name comprised entirely of ASCII characters.


| Parameter | Value |
| --- | --- |
| symbol | BTCUSDT |
| side | SELL |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.2 |
| timestamp | 1668481559918 |
| recvWindow | 5000 |


Example of a request with a symbol name containing non-ASCII characters.


| Parameter | Value |
| --- | --- |
| symbol | １２３４５６ |
| side | SELL |
| type | LIMIT |
| timeInForce | GTC |
| quantity | 1 |
| price | 0.2 |
| timestamp | 1668481559918 |
| recvWindow | 5000 |


**Step 1: Construct the signature payload**

1. Format parameters as `parameter=value` pairs separated by `&` .
2. Percent-encode the string.

For the first set of example parameters (ASCII only), the `parameter=value` string should look like this:


```
symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


After percent-encoding, the signature payload should look like this:


```
symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


For the second set of example parameters (some non-ASCII characters), the `parameter=value` string should look like this:


```
symbol=１２３４５６&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


After percent-encoding, the signature payload should look like this:


```
symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000
```


**Step 2: Compute the signature**

1. Sign the payload.
2. Encode the output as a base64 string.

Note that the payload and the resulting `signature` are **case-sensitive**.


For the first set of example parameters (ASCII only):


```
echo -n "symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000" | openssl dgst -keyform PEM -sha256 -sign ./test-prv-key.pem | openssl enc -base64 | tr -d '\n'HaZnek7KOGa/k5+f6Q1nw8lzMUpo36mRVvvLHCMUCXxlmdQQGZge1luAUKnleD/DYeD19YrqzeHbb6xU3MkSIXKhAO1MaYq48uGVYb3vJScEZVOutgMInrZzUcCWNulNkfcbmExSiymCZ5xQBw5QDuzpuDFqRZ1Xt+BZxEHBN9OYQKpoe0+ovjnXyVOaH8VUKhE/ghUWnThrXJr+hmSc5t7ggjiVPQc7pGn3qSNGCQwdpkQC9GHMr/r+8n6qeEKMYB5j/1wC4d8Jae8FQiU8xcXR0NlUgV2LAw61/ZJv5BTJpa+z5Lv1W9v6jHQWRX2O8uaG3KU/lR3spR7+oGlWOw=
```


For the second set of example parameters (some non-ASCII characters):


```
echo -n "symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000" | openssl dgst -keyform PEM -sha256 -sign ./test-prv-key.pem | openssl enc -base64 | tr -d '\n'qJtv66wyp/1mZE+mIFAAMUoTe8xkmLN7/eAZjuC9x1ocxovItHLl/sNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M+JNIMz5UFxfeA53rXjFlvsyH1Sig+OuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C/QMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt/GuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng==
```

1. Percent-encode the base64 string.

For the first set of example parameters (ASCII only):


```
HaZnek7KOGa%2Fk5%2Bf6Q1nw8lzMUpo36mRVvvLHCMUCXxlmdQQGZge1luAUKnleD%2FDYeD19YrqzeHbb6xU3MkSIXKhAO1MaYq48uGVYb3vJScEZVOutgMInrZzUcCWNulNkfcbmExSiymCZ5xQBw5QDuzpuDFqRZ1Xt%2BBZxEHBN9OYQKpoe0%2BovjnXyVOaH8VUKhE%2FghUWnThrXJr%2BhmSc5t7ggjiVPQc7pGn3qSNGCQwdpkQC9GHMr%2Fr%2B8n6qeEKMYB5j%2F1wC4d8Jae8FQiU8xcXR0NlUgV2LAw61%2FZJv5BTJpa%2Bz5Lv1W9v6jHQWRX2O8uaG3KU%2FlR3spR7%2BoGlWOw%3D
```


For the second set of example parameters (some non-ASCII characters):


```
qJtv66wyp%2F1mZE%2BmIFAAMUoTe8xkmLN7%2FeAZjuC9x1ocxovItHLl%2FsNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M%2BJNIMz5UFxfeA53rXjFlvsyH1Sig%2BOuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C%2FQMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt%2FGuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng%3D%3D
```


**Step 3: Add signature to the request**


Complete the request by adding the `signature` parameter to the query string.


For the first set of example parameters (ASCII only):


```
curl -H "X-MBX-APIKEY: 4yNzx3yWC5bS6YTwEkSRaC0nRmSQIIStAUOh1b6kqaBrTLIhjCpI5lJH8q8R8WNO" -X POST 'https://api.binance.com/api/v3/order?symbol=BTCUSDT&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000&signature=HaZnek7KOGa%2Fk5%2Bf6Q1nw8lzMUpo36mRVvvLHCMUCXxlmdQQGZge1luAUKnleD%2FDYeD19YrqzeHbb6xU3MkSIXKhAO1MaYq48uGVYb3vJScEZVOutgMInrZzUcCWNulNkfcbmExSiymCZ5xQBw5QDuzpuDFqRZ1Xt%2BBZxEHBN9OYQKpoe0%2BovjnXyVOaH8VUKhE%2FghUWnThrXJr%2BhmSc5t7ggjiVPQc7pGn3qSNGCQwdpkQC9GHMr%2Fr%2B8n6qeEKMYB5j%2F1wC4d8Jae8FQiU8xcXR0NlUgV2LAw61%2FZJv5BTJpa%2Bz5Lv1W9v6jHQWRX2O8uaG3KU%2FlR3spR7%2BoGlWOw%3D'
```


For the second set of example parameters (some non-ASCII characters):


```
curl -H "X-MBX-APIKEY: 4yNzx3yWC5bS6YTwEkSRaC0nRmSQIIStAUOh1b6kqaBrTLIhjCpI5lJH8q8R8WNO" -X POST 'https://api.binance.com/api/v3/order?symbol=%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94%EF%BC%95%EF%BC%96&&side=SELL&type=LIMIT&timeInForce=GTC&quantity=1&price=0.2&timestamp=1668481559918&recvWindow=5000&signature=qJtv66wyp%2F1mZE%2BmIFAAMUoTe8xkmLN7%2FeAZjuC9x1ocxovItHLl%2FsNK7Wq8QjgiHqGn0bb8P7yVvGBEd1gFe71NQ8aM0M%2BJNIMz5UFxfeA53rXjFlvsyH1Sig%2BOuO9Nz5nhCaJ6bEfj2iuv7w27pB3L8MVqmoCi6D9C%2FQMiLxtPaR70CxtnvoOlIgPmpv2bQy029A31NEK19ieVLkoyp1EUkXRaX3v0mohx8yMnUG1dhX9nUg3Oy8TYZ03DQy7kHDGkMKisNX7rt%2FGuGx1HIgjFclDGLsbAFIodvSLjm9FbseasMELoxlAJDlwRnW8zo5sQmL0Fz7ao935QBynrng%3D%3D'
```


Here is a sample Python script performing all the steps above:


```
#!/usr/bin/env python3import base64import requestsimport timeimport urllib.parsefrom cryptography.hazmat.primitives.serialization import load_pem_private_key# Set up authenticationAPI_KEY='put your own API Key here'PRIVATE_KEY_PATH='test-prv-key.pem'# Load the private key.# In this example the key is expected to be stored without encryption,# but we recommend using a strong password for improved security.with open(PRIVATE_KEY_PATH, 'rb') as f:    private_key = load_pem_private_key(data=f.read(), password=None)# Set up the request parametersparams = {    'symbol':       'BTCUSDT',    'side':         'SELL',    'type':         'LIMIT',    'timeInForce':  'GTC',    'quantity':     '1.0000000',    'price':        '0.20',}# Timestamp the requesttimestamp = int(time.time() * 1000) # UNIX timestamp in millisecondsparams['timestamp'] = timestamp# Sign the requestpayload = urllib.parse.urlencode(params, encoding='UTF-8')signature = base64.b64encode(private_key.sign(payload.encode('ASCII')))params['signature'] = signature# Send the requestheaders = {    'X-MBX-APIKEY': API_KEY,}response = requests.post(    'https://api.binance.com/api/v3/order',    headers=headers,    data=params,)print(response.json())
```


A sample Bash script containing similar steps is available in the right side.


---
