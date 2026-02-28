On this page

# Get Download Id For Futures Transaction History(USER_DATA)

## API Description

Get download id for futures transaction history

## HTTP Request

GET `/dapi/v1/income/asyn`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

startTime| LONG| YES| Timestamp in ms

endTime| LONG| YES| Timestamp in ms

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   *Request Limitation is 5 times per month, shared by front end download page and rest api
>  * The time between `startTime` and `endTime` can not be longer than 1 year
>

## Response Example


    {
        "avgCostTimestampOfLast30d":7241837, // Average time taken for data download in the past 30 days
          "downloadId":"546975389218332672",
    }


  - [API Description](</docs/derivatives/coin-margined-futures/account/Get-Download-Id-For-Futures-Transaction-History#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/account/Get-Download-Id-For-Futures-Transaction-History#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/account/Get-Download-Id-For-Futures-Transaction-History#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/account/Get-Download-Id-For-Futures-Transaction-History#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/account/Get-Download-Id-For-Futures-Transaction-History#response-example>)
