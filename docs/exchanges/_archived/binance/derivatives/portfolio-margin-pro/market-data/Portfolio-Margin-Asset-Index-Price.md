On this page

# Query Portfolio Margin Asset Index Price (MARKET_DATA)

## API Description

Query Portfolio Margin Asset Index Price

## HTTP Request

GET `/sapi/v1/portfolio/asset-index-price`

## Request Weight(IP)")

- *1**if send asset or**50** if not send asset

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| NO|

## Response Example


    [
       {
           "asset": "BTC",
           "assetIndexPrice": "28251.9136906",  // in USD
           "time": 1683518338121
       }
    ]


  - [API Description](</docs/derivatives/portfolio-margin-pro/market-data#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/market-data#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/market-data#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/market-data#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/market-data#response-example>)
