
# Get All Isolated Margin Symbol(MARKET_DATA)


## API Description​


Get All Isolated Margin Symbol


## HTTP Request​


GET `/sapi/v1/margin/isolated/allPairs`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | NO |  |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[    {        "base": "BNB",        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "BNBBTC"         },    {        "base": "TRX",        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "TRXBTC"        }]
```
