
# Cross margin collateral ratio (MARKET_DATA)


## API Description​


Cross margin collateral ratio


## HTTP Request​


GET `/sapi/v1/margin/crossMarginCollateralRatio`


## Request Weight​


- *100(IP)**


## Request Parameters​


None


## Response Example​


```bash
[{    "collaterals": [{        "minUsdValue": "0",        "maxUsdValue": "13000000",        "discountRate": "1"      },      {        "minUsdValue": "13000000",        "maxUsdValue": "20000000",        "discountRate": "0.975"      },      {        "minUsdValue": "20000000",        "discountRate": "0"      }  ],    "assetNames": ["BNX"   ]  },  {    "collaterals": [{        "minUsdValue": "0",        "discountRate": "1"      }   ],    "assetNames": ["BTC",      "BUSD",      "ETH",      "USDT"   ]  }]

```bash
