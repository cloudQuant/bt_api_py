On this page

# Mark Price of All Symbols of a Pair

## Stream Description

Mark Price of All Symbols of a Pair

## Stream Name

`<pair>@markPrice` OR `<pair>@markPrice@1s`

## Update Speed

- *3000ms**OR**1000ms**

## Response Example


    [
      {
        "e":"markPriceUpdate",    // Event type
        "E":1596095725000,        // Event time
        "s":"BTCUSD_201225",    // Symbol
        "p":"10934.62615417",    // Mark Price
        "P":"10962.17178236",    // Estimated Settle Price, only useful in the last hour before the settlement starts.
        "i":"10933.62615417",   // Index Price
        "r":"",                    // funding rate for perpetual symbol, "" will be shown for delivery symbol
        "T":0                    // next funding time for perpetual symbol, 0 will be shown for delivery symbol
      },
      {
        "e":"markPriceUpdate",
        "E":1596095725000,
        "s":"BTCUSD_PERP",
        "p":"11012.31359011",
        "P":"10962.17178236",
        "i":"10933.62615417",   // Index Price
        "r":"0.00000000",
        "T":1596096000000
      }
    ]


  - [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-of-All-Symbols-of-a-Pair#stream-description>)
  - [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-of-All-Symbols-of-a-Pair#stream-name>)
  - [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-of-All-Symbols-of-a-Pair#update-speed>)
  - [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-of-All-Symbols-of-a-Pair#response-example>)
