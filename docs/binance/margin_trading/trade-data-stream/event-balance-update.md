
# Payload: Balance update


## Event Description​


Balance Update occurs during the following:

- Deposits or withdrawals from the account
- Transfer of funds between accounts (e.g. Spot to Margin)

## Event Name​


`balanceUpdate`


## Response Example​


> Payload:


```
{  "e": "balanceUpdate",         //Event Type  "E": 1573200697110,           //Event Time  "a": "BTC",                   //Asset  "d": "100.00000000",          //Balance Delta  "T": 1573200697068            //Clear Time}
```
