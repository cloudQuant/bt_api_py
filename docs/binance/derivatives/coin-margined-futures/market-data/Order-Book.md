On this page

# Order Book

## API Description

Query orderbook on specific symbol

## HTTP Request

GET `/dapi/v1/depth`

## Request Weight

Adjusted based on the limit:

Limit| Weight

- --|---

5, 10, 20, 50| 2

100| 5

500| 10

1000| 20

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

limit| INT| NO| Default 500; Valid limits:[5, 10, 20, 50, 100, 500, 1000]

## Response Example


    {
      "lastUpdateId": 16769853,
      "symbol": "BTCUSD_PERP", // Symbol
      "pair": "BTCUSD",         // Pair
      "E": 1591250106370,   // Message output time
      "T": 1591250106368,   // Transaction time
      "bids": [
        [
          "9638.0",         // PRICE
          "431"                // QTY
        ]
      ],
      "asks": [
        [
          "9638.2",
          "12"
        ]
      ]
    }


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Order-Book#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Order-Book#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Order-Book#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Order-Book#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Order-Book#response-example>)
