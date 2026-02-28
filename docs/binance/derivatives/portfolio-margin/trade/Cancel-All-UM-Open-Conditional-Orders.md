On this page

# Cancel All UM Open Conditional Orders (TRADE)

## API Description

Cancel All UM Open Conditional Orders

## HTTP Request

`DELETE /papi/v1/um/conditional/allOpenOrders`

## Request Weight(Order)")

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "code": "200",
        "msg": "The operation of cancel all conditional open order is done."
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Conditional-Orders#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Conditional-Orders#http-request>)
  - [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Conditional-Orders#request-weightorder>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Conditional-Orders#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Conditional-Orders#response-example>)
