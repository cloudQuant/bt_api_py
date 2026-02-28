On this page

# Kline/Candlestick Streams

## Stream Description

The Kline/Candlestick Stream push updates to the current klines/candlestick every 1000 milliseconds (if existing).

- *Kline/Candlestick chart intervals:**

m -> minutes; h -> hours; d -> days; w -> weeks; M -> months

"1m", "3m", "5m", "15m" "30m" "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w",

## Stream Name

`<symbol>@kline_<interval>`

## Update Speed

- *1000ms**

## Response Example


    {
        "e":"kline",                        // event type
        "E":1638747660000,                  // event time
        "s":"BTC-200630-9000-P",            // Option trading symbol
        "k":{
            "t":1638747660000,              // kline start time
            "T":1638747719999,              // kline end time
            "s":"BTC-200630-9000-P",        // Option trading symbol
            "i":"1m",                       // candle period
            "F":0,                          // first trade ID
            "L":0,                          // last trade ID
            "o":"1000",                     // open
            "c":"1000",                     // close
            "h":"1000",                     // high
            "l":"1000",                     // low
            "v":"0",                        // volume(in contracts)
            "n":0,                          // number of trades
            "x":false,                      // current candle has been completed Y/N
            "q":"0",                        // completed trade amount   (in quote asset)           
            "V":"0",                        // taker completed trade volume (in contracts)           
            "Q":"0"                         // taker trade amount(in quote asset)
        }
    }


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/Kline-Candlestick-Streams#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/Kline-Candlestick-Streams#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/Kline-Candlestick-Streams#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/Kline-Candlestick-Streams#response-example>)
