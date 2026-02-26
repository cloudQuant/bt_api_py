
# Query Margin Account's all OCO (USER_DATA)


## API Description​


Retrieves all OCO for a specific margin account based on provided optional parameters


## HTTP Request​


GET `/sapi/v1/margin/allOrderList`


## Request Weight​


**200(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| symbol | STRING | NO | mandatory for isolated margin, not supported for cross margin |
| fromId | LONG | NO | If supplied, neither startTime or endTime can be provided |
| startTime | LONG | NO |  |
| endTime | LONG | NO |  |
| limit | INT | NO | Default Value: 500; Max Value: 1000 |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[  {    "orderListId": 29,    "contingencyType": "OCO",    "listStatusType": "EXEC_STARTED",    "listOrderStatus": "EXECUTING",    "listClientOrderId": "amEEAXryFzFwYF1FeRpUoZ",    "transactionTime": 1565245913483,    "symbol": "LTCBTC",    "isIsolated": true,       // if isolated margin    "orders": [      {        "symbol": "LTCBTC",        "orderId": 4,        "clientOrderId": "oD7aesZqjEGlZrbtRpy5zB"      },      {        "symbol": "LTCBTC",        "orderId": 5,        "clientOrderId": "Jr1h6xirOxgeJOUuYQS7V3"      }    ]  },  {    "orderListId": 28,    "contingencyType": "OCO",    "listStatusType": "EXEC_STARTED",    "listOrderStatus": "EXECUTING",    "listClientOrderId": "hG7hFNxJV6cZy3Ze4AUT4d",    "transactionTime": 1565245913407,    "symbol": "LTCBTC",    "orders": [      {        "symbol": "LTCBTC",        "orderId": 2,        "clientOrderId": "j6lFOfbmFMRjTYA7rRJ0LP"      },      {        "symbol": "LTCBTC",        "orderId": 3,        "clientOrderId": "z0KCjOdditiLS5ekAFtK81"      }    ]  }]
```
