
# Query Isolated Margin Fee Data (USER_DATA)


## API Description​


Get isolated margin fee data collection with any vip level or user's current specific data as [https://www.binance.com/en/margin-fee](https://www.binance.com/en/margin-fee)


## HTTP Request​


GET `/sapi/v1/margin/isolatedMarginData`


## Request Weight​


**1 when a single is specified;(IP)**
**10 when the symbol parameter is omitted(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| vipLevel | INT | NO | User's current specific margin data will be returned if vipLevel is omitted |
| symbol | STRING | NO |  |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[    {        "vipLevel": 0,        "symbol": "BTCUSDT",        "leverage": "10",        "data": [            {                "coin": "BTC",                "dailyInterest": "0.00026125",                "borrowLimit": "270"            },            {                "coin": "USDT",                "dailyInterest": "0.000475",                "borrowLimit": "2100000"            }        ]    }]
```
