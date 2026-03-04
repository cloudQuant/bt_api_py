
# Query Liability Coin Leverage Bracket in Cross Margin Pro Mode(MARKET_DATA)


## API Description​


Liability Coin Leverage Bracket in Cross Margin Pro Mode


## HTTP Request​


GET `/sapi/v1/margin/leverageBracket`


## Request Weight(IP)​


- *1**


## Request Parameters​


None


## Response Example​


```bash
[{     "assetNames":["SHIB",        "FDUSD",        "BTC",        "ETH",        "USDC"   ],               "rank":1,     "brackets":[{           "leverage":10,           "maxDebt":1000000.00000000,           "maintenanceMarginRate":0.02000000,           "initialMarginRate":0.1112,           "fastNum":0        },        {           "leverage":3,           "maxDebt":4000000.00000000,           "maintenanceMarginRate":0.07000000,           "initialMarginRate":0.5000,           "fastNum":60000.0000000000000000        }    ]  }]

```bash
