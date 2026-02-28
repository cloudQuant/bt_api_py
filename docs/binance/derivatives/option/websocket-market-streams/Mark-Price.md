On this page

# Mark Price

## Stream Description

The mark price for all option symbols on specific underlying asset. E.g.[ETH@markPrice](<wss://nbstream.binance.com/eoptions/stream?streams=ETH@markPrice>)

## Stream Name

`<underlyingAsset>@markPrice`

## Update Speed

- *1000ms**

# Response Example


    [
        {
          "e":"markPrice",         // Event Type
          "E":1663684594227,       // Event time
          "s":"ETH-220930-1500-C", // Symbol
          "mp":"30.3"              // Option mark price
        },
        {
          "e":"markPrice",
          "E":1663684594228,
          "s":"ETH-220923-1000-C",
          "mp":"341.5"
        }
    ]


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/Mark-Price#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/Mark-Price#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/Mark-Price#update-speed>)
