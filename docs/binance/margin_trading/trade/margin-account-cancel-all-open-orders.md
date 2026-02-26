
# Margin Account Cancel all Open Orders on a Symbol (TRADE)


## API Description​


Cancels all active orders on a symbol for margin account.


This includes OCO orders.


## HTTP Request​


DELETE /sapi/v1/margin/openOrders


## Request Weight​


**1**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
[  {    "symbol": "BTCUSDT",    "isIsolated": true,       // if isolated margin    "origClientOrderId": "E6APeyTJvkMvLMYMqu1KQ4",    "orderId": 11,    "orderListId": -1,    "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",    "price": "0.089853",    "origQty": "0.178622",    "executedQty": "0.000000",    "cummulativeQuoteQty": "0.000000",    "status": "CANCELED",    "timeInForce": "GTC",    "type": "LIMIT",    "side": "BUY",    "selfTradePreventionMode": "NONE"  },  {    "symbol": "BTCUSDT",    "isIsolated": false,       // if isolated margin    "origClientOrderId": "A3EF2HCwxgZPFMrfwbgrhv",    "orderId": 13,    "orderListId": -1,    "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",    "price": "0.090430",    "origQty": "0.178622",    "executedQty": "0.000000",    "cummulativeQuoteQty": "0.000000",    "status": "CANCELED",    "timeInForce": "GTC",    "type": "LIMIT",    "side": "BUY",    "selfTradePreventionMode": "NONE"  },  {    "orderListId": 1929,    "contingencyType": "OCO",    "listStatusType": "ALL_DONE",    "listOrderStatus": "ALL_DONE",    "listClientOrderId": "2inzWQdDvZLHbbAmAozX2N",    "transactionTime": 1585230948299,    "symbol": "BTCUSDT",    "isIsolated": true,       // if isolated margin    "orders": [      {        "symbol": "BTCUSDT",        "orderId": 20,        "clientOrderId": "CwOOIPHSmYywx6jZX77TdL"      },      {        "symbol": "BTCUSDT",        "orderId": 21,        "clientOrderId": "461cPg51vQjV3zIMOXNz39"      }    ],    "orderReports": [      {        "symbol": "BTCUSDT",        "origClientOrderId": "CwOOIPHSmYywx6jZX77TdL",        "orderId": 20,        "orderListId": 1929,        "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",        "price": "0.668611",        "origQty": "0.690354",        "executedQty": "0.000000",        "cummulativeQuoteQty": "0.000000",        "status": "CANCELED",        "timeInForce": "GTC",        "type": "STOP_LOSS_LIMIT",        "side": "BUY",        "stopPrice": "0.378131",        "icebergQty": "0.017083"      },      {        "symbol": "BTCUSDT",        "origClientOrderId": "461cPg51vQjV3zIMOXNz39",        "orderId": 21,        "orderListId": 1929,        "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",        "price": "0.008791",        "origQty": "0.690354",        "executedQty": "0.000000",        "cummulativeQuoteQty": "0.000000",        "status": "CANCELED",        "timeInForce": "GTC",        "type": "LIMIT_MAKER",        "side": "BUY",        "icebergQty": "0.639962"      }    ]  }]
```
