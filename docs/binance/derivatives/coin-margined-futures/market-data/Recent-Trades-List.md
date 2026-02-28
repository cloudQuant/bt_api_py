On this page

# Recent Trades List

## API Description

Get recent market trades

## HTTP Request

GET `/dapi/v1/trades`

## Request Weight

5

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

limit| INT| NO| Default 500; max 1000.

  - Market trades means trades filled in the order book. Only market trades will be returned, which means the insurance fund trades and ADL trades won't be returned.

## Response Example


    [
      {
        "id": 28457,
        "price": "9635.0",
        "qty": "1",
        "baseQty": "0.01037883",
        "time": 1591250192508,
        "isBuyerMaker": true,
      }
    ]


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Recent-Trades-List#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Recent-Trades-List#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Recent-Trades-List#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Recent-Trades-List#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Recent-Trades-List#response-example>)
