
# Get Cross Margin Transfer History (USER_DATA)


## API Description​


Get Cross Margin Transfer History


## HTTP Request​


GET `/sapi/v1/margin/transfer`


## Request Weight​


**1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | NO |  |
| type | STRING | NO | Transfer Type: ROLL_IN, ROLL_OUT |
| startTime | LONG | NO |  |
| endTime | LONG | NO |  |
| current | LONG | NO | Currently querying page. Start from 1. Default:1 |
| size | LONG | NO | Default:10 Max:100 |
| isolatedSymbol | STRING | NO | Symbol in Isolated Margin |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |

- Response in descending order
- The max interval between `startTime` and `endTime` is 30 days.
- Returns data for last 7 days by default

## Response Example​


```
{  "rows": [    {      "amount": "0.10000000",      "asset": "BNB",      "status": "CONFIRMED",      "timestamp": 1566898617,      "txId": 5240372201,      "type": "ROLL_IN",      "transFrom": "SPOT", //SPOT,FUTURES,FIAT,DELIVERY,MINING,ISOLATED_MARGIN,FUNDING,MOTHER_SPOT,OPTION,SUB_SPOT,SUB_MARGIN,CROSS_MARGIN      "transTo": "ISOLATED_MARGIN",//SPOT,FUTURES,FIAT,DELIVERY,MINING,ISOLATED_MARGIN,FUNDING,MOTHER_SPOT,OPTION,SUB_SPOT,SUB_MARGIN,CROSS_MARGIN    },    {      "amount": "5.00000000",      "asset": "USDT",      "status": "CONFIRMED",      "timestamp": 1566888436,      "txId": 5239810406,      "type": "ROLL_OUT",      "transFrom": "ISOLATED_MARGIN",//SPOT,FUTURES,FIAT,DELIVERY,MINING,ISOLATED_MARGIN,FUNDING,MOTHER_SPOT,OPTION,SUB_SPOT,SUB_MARGIN,CROSS_MARGIN      "transTo": "ISOLATED_MARGIN", //SPOT,FUTURES,FIAT,DELIVERY,MINING,ISOLATED_MARGIN,FUNDING,MOTHER_SPOT,OPTION,SUB_SPOT,SUB_MARGIN,CROSS_MARGIN      "fromSymbol": "BNBUSDT",      "toSymbol": "BTCUSDT"    },    {      "amount": "1.00000000",      "asset": "EOS",      "status": "CONFIRMED",      "timestamp": 1566888403,      "txId": 5239808703,      "type": "ROLL_IN"    }  ],  "total": 3}
```


**Error Code Description:**

- **EXCEED_MAX_ROLLOUT** Sometimes, your collateral margin level may be too low to allow a transfer out of your account. You will get an error response {"code":-3020,"msg":"Transfer out amount exceeds max amount."}. To resolve it, you can reduce your outstanding debt or add more assets to meet the required margin level for the transfer.
- **PREPAREDELIST_CANT_TRANSFER_IN** This error {“code”: -3065, “msg”: “%s has been scheduled for delisting. You may only transfer up to %s %s, which is the amount of liabilities less any collateral already available.”} indicates that a specific asset is planned to be delisted. As a result, there are restrictions on how much of this asset you can transfer out of your account. When transferring the asset out of Binance, you will not be able to exceed the allowed amount
- **NET_ASSET_MUST_LTE_RATIO** This error {“code”:-21003, “msg”: ”Fail to retrieve margin assets.”} typically occurs when users send requests at a very high frequency. Because asset information updates need processing time, sending requests too frequently can cause failures or delayed responses. We recommend that users maintain at least a 500 milliseconds (0.5 seconds) interval between each request. This interval allows the system enough time to process and update asset information, reducing errors or delays caused by high-frequency requests.