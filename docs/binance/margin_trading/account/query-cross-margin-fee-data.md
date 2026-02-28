
# Query Cross Margin Fee Data (USER_DATA)


## API Description​


Get cross margin fee data collection with any vip level or user's current specific data as [<https://www.binance.com/en/margin-fee](<https://www.binance.com/en/margin-fee>)>


## HTTP Request​


GET `/sapi/v1/margin/crossMarginData`


## Request Weight​


- *1 when coin is specified;(IP)**
- *5 when the coin parameter is omitted(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| vipLevel | INT | NO | User's current specific margin data will be returned if vipLevel is omitted |

| coin | STRING | NO |  |

| recvWindow | LONG | NO | No more than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
[{        "vipLevel": 0,        "coin": "BTC",        "transferIn": true,        "borrowable": true,        "dailyInterest": "0.00026125",        "yearlyInterest": "0.0953",        "borrowLimit": "180",        "marginablePairs": ["BNBBTC",            "TRXBTC",            "ETHBTC",            "BTCUSDT"      ]    }]

```bash
