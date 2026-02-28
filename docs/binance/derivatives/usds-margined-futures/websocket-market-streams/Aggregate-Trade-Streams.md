On this page

# Aggregate Trade Streams

## Stream Description

The Aggregate Trade Streams push market trade information that is aggregated for fills with same price and taking side every 100 milliseconds. Only market trades will be aggregated, which means the insurance fund trades and ADL trades won't be aggregated.

## Stream Name

`<symbol>@aggTrade`

## Update Speed

- *100ms**

## Response Example


    {
      "e": "aggTrade",  // Event type
      "E": 123456789,   // Event time
      "s": "BTCUSDT",    // Symbol
      "a": 5933014,        // Aggregate trade ID
      "p": "0.001",     // Price
      "q": "100",       // Quantity
      "f": 100,         // First trade ID
      "l": 105,         // Last trade ID
      "T": 123456785,   // Trade time
      "m": true,        // Is the buyer the market maker?
    }


  - [Stream Description](</docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams#update-speed>)
  - [Response Example](</docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams#response-example>)
