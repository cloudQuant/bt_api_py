On this page

# Long/Short Ratio

## API Description

Query symbol Long/Short Ratio

## HTTP Request

GET `/futures/data/globalLongShortAccountRatio`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

pair| STRING| YES| BTCUSD

period| ENUM| YES| "5m","15m","30m","1h","2h","4h","6h","12h","1d"

limit| LONG| NO| Default 30,Max 500

startTime| LONG| NO|

endTime| LONG| NO|

>   *If startTime and endTime are not sent, the most recent data is returned.
>  * Only the data of the latest 30 days is available.
>

## Response Example


    [
       {
          "pair": "BTCUSD",
          "longShortRatio": "0.1960",
          "longAccount": "0.6622",  //66.22%
          "shortAccount": "0.3378",  //33.78%
          "timestamp": 1583139600000
       },
       {
         "pair": "BTCUSD",
          "longShortRatio": "1.9559",
          "longAccount": "0.6617",
          "shortAccount": "0.3382",
          "timestamp": 1583139900000
        }
    ]


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Long-Short-Ratio#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Long-Short-Ratio#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Long-Short-Ratio#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Long-Short-Ratio#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Long-Short-Ratio#response-example>)
