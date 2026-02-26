
# Get All Margin Assets (MARKET_DATA)


## API Description​


Get All Margin Assets.


## HTTP Request​


GET `/sapi/v1/margin/allAssets`


## Request Weight​


**1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | NO |  |


## Response Example​


```
[  {    "assetFullName": "USD coin",    "assetName": "USDC",    "isBorrowable": true,    "isMortgageable": true,    "userMinBorrow": "0.00000000",    "userMinRepay": "0.00000000",    "delistTime": 1704973040  }]
```
