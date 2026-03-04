On this page

# Cancel Block Trade Order (TRADE)

## API Description

Cancel a block trade order.

## HTTP Request

DELETE `eapi/v1/block/order/create`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory|  Description

- --|---|---|---

blockOrderMatchingKey| STRING| YES|

recvWindow| INT| NO| The value cannot be greater than 60000

timestamp| INT| YES|

## Response Example


    {}


  - [API Description](</docs/derivatives/option/market-maker-block-trade/Cancel-Block-Trade-Order#api-description>)
  - [HTTP Request](</docs/derivatives/option/market-maker-block-trade/Cancel-Block-Trade-Order#http-request>)
  - [Request Weight](</docs/derivatives/option/market-maker-block-trade/Cancel-Block-Trade-Order#request-weight>)
  - [Request Parameters](</docs/derivatives/option/market-maker-block-trade/Cancel-Block-Trade-Order#request-parameters>)
  - [Response Example](</docs/derivatives/option/market-maker-block-trade/Cancel-Block-Trade-Order#response-example>)
