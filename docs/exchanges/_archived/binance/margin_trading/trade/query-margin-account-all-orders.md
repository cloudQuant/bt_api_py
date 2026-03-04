
# Query Margin Account's All Orders (USER_DATA)


## API Description​


Query Margin Account's All Orders


## HTTP Request​


GET `/sapi/v1/margin/allOrders`


## Request Weight​


- *200(IP)**


## Request Limit​


- *60times/min per IP**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| symbol | STRING | YES |  |

| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |

| orderId | LONG | NO |  |

| startTime | LONG | NO |  |

| endTime | LONG | NO |  |

| limit | INT | NO | Default 500; max 500. |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |

- If orderId is set, it will get orders >= that orderId. Otherwise the orders within 24 hours are returned.
- For some historical orders cummulativeQuoteQty will be < 0, meaning the data is not available at this time.
- Less than 24 hours between startTime and endTime.

## Response Example​


```bash
[      {          "clientOrderId": "D2KDy4DIeS56PvkM13f8cP",          "cummulativeQuoteQty": "0.00000000",          "executedQty": "0.00000000",          "icebergQty": "0.00000000",          "isWorking": false,          "orderId": 41295,          "origQty": "5.31000000",          "price": "0.22500000",          "side": "SELL",          "status": "CANCELED",          "stopPrice": "0.18000000",          "symbol": "BNBBTC",          "isIsolated": false,          "time": 1565769338806,          "timeInForce": "GTC",          "type": "TAKE_PROFIT_LIMIT",          "selfTradePreventionMode": "NONE",          "updateTime": 1565769342148      },      {          "clientOrderId": "gXYtqhcEAs2Rn9SUD9nRKx",          "cummulativeQuoteQty": "0.00000000",          "executedQty": "0.00000000",          "icebergQty": "1.00000000",          "isWorking": true,          "orderId": 41296,          "origQty": "6.65000000",          "price": "0.18000000",          "side": "SELL",          "status": "CANCELED",          "stopPrice": "0.00000000",          "symbol": "BNBBTC",          "isIsolated": false,          "time": 1565769348687,          "timeInForce": "GTC",          "type": "LIMIT",          "selfTradePreventionMode": "NONE",          "updateTime": 1565769352226      }]

```bash
