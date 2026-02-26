
# Query Margin Account's OCO (USER_DATA)


## API Description​


Retrieves a specific OCO based on provided optional parameters


## HTTP Request​


GET `/sapi/v1/margin/orderList`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| symbol | STRING | NO | mandatory for isolated margin, not supported for cross margin |
| orderListId | LONG | NO | Either orderListId or origClientOrderId must be provided |
| origClientOrderId | STRING | NO | Either orderListId or origClientOrderId must be provided |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{  "orderListId": 27,  "contingencyType": "OCO",  "listStatusType": "EXEC_STARTED",  "listOrderStatus": "EXECUTING",  "listClientOrderId": "h2USkA5YQpaXHPIrkd96xE",  "transactionTime": 1565245656253,  "symbol": "LTCBTC",  "isIsolated": false,       // if isolated margin  "orders": [    {      "symbol": "LTCBTC",      "orderId": 4,      "clientOrderId": "qD1gy3kc3Gx0rihm9Y3xwS"    },    {      "symbol": "LTCBTC",      "orderId": 5,      "clientOrderId": "ARzZ9I00CPM8i3NhmU9Ega"    }  ]}
```
