
# Get Small Liability Exchange History (USER_DATA)


## API Description​


Get Small liability Exchange History


## HTTP Request​


GET `/sapi/v1/margin/exchange-small-liability-history`


## Request Weight​


**100(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| current | INT | YES | Currently querying page. Start from 1. Default:1 |
| size | INT | YES | Default:10, Max:100 |
| startTime | LONG | NO | Default: 30 days from current timestamp |
| endTime | LONG | NO | Default: present timestamp |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |


## Response Example​


```
{    "total": 1,    "rows": [      {        "asset": "ETH",        "amount": "0.00083434",        "targetAsset": "BUSD",        "targetAmount": "1.37576819",        "bizType": "EXCHANGE_SMALL_LIABILITY",        "timestamp": 1672801339253      }    ]}
```
