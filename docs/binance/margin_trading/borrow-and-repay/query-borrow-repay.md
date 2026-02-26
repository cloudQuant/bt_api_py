
# Query borrow/repay records in Margin account(USER_DATA)


## API Description​


Query borrow/repay records in Margin account


## HTTP Request​


GET `/sapi/v1/margin/borrow-repay`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | NO |  |
| isolatedSymbol | STRING | NO | Symbol in Isolated Margin |
| txId | LONG | NO | tranId in POST /sapi/v1/margin/loan |
| startTime | LONG | NO |  |
| endTime | LONG | NO |  |
| current | LONG | NO | Current querying page. Start from 1. Default:1 |
| size | LONG | NO | Default:10 Max:100 |
| type | STRING | YES | BORROW or REPAY |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


> txId or startTime must be sent. txId takes precedence.
Response in descending order
If an asset is sent, data within 30 days before endTime; If an asset is not sent, data within 7 days before endTime
If neither startTime nor endTime is sent, the recent 7-day data will be returned.
startTime set as endTime - 7days by default, endTime set as current time by default


## Response Example​


```
{  "rows": [      {        "type": "AUTO", // AUTO,MANUAL for Cross Margin Borrow; MANUAL，AUTO，BNB_AUTO_REPAY，POINT_AUTO_REPAY for Cross Margin Repay; AUTO，MANUAL for Isolated Margin Borrow/Repay;        "isolatedSymbol": "BNBUSDT",     // isolated symbol, will not be returned for crossed margin        "amount": "14.00000000",   // Total amount borrowed/repaid        "asset": "BNB",           "interest": "0.01866667",    // Interest repaid        "principal": "13.98133333",   // Principal repaid        "status": "CONFIRMED",   //one of PENDING (pending execution), CONFIRMED (successfully execution), FAILED (execution failed, nothing happened to your account);        "timestamp": 1563438204000,        "txId": 2970933056      }  ],  "total": 1}
```
