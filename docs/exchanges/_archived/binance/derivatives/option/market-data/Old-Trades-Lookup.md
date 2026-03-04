On this page

# Old Trades Lookup (MARKET_DATA)

## API Description

Get older market historical trades.

## HTTP Request

GET `/eapi/v1/historicalTrades`

## Request Weight

20

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES| Option trading pair, e.g BTC-200730-9000-C

fromId| LONG| NO| The UniqueId ID from which to return. The latest deal record is returned by default

limit| INT| NO| Number of records Default:100 Max:500

## Response Example


    [
      {
        "id":"1",             // UniqueId
        "tradeId": "159244329455993",    // TradeId
        "price": "1000",      // Completed trade price
        "qty": "-0.1",        // Completed trade Quantity
        "quoteQty": "-100",   // Completed trade amount
        "side": -1            // Completed trade direction（-1 Sell，1 Buy）
        "time": 1592449455993,// Time
      }
    ]


  - [API Description](</docs/derivatives/option/market-data/Old-Trades-Lookup#api-description>)
  - [HTTP Request](</docs/derivatives/option/market-data/Old-Trades-Lookup#http-request>)
  - [Request Weight](</docs/derivatives/option/market-data/Old-Trades-Lookup#request-weight>)
  - [Request Parameters](</docs/derivatives/option/market-data/Old-Trades-Lookup#request-parameters>)
  - [Response Example](</docs/derivatives/option/market-data/Old-Trades-Lookup#response-example>)
