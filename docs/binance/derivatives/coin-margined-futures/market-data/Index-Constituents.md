On this page

# Query Index Price Constituents

## API Description

Query index price constituents

## HTTP Request

GET `/dapi/v1/constituents`

## Request Weight

- *2**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

## Response Example


    {
        "symbol": "BTCUSD",
        "time": 1697422647853,
        "constituents": [
            {
                "exchange": "bitstamp",
                "symbol": "btcusd"
            },
            {
                "exchange": "coinbase",
                "symbol": "BTC-USD"
            },
            {
                "exchange": "kraken",
                "symbol": "XBT/USD"
            },
            {
                "exchange": "binance_cross",
                "symbol": "BTCUSDC*index(USDCUSD)"
            }
        ]
    }


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Index-Constituents#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Index-Constituents#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Index-Constituents#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Index-Constituents#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Index-Constituents#response-example>)
