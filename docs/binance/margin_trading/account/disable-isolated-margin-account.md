
# Disable Isolated Margin Account (TRADE)


## API Description​


Disable isolated margin account for a specific symbol. Each trading pair can only be deactivated once every 24
hours.


## HTTP Request​


DELETE `/sapi/v1/margin/isolated/account`


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
