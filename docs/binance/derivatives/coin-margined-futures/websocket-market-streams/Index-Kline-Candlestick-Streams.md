On this page

# Index Kline/Candlestick Streams

## Stream Description

Index Kline/Candlestick Streams

- *Kline/Candlestick chart intervals:**

m -> minutes; h -> hours; d -> days; w -> weeks; M -> months

  - 1m
  - 3m
  - 5m
  - 15m
  - 30m
  - 1h
  - 2h
  - 4h
  - 6h
  - 8h
  - 12h
  - 1d
  - 3d
  - 1w
  - 1M

## Stream Name

`<pair>@indexPriceKline_<interval>`

e.g. "btcusd@indexPriceKline_1m"

## Update Speed

- *250ms**

## Response Example


    {
      "e":"indexPrice_kline",        // Event Name
      "E":1591267070033,            // Event Time
      "ps":"BTCUSD",                // Pair
      "k":{
        "t":1591267020000,            // Kline start time
        "T":1591267079999,            // Kline close time
        "s":"0",                    // ignore
        "i":"1m",                    // Interval
        "f":1591267020000,            // ignore
        "L":1591267070000,            // ignore
        "o":"9542.21900000",        // Open price
        "c":"9542.50440000",        // Close price
        "h":"9542.71640000",        // High price
        "l":"9542.21040000",        // Low price
        "v":"0",                    // ignore
        "n":51,                        // Number of basic data
        "x":false,                    // Is this kline closed?
        "q":"0",                    // ignore
        "V":"0",                    // ignore
        "Q":"0",                    // ignore
        "B":"0"                        // ignore
      }
    }


  - [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Index-Kline-Candlestick-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Index-Kline-Candlestick-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Index-Kline-Candlestick-Streams#update-speed>)
  - [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Index-Kline-Candlestick-Streams#response-example>)
