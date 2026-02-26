
# Query Margin Account's Open Orders (USER_DATA)


## API Description​


Query Margin Account's Open Orders


## HTTP Request​


GET `/sapi/v1/margin/openOrders`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | NO |  |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |

- If the symbol is not sent, orders for all symbols will be returned in an array.
- When all symbols are returned, the number of requests counted against the rate limiter is equal to the number of symbols currently trading on the exchange.
- If isIsolated ="TRUE", symbol must be sent.

## Response Example​


```
[   {       "clientOrderId": "qhcZw71gAkCCTv0t0k8LUK",       "cummulativeQuoteQty": "0.00000000",       "executedQty": "0.00000000",       "icebergQty": "0.00000000",       "isWorking": true,       "orderId": 211842552,       "origQty": "0.30000000",       "price": "0.00475010",       "side": "SELL",       "status": "NEW",       "stopPrice": "0.00000000",       "symbol": "BNBBTC",       "isIsolated": true,       "time": 1562040170089,       "timeInForce": "GTC",       "type": "LIMIT",       "selfTradePreventionMode": "NONE",       "updateTime": 1562040170089	}]
```
