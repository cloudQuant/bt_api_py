
# Query Margin Interest Rate History (USER_DATA)


## API Description​


Query Margin Interest Rate History


## HTTP Request​


GET `/sapi/v1/margin/interestRateHistory`


## Request Weight​


**1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | YES |  |
| vipLevel | INT | NO | Default: user's vip level |
| startTime | LONG | NO | Default: 7 days ago |
| endTime | LONG | NO | Default: present. Maximum range: 1 months. |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[    {        "asset": "BTC",        "dailyInterestRate": "0.00025000",        "timestamp": 1611544731000,        "vipLevel": 1        },    {        "asset": "BTC",        "dailyInterestRate": "0.00035000",        "timestamp": 1610248118000,        "vipLevel": 1        }]
```
