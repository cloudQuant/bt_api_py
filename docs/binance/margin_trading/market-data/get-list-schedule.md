
# Get list Schedule (MARKET_DATA)


## API Description​


Get the upcoming tokens or symbols listing schedule for Cross Margin and Isolated Margin.


## HTTP Request​


GET `/sapi/v1/margin/list-schedule`


## Request Weight(IP)​


- *100**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| recvWindow | LONG | NO |  |

| timestamp | LONG | YES |  |


## Response Example​


```bash
[{    "listTime": 1686161202000,    "crossMarginAssets": ["BTC",      "USDT"  ],    "isolatedMarginSymbols": ["ADAUSDT",      "BNBUSDT"   ]  },  {    "listTime": 1686222232000,    "crossMarginAssets": ["ADA"   ],    "isolatedMarginSymbols": []  }]

```bash
