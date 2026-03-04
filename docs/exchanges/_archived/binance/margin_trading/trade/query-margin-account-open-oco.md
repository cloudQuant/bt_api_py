
# Query Margin Account's Open OCO (USER_DATA)


## API Description​


Query Margin Account's Open OCO


## HTTP Request​


GET `/sapi/v1/margin/openOrderList`


## Request Weight​


- *10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |

| symbol | STRING | NO | mandatory for isolated margin, not supported for cross margin |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
[{    "orderListId": 31,    "contingencyType": "OCO",    "listStatusType": "EXEC_STARTED",    "listOrderStatus": "EXECUTING",    "listClientOrderId": "wuB13fmulKj3YjdqWEcsnp",    "transactionTime": 1565246080644,    "symbol": "LTCBTC",    "isIsolated": false,       // if isolated margin    "orders": [{        "symbol": "LTCBTC",        "orderId": 4,        "clientOrderId": "r3EH2N76dHfLoSZWIUw1bT"      },      {        "symbol": "LTCBTC",        "orderId": 5,        "clientOrderId": "Cv1SnyPD3qhqpbjpYEHbd2"      }  ]  }]

```bash
