
# Enable Isolated Margin Account (TRADE)


## API Description​


Enable isolated margin account for a specific symbol(Only supports activation of previously disabled accounts).


## HTTP Request​


POST `/sapi/v1/margin/isolated/account`


## Request Weight​


**300(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| recvWindow | LONG | NO | No more than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{  "success": true,  "symbol": "BTCUSDT"}
```
