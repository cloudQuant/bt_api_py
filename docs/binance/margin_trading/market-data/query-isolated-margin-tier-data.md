
# Query Isolated Margin Tier Data (USER_DATA)


## API Description​


Get isolated margin tier data collection with any tier as [https://www.binance.com/en/margin-data](https://www.binance.com/en/margin-data)


## HTTP Request​


GET `/sapi/v1/margin/isolatedMarginTier`


## Request Weight​


**1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| tier | INTEGER | NO | All margin tier data will be returned if tier is omitted |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[    {        "symbol": "BTCUSDT",        "tier": 1,        "effectiveMultiple": "10",        "initialRiskRatio": "1.111",        "liquidationRiskRatio": "1.05",        "baseAssetMaxBorrowable": "9",        "quoteAssetMaxBorrowable": "70000"    }]
```
