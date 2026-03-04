On this page

# Get CM Income History(USER_DATA)

## API Description

Get CM Income History

## HTTP Request

GET `/papi/v1/cm/income`

## Request Weight

- *30**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

incomeType| STRING| NO| "TRANSFER","WELCOME_BONUS", "FUNDING_FEE", "REALIZED_PNL", "COMMISSION", "INSURANCE_CLEAR", and "DELIVERED_SETTELMENT"

startTime| LONG| NO| Timestamp in ms to get funding from INCLUSIVE.

endTime| LONG| NO| Timestamp in ms to get funding until INCLUSIVE.

page| INT| NO|

limit| INT| NO| Default 100; max 1000

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   *If `incomeType` is not sent, all kinds of flow will be returned
>  *"trandId" is unique in the same "incomeType" for a user
>  *The interval between `startTime` and `endTime` can not exceed 200 days:
>    * If `startTime` and `endTime` are not sent, the last 200 days will be returned
>

## Response Example


    [
        {
            "symbol": "",               // trade symbol, if existing
            "incomeType": "TRANSFER",   // income type
            "income": "-0.37500000",    // income amount
            "asset": "BTC",             // income asset
            "info":"WITHDRAW",          // extra information
            "time": 1570608000000,
            "tranId":"9689322392",      // transaction id
            "tradeId":""                // trade id, if existing
        },
        {
            "symbol": "BTCUSD_200925",
            "incomeType": "COMMISSION",
            "income": "-0.01000000",
            "asset": "BTC",
            "info":"",
            "time": 1570636800000,
            "tranId":"9689322392",
            "tradeId":"2059192"
        }
    ]


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-CM-Income-History#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-CM-Income-History#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-CM-Income-History#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-CM-Income-History#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-CM-Income-History#response-example>)
