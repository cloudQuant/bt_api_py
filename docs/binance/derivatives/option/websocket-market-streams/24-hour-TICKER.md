On this page

# 24-hour TICKER

## Stream Description

24hr ticker info for all symbols. Only symbols whose ticker info changed will be sent.

## Stream Name

`<symbol>@ticker`

## Update Speed

- *1000ms**

## Response Example


    {
        "e":"24hrTicker",           // event type
        "E":1657706425200,          // event time
        "T":1657706425220,          // transaction time
        "s":"BTC-220930-18000-C",   // Option symbol
        "o":"2000",                 // 24-hour opening price
        "h":"2020",                 // Highest price
        "l":"2000",                 // Lowest price
        "c":"2020",                 // latest price
        "V":"1.42",                 // Trading volume(in contracts)
        "A":"2841",                 // trade amount(in quote asset)
        "P":"0.01",                 // price change percent
        "p":"20",                   // price change
        "Q":"0.01",                 // volume of last completed trade(in contracts)
        "F":"27",                   // first trade ID
        "L":"48",                   // last trade ID
        "n":22,                     // number of trades
        "bo":"2012",                // The best buy price
        "ao":"2020",                // The best sell price
        "bq":"4.9",                 // The best buy quantity
        "aq":"0.03",                // The best sell quantity
        "b":"0.1202",               // BuyImplied volatility
        "a":"0.1318",               // SellImplied volatility
        "d":"0.98911",              // delta
        "t":"-0.16961",             // theta
        "g":"0.00004",              // gamma
        "v":"2.66584",              // vega
        "vo":"0.10001",             // Implied volatility
        "mp":"2003.5102",           // Mark price
        "hl":"2023.511",            // Buy Maximum price
        "ll":"1983.511",            // Sell Minimum price
        "eep":"0"                   // Estimated strike price (return estimated strike price half hour before exercise)
      }


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER#response-example>)
