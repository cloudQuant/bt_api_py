On this page

# Multi-Assets Mode Asset Index

## Stream Description

Asset index for multi-assets mode user

## Stream Name

`!assetIndex@arr`OR `<assetSymbol>@assetIndex`

## Update Speed

- *1s**

## Response Example


    [
        {
          "e":"assetIndexUpdate",
          "E":1686749230000,
          "s":"ADAUSD",           // asset index symbol
          "i":"0.27462452",       // index price
          "b":"0.10000000",       // bid buffer
          "a":"0.10000000",       // ask buffer
          "B":"0.24716207",       // bid rate
          "A":"0.30208698",       // ask rate
          "q":"0.05000000",       // auto exchange bid buffer
          "g":"0.05000000",       // auto exchange ask buffer
          "Q":"0.26089330",       // auto exchange bid rate
          "G":"0.28835575"        // auto exchange ask rate
        },
        {
          "e":"assetIndexUpdate",
          "E":1686749230000,
          "s":"USDTUSD",
          "i":"0.99987691",
          "b":"0.00010000",
          "a":"0.00010000",
          "B":"0.99977692",
          "A":"0.99997689",
          "q":"0.00010000",
          "g":"0.00010000",
          "Q":"0.99977692",
          "G":"0.99997689"
        }
    ]


  - [Stream Description](</docs/derivatives/usds-margined-futures/websocket-market-streams/Multi-Assets-Mode-Asset-Index#stream-description>)
  - [Stream Name](</docs/derivatives/usds-margined-futures/websocket-market-streams/Multi-Assets-Mode-Asset-Index#stream-name>)
  - [Update Speed](</docs/derivatives/usds-margined-futures/websocket-market-streams/Multi-Assets-Mode-Asset-Index#update-speed>)
  - [Response Example](</docs/derivatives/usds-margined-futures/websocket-market-streams/Multi-Assets-Mode-Asset-Index#response-example>)
