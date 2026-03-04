On this page

# Partial Book Depth Streams

## Stream Description

Top **< levels>**bids and asks, Valid levels are**< levels>** are 10, 20, 50, 100.

## Stream Name

`<symbol>@depth<levels>` OR `<symbol>@depth<levels>@100ms` OR `<symbol>@depth<levels>@1000ms`.

## Update Speed

- *100ms**or**1000ms**,**500ms**(default when update speed isn't used)

## Response Example


    {
        "e":"depth",                    // event type
        "E":1591695934010,              // event time
        "T":1591695934000,              // transaction time
        "s":"BTC-200630-9000-P",        // Option symbol
        "u":162,                                     // update id in event
        "pu":162,                                     // same as update id in event
        "b":[// Buy order
          [
              "200",                    // Price
              "3",                      // quantity
         ],
          [
              "101",
              "1"
          ],
          [
              "100",
              "2"
          ]
        ],
        "a":[// Sell order
            [
                "1000",
                "89"
           ]
        ]
    }


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/Partial-Book-Depth-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/Partial-Book-Depth-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/Partial-Book-Depth-Streams#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/Partial-Book-Depth-Streams#response-example>)
