
# Query Margin Account's Trade List (USER_DATA)


## API Description​


Query Margin Account's Trade List


## HTTP Request​


GET `/sapi/v1/margin/myTrades`


## Request Weight​


- *10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| symbol | STRING | YES |  |

| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |

| orderId | LONG | NO |  |

| startTime | LONG | NO |  |

| endTime | LONG | NO |  |

| fromId | LONG | NO | TradeId to fetch from. Default gets most recent trades. |

| limit | INT | NO | Default 500; max 1000. |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |

- If fromId is set, it will get trades >= that fromId. Otherwise the trades within 24 hours are returned.
- Less than 24 hours between startTime and endTime.

## Response Example​


```bash
[    {        "commission": "0.00006000",        "commissionAsset": "BTC",        "id": 34,        "isBestMatch": true,        "isBuyer": false,        "isMaker": false,        "orderId": 39324,        "price": "0.02000000",        "qty": "3.00000000",        "symbol": "BNBBTC",        "isIsolated": false,        "time": 1561973357171    }]

```bash
