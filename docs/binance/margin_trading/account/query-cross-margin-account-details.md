
# Query Cross Margin Account Details (USER_DATA)


## API Description​


Query Cross Margin Account Details


## HTTP Request​


GET `/sapi/v1/margin/account`


## Request Weight​


**10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{      "created" : true, // True means margin account created , false means margin account not created.      "borrowEnabled": true,      "marginLevel": "11.64405625",      "collateralMarginLevel" : "3.2",      "totalAssetOfBtc": "6.82728457",      "totalLiabilityOfBtc": "0.58633215",      "totalNetAssetOfBtc": "6.24095242",      "TotalCollateralValueInUSDT": "5.82728457",      "totalOpenOrderLossInUSDT": "582.728457",      "tradeEnabled": true,      "transferInEnabled": true,      "transferOutEnabled": true,      "accountType": "MARGIN_1",  // // MARGIN_1 for Cross Margin Classic, MARGIN_2 for Cross Margin Pro      "userAssets": [          {              "asset": "BTC",              "borrowed": "0.00000000",              "free": "0.00499500",              "interest": "0.00000000",              "locked": "0.00000000",              "netAsset": "0.00499500"          },          {              "asset": "BNB",              "borrowed": "201.66666672",              "free": "2346.50000000",              "interest": "0.00000000",              "locked": "0.00000000",              "netAsset": "2144.83333328"          },          {              "asset": "ETH",              "borrowed": "0.00000000",              "free": "0.00000000",              "interest": "0.00000000",              "locked": "0.00000000",              "netAsset": "0.00000000"          },          {              "asset": "USDT",              "borrowed": "0.00000000",              "free": "0.00000000",              "interest": "0.00000000",              "locked": "0.00000000",              "netAsset": "0.00000000"          }      ]}
```
