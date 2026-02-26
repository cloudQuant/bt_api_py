
# Get Delist Schedule (MARKET_DATA)


## API Description​


Get tokens or symbols delist schedule for cross margin and isolated margin


## HTTP Request​


GET `/sapi/v1/margin/delist-schedule`


## Request Weight(IP)​


**100**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |


## Response Example​


```
[  {    "delistTime": 1686161202000,    "crossMarginAssets": [      "BTC",      "USDT"    ],    "isolatedMarginSymbols": [      "ADAUSDT",      "BNBUSDT"    ]  },  {    "delistTime": 1686222232000,    "crossMarginAssets": [      "ADA"    ],    "isolatedMarginSymbols": []  }]
```
