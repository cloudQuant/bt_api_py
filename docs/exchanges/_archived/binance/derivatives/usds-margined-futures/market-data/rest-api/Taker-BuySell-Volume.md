On this page

# Taker Buy/Sell Volume

## API Description

Taker Buy/Sell Volume

## HTTP Request

GET `/futures/data/takerlongshortRatio`

## Request Weight

- *0**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

period| ENUM| YES| "5m","15m","30m","1h","2h","4h","6h","12h","1d"

limit| LONG| NO| default 30, max 500

startTime| LONG| NO|

endTime| LONG| NO|

>   *If startTime and endTime are not sent, the most recent data is returned.
>  *Only the data of the latest 30 days is available.
>  * IP rate limit 1000 requests/5min
>

## Response Example


    [
        {
            "buySellRatio":"1.5586",
            "buyVol": "387.3300",
            "sellVol":"248.5030",
            "timestamp":"1585614900000"
        },
        {
            "buySellRatio":"1.3104",
            "buyVol": "343.9290",
            "sellVol":"248.5030",
            "timestamp":"1583139900000"
        },
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Taker-BuySell-Volume#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Taker-BuySell-Volume#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Taker-BuySell-Volume#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Taker-BuySell-Volume#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Taker-BuySell-Volume#response-example>)
