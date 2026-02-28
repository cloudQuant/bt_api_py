On this page

# Mark Price Stream

## Stream Description

Mark price and funding rate for a single symbol pushed every 3 seconds or every second.

## Stream Name

`<symbol>@markPrice` or `<symbol>@markPrice@1s`

## Update Speed

- *3000ms**or**1000ms**

## Response Example


      {
        "e": "markPriceUpdate",      // Event type
        "E": 1562305380000,          // Event time
        "s": "BTCUSDT",              // Symbol
        "p": "11794.15000000",       // Mark price
        "i": "11784.62659091",        // Index price
        "P": "11784.25641265",        // Estimated Settle Price, only useful in the last hour before the settlement starts
        "r": "0.00038167",           // Funding rate
        "T": 1562306400000           // Next funding time
      }


  - [Stream Description](</docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream#stream-description>)
  - [Stream Name](</docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream#stream-name>)
  - [Update Speed](</docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream#update-speed>)
  - [Response Example](</docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream#response-example>)
