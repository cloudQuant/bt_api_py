On this page

# Open Interest

## API Description

Get open interest for specific underlying asset on specific expiration date.

## HTTP Request

GET `/eapi/v1/openInterest`

## Request Weight

- *0**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

underlyingAsset| STRING| YES| underlying asset, e.g ETH/BTC

expiration| STRING| YES| expiration date, e.g 221225

## Response Example


    [
        {
            "symbol": "ETH-221119-1175-P",
            "sumOpenInterest": "4.01",
            "sumOpenInterestUsd": "4880.2985615624",
            "timestamp": "1668754020000"
        }
    ]


  - [API Description](</docs/derivatives/option/market-data/Open-Interest#api-description>)
  - [HTTP Request](</docs/derivatives/option/market-data/Open-Interest#http-request>)
  - [Request Weight](</docs/derivatives/option/market-data/Open-Interest#request-weight>)
  - [Request Parameters](</docs/derivatives/option/market-data/Open-Interest#request-parameters>)
  - [Response Example](</docs/derivatives/option/market-data/Open-Interest#response-example>)
