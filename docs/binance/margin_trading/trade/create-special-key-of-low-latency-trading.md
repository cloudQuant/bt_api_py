
# Create Special Key(Low-Latency Trading)(TRADE)


## API Description​

- Binance Margin offers low-latency trading through a [special key](<https://www.binance.com/en/support/faq/frequently-asked-questions-on-margin-special-api-key-3208663e900d4d2e9fec4140e1832f4e)> , available exclusively to users with VIP level 4 or higher.
- If you are VIP level 3 or below, please contact your VIP manager for eligibility criterias.**

- *Supported Products:**

- Cross Margin
- Isolated Margin
- Portfolio Margin Pro
- Cross Margin Pro (Additional agreement required and subject to meeting eligibility criteria)

- *Unsupported Products:**

- Portfolio Margin

We support several types of API keys:

- Ed25519 (recommended)
- HMAC
- RSA

We recommend to **use Ed25519 API keys**as it should provide the best performance and security out of all supported key types. We accept PKCS#8 (BEGIN PUBLIC KEY). For how to generate an RSA key pair to send API requests on Binance. Please refer to the document below [FAQ](<https://www.binance.com/en/support/faq/how-to-generate-an-rsa-key-pair-to-send-api-requests-on-binance-2b79728f331e43079b27440d9d15c5db)> .


## How to use the Margin Special Key​

- Use the below `sapi` endpoint to create your margin special API Key.
- For accessing the Cross Margin account, do not send the `symbol` parameter.
- For accessing the Isolated Margin account(s), pass the relevant `symbol` parameter in the API Key creation request.
- Use the generated API Key (and Secret key, if applicable) to perform margin trading and listenKey generation via**Spot** REST API ( `<https://api.binance.com/api/v3/*`> ) endpoints.

Read [REST API](<https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#signed-trade-and-user_data-endpoint-security)> or [WebSocket API](<https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md#request-security)> documentation to learn how to use different API keys


You need to enable Permits “Enable Spot & Margin Trading” option for the API Key which requests this endpoint.


## HTTP Request​


POST `/sapi/v1/margin/apiKey`


## Request Weight​


- *1(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| apiName | STRING | YES |  |

| symbol | STRING | NO | isolated margin pair |

| ip | STRING | NO | Can be added in batches, separated by commas. Max 30 for an API key |

| publicKey | STRING | NO | 1. If publicKey is inputted it will create an RSA or Ed25519 key. 2. Need to be encoded to URL-encoded format |

| permissionMode | enum | NO | This parameter is only for the Ed25519 API key, and does not effact for other encryption methods. The value can be TRADE (TRADE for all permissions) or READ (READ for USER_DATA, FIX_API_READ_ONLY). The default value is TRADE. |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
{  "apiKey":"npOzOAeLVgr2TuxWfNo43AaPWpBbJEoKezh1o8mSQb6ryE2odE11A4AoVlJbQoGx",  "secretKey":"87ssWB7azoy6ACRfyp6OVOL5U3rtZptX31QWw2kWjl1jHEYRbyM1pd6qykRBQw8p" //secretKey will be null when creating an RSA key  "type": "HMAC_SHA256"   //HMAC_SHA256 or RSA}

```bash
Error Code Description

- **UNSUPPORTED_OPERATION**: Portfolio Margin is an unsupported product, please change the account type to a supported margin product.
- **Forbidden** :  Cross Margin Pro accounts require additional agreements, please contact your relationship manager.
