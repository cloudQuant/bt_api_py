On this page

# Portfolio Margin Pro Tiered Collateral Rate(USER_DATA)

## API Description

Portfolio Margin PRO Tiered Collateral Rate

## HTTP Request

GET `/sapi/v2/portfolio/collateralRate`

## Request Weight(IP)")

- *50**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    [
        {
            "asset": "BNB",
            "collateralInfo": [
                {
                "tierFloor": "0.0000",
                "tierCap": "1000.0000",
                "collateralRate": "1.0000",
                "cum":"0.0000"    //account equity quick addition number
                },
                {
                "tierFloor": "1000.0000",
                "tierCap": "2000.0000",
                "collateralRate": "0.9000",
                "cum":"0.0000"
                }
            ]
        },
        {
            "asset": "USDT",
            "collateralInfo": [
                {
                    "tierFloor": "0.0000",
                    "tierCap": "1000.0000",
                    "collateralRate": "1.0000",
                    "cum":"0.0000"
                },
                {
                    "tierFloor": "1000.0000",
                    "tierCap": "2000.0000",
                    "collateralRate": "0.9999",
                    "cum":"0.0000"
                }
            ]
        }
    ]


  - [API Description](</docs/derivatives/portfolio-margin-pro/market-data/Portfolio-Margin-Pro-Tiered-Collateral-Rate#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/market-data/Portfolio-Margin-Pro-Tiered-Collateral-Rate#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/market-data/Portfolio-Margin-Pro-Tiered-Collateral-Rate#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/market-data/Portfolio-Margin-Pro-Tiered-Collateral-Rate#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/market-data/Portfolio-Margin-Pro-Tiered-Collateral-Rate#response-example>)
