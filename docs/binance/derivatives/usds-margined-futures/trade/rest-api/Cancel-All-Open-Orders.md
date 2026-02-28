On this page

# Cancel All Open Orders (TRADE)

## API Description

Cancel All Open Orders

## HTTP Request

DELETE `/fapi/v1/allOpenOrders`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "code": 200,
        "msg": "The operation of cancel all open order is done."
    }


  - [API Description](</docs/derivatives/usds-margined-futures/trade/rest-api/Cancel-All-Open-Orders#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/trade/rest-api/Cancel-All-Open-Orders#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/trade/rest-api/Cancel-All-Open-Orders#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/trade/rest-api/Cancel-All-Open-Orders#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/trade/rest-api/Cancel-All-Open-Orders#response-example>)
