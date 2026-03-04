On this page

# Cancel All Option Orders By Underlying (TRADE)

## API Description

Cancel all active orders on specified underlying.

## HTTP Request

DELETE `/eapi/v1/allOpenOrdersByUnderlying`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

underlying| STRING| YES| Option underlying, e.g BTCUSDT

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "code": 0,
        "msg": "success",
        "data": 0
    }


  - [API Description](</docs/derivatives/option/trade/Cancel-All-Option-Orders-By-Underlying#api-description>)
  - [HTTP Request](</docs/derivatives/option/trade/Cancel-All-Option-Orders-By-Underlying#http-request>)
  - [Request Weight](</docs/derivatives/option/trade/Cancel-All-Option-Orders-By-Underlying#request-weight>)
  - [Request Parameters](</docs/derivatives/option/trade/Cancel-All-Option-Orders-By-Underlying#request-parameters>)
  - [Response Example](</docs/derivatives/option/trade/Cancel-All-Option-Orders-By-Underlying#response-example>)
