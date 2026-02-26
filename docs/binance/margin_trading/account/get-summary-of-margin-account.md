
# Get Summary of Margin account (USER_DATA)


## API Description​


Get personal margin level information


## HTTP Request​


GET `/sapi/v1/margin/tradeCoeff`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |


## Response Example​


```
{  "normalBar": "1.5",  "marginCallBar": "1.3",  "forceLiquidationBar": "1.1"}
```
