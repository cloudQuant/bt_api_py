On this page

# 24-hour TICKER by underlying asset and expiration data

## Stream Description

24hr ticker info by underlying asset and expiration date. E.g.[ETH@ticker@220930](<wss://nbstream.binance.com/eoptions/stream?streams=ETH@ticker@220930>)

## Stream Name

`<underlyingAsset>@ticker@<expirationDate>`

## Update Speed

- *1000ms**

## Response Example


    [
    {
          "e":"24hrTicker",           // event type
          "E":1657706425200,          // event time
          "T":1657706425220,          // transaction time
          "s":"ETH-220930-1600-C",    // Option symbol
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
        },
        {
          "e":"24hrTicker",
          "E":1663685112123,
          "s":"ETH-220930-1500-C",
          "o":"34.9",
          "h":"44.6",
          "l":"26.8",
          "c":"26.8",
          "V":"11.84",
          "A":"444.37",
          "P":"-0.232",
          "p":"-8.1",
          "Q":"0",
          "F":"91",
          "L":"129",
          "n":39,
          "bo":"26.8",
          "ao":"33.9",
          "bq":"0.65",
          "aq":"0.01",
          "b":"0.88790536",
          "a":"0.98729014",
          "d":"0.2621153",
          "t":"-3.44806807",
          "g":"0.00158298",
          "v":"0.7148147",
          "vo":"0.93759775",
          "mp":"30.3",
          "hl":"228.7",
          "ll":"0.1",
          "eep":"0"
        }
    ]


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER-by-underlying-asset-and-expiration-data#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER-by-underlying-asset-and-expiration-data#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER-by-underlying-asset-and-expiration-data#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/24-hour-TICKER-by-underlying-asset-and-expiration-data#response-example>)
