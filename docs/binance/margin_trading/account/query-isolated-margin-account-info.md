
# Query Isolated Margin Account Info (USER_DATA)


## API Description​


Query Isolated Margin Account Info


## HTTP Request​


GET `/sapi/v1/margin/isolated/account`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbols | STRING | NO | Max 5 symbols can be sent; separated by ",". e.g. "BTCUSDT,BNBUSDT,ADAUSDT" |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |

- If "symbols" is not sent, all isolated assets will be returned.
- If "symbols" is sent, only the isolated assets of the sent symbols will be returned.

## Response Example​


> If "symbols" is not sent


```
{   "assets":[      {        "baseAsset":         {          "asset": "BTC",          "borrowEnabled": true,          "borrowed": "0.00000000",          "free": "0.00000000",          "interest": "0.00000000",          "locked": "0.00000000",          "netAsset": "0.00000000",          "netAssetOfBtc": "0.00000000",          "repayEnabled": true,          "totalAsset": "0.00000000"        },        "quoteAsset":         {          "asset": "USDT",          "borrowEnabled": true,          "borrowed": "0.00000000",          "free": "0.00000000",          "interest": "0.00000000",          "locked": "0.00000000",          "netAsset": "0.00000000",          "netAssetOfBtc": "0.00000000",          "repayEnabled": true,          "totalAsset": "0.00000000"        },        "symbol": "BTCUSDT",        "isolatedCreated": true,         "enabled": true, // true-enabled, false-disabled        "marginLevel": "0.00000000",         "marginLevelStatus": "EXCESSIVE", // "EXCESSIVE", "NORMAL", "MARGIN_CALL", "PRE_LIQUIDATION", "FORCE_LIQUIDATION"        "marginRatio": "0.00000000",        "indexPrice": "10000.00000000",        "liquidatePrice": "1000.00000000",        "liquidateRate": "1.00000000",        "tradeEnabled": true      }    ],    "totalAssetOfBtc": "0.00000000",    "totalLiabilityOfBtc": "0.00000000",    "totalNetAssetOfBtc": "0.00000000" }
```


> If "symbols" is sent


```
{   "assets":[      {        "baseAsset":         {          "asset": "BTC",          "borrowEnabled": true,          "borrowed": "0.00000000",          "free": "0.00000000",          "interest": "0.00000000",          "locked": "0.00000000",          "netAsset": "0.00000000",          "netAssetOfBtc": "0.00000000",          "repayEnabled": true,          "totalAsset": "0.00000000"        },        "quoteAsset":         {          "asset": "USDT",          "borrowEnabled": true,          "borrowed": "0.00000000",          "free": "0.00000000",          "interest": "0.00000000",          "locked": "0.00000000",          "netAsset": "0.00000000",          "netAssetOfBtc": "0.00000000",          "repayEnabled": true,          "totalAsset": "0.00000000"        },        "symbol": "BTCUSDT",        "isolatedCreated": true,         "enabled": true, // true-enabled, false-disabled        "marginLevel": "0.00000000",         "marginLevelStatus": "EXCESSIVE", // "EXCESSIVE", "NORMAL", "MARGIN_CALL", "PRE_LIQUIDATION", "FORCE_LIQUIDATION"        "marginRatio": "0.00000000",        "indexPrice": "10000.00000000",        "liquidatePrice": "1000.00000000",        "liquidateRate": "1.00000000",        "tradeEnabled": true      }    ]}
```
