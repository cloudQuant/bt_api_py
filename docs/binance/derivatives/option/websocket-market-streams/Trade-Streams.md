On this page

# Trade Streams

## Stream Description

The Trade Streams push raw trade information for specific symbol or underlying asset. E.g.[ETH@trade](<wss://nbstream.binance.com/eoptions/stream?streams=ETH@trade>)

## Stream Name

`<symbol>@trade` or `<underlyingAsset>@trade`

## Update Speed

- *50ms**

## Response Example


    {
        "e":"trade",                        // event type
        "E":1591677941092,                  // event time
        "s":"BTC-200630-9000-P",            // Option trading symbol
        "t":1,                              // trade ID
        "p":"1000",                         // price
        "q":"-2",                           // quantity
        "b":4611781675939004417,            // buy order ID
        "a":4611781675939004418,            // sell order ID
        "T":1591677567872,                  // trade completed time
        "S":"-1"                            // direction
        "X": "MARKET"                       // trade type enum, "MARKET" for Orderbook trading, "BLOCK" for Block trade
    }


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/Trade-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/Trade-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/Trade-Streams#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/Trade-Streams#response-example>)
