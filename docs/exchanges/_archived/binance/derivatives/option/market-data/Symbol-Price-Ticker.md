On this page

# Symbol Price Ticker

## API Description

Get spot index price for option underlying.

## HTTP Request

GET `/eapi/v1/index`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

underlying| STRING| YES| Spot pair（Option contract underlying asset, e.g BTCUSDT)

## Response Example


    {
       "time": 1656647305000,
       "indexPrice": "9200" // Current spot index price
    }


  - [API Description](</docs/derivatives/option/market-data/Symbol-Price-Ticker#api-description>)
  - [HTTP Request](</docs/derivatives/option/market-data/Symbol-Price-Ticker#http-request>)
  - [Request Weight](</docs/derivatives/option/market-data/Symbol-Price-Ticker#request-weight>)
  - [Request Parameters](</docs/derivatives/option/market-data/Symbol-Price-Ticker#request-parameters>)
  - [Response Example](</docs/derivatives/option/market-data/Symbol-Price-Ticker#response-example>)
