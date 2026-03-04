On this page

# Symbol Price Ticker

## API Description

Latest price for a symbol or symbols.

## HTTP Request

GET `/fapi/v1/ticker/price`

- *Weight:**

- *1** for a single symbol;
- *2** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

>   * If the symbol is not sent, prices for all symbols will be returned in an array.
>

## Response Example


    {
      "symbol": "BTCUSDT",
      "price": "6000.01",
      "time": 1589437530011   // Transaction time
    }


> OR


    [
        {
              "symbol": "BTCUSDT",
              "price": "6000.01",
              "time": 1589437530011
        }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker#http-request>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker#response-example>)
