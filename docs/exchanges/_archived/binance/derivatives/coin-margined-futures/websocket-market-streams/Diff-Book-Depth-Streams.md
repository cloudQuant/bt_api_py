On this page

# Diff. Book Depth Streams

## Stream Description

Bids and asks, pushed every 250 milliseconds, 500 milliseconds, or 100 milliseconds

## Stream Name

`<symbol>@depth` OR `<symbol>@depth@500ms` OR `<symbol>@depth@100ms`

## Update Speed

- *250ms**or**500ms**or**100ms**

## Response Example


    {
      "e": "depthUpdate",            // Event type
      "E": 1591270260907,            // Event time
      "T": 1591270260891,            // Transction time
      "s": "BTCUSD_200626",            // Symbol
      "ps": "BTCUSD",                // Pair
      "U": 17285681,                // First update ID in event
      "u": 17285702,                // Final update ID in event
      "pu": 17285675,                // Final update Id in last stream(ie `u` in last stream)
      "b": [// Bids to be updated
        [
          "9517.6",                    // Price level to be updated
          "10"                        // Quantity
       ]
      ],
      "a": [// Asks to be updated
        [
          "9518.5",                    // Price level to be updated
          "45"                        // Quantity
       ]
      ]
    }


  - [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#update-speed>)
  - [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#response-example>)
