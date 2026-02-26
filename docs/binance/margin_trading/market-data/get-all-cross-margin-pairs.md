
# Get All Cross Margin Pairs (MARKET_DATA)


## API Description​


Get All Cross Margin Pairs


## HTTP Request​


GET `/sapi/v1/margin/allPairs`


## Request Weight​


**1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | NO |  |


## Response Example​


```
[    {        "base": "BNB",        "id": 351637150141315861,        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "BNBBTC"    },    {        "base": "TRX",        "id": 351637923235429141,        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "TRXBTC",        "delistTime": 1704973040    },    {        "base": "XRP",        "id": 351638112213990165,        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "XRPBTC"    },    {        "base": "ETH",        "id": 351638524530850581,        "isBuyAllowed": true,        "isMarginTrade": true,        "isSellAllowed": true,        "quote": "BTC",        "symbol": "ETHBTC"    }]
```
