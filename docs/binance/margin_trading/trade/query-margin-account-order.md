
# Query Margin Account's Order (USER_DATA)


## API Description​


Query Margin Account's Order


## HTTP Request​


GET `/sapi/v1/margin/order`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| orderId | LONG | NO |  |
| origClientOrderId | STRING | NO |  |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |

- Either orderId or origClientOrderId must be sent.
- For some historical orders cummulativeQuoteQty will be < 0, meaning the data is not available at this time.

## Response Example​


```
{   "clientOrderId": "ZwfQzuDIGpceVhKW5DvCmO",   "cummulativeQuoteQty": "0.00000000",   "executedQty": "0.00000000",   "icebergQty": "0.00000000",   "isWorking": true,   "orderId": 213205622,   "origQty": "0.30000000",   "price": "0.00493630",   "side": "SELL",   "status": "NEW",   "stopPrice": "0.00000000",   "symbol": "BNBBTC",   "isIsolated": true,   "time": 1562133008725,   "timeInForce": "GTC",   "type": "LIMIT",   "selfTradePreventionMode": "NONE",   "updateTime": 1562133008725}
```
