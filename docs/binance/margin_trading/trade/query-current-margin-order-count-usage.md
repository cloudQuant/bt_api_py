
# Query Current Margin Order Count Usage (TRADE)


## API Description​


Displays the user's current margin order count usage for all intervals.


## HTTP Request​


GET `/sapi/v1/margin/rateLimit/order`


## Request Weight​


**20(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| symbol | STRING | NO | isolated symbol, mandatory for isolated margin |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[  {    "rateLimitType": "ORDERS",    "interval": "SECOND",    "intervalNum": 10,    "limit": 10000,    "count": 0  },  {    "rateLimitType": "ORDERS",    "interval": "DAY",    "intervalNum": 1,    "limit": 20000,    "count": 0  }]
```
