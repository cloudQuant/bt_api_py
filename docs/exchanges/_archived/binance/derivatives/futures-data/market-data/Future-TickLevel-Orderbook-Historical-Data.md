On this page

# Get Future TickLevel Orderbook Historical Data Download Link(USER_DATA)

## API Description

Get Future TickLevel Orderbook Historical Data Download Link

## HTTP Request

GET `/sapi/v1/futures/histDataLink`

## Request Weight(IP)")

- *200**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES| symbol name, e.g. BTCUSDT or BTCUSD_PERP ｜

dataType| ENUM| YES| `T_DEPTH` for ticklevel orderbook data, `S_DEPTH` for orderbook snapshot data

startTime| LONG| YES|

endTime| LONG| YES|

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   *The span between `startTime` and `endTime` can't be more than 7 days
>  *The downloand link will be valid for 1 day
>  * Only VIP user can query this endpoint
>

## Response Example


    {
        "data": [
            {
                "day": "2023-06-30",
                "url": "<https://bin-prod-user-rebate-bucket.s3.ap-northeast-1.amazonaws.com/future-data-symbol-update/2023-06-30/BTCUSDT_T_DEPTH_2023-06-30.tar.gz?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20230925T025710Z&X-Amz-SignedHeaders=host&X-Amz-Expires=86399&X-Amz-Credential=AKIAVL364M5ZNFZ74IPP%2F20230925%2Fap-northeast-1%2Fs3%2Faws4_request&X-Amz-Signature=5fffcb390d10f34d71615726f81f99e42d80a11532edeac77b858c51a88cbf59">
            }
        ]
    }


  - [API Description](</docs/derivatives/futures-data/market-data#api-description>)
  - [HTTP Request](</docs/derivatives/futures-data/market-data#http-request>)
  - [Request Weight(IP)](</docs/derivatives/futures-data/market-data#request-weightip>)
  - [Request Parameters](</docs/derivatives/futures-data/market-data#request-parameters>)
  - [Response Example](</docs/derivatives/futures-data/market-data#response-example>)
