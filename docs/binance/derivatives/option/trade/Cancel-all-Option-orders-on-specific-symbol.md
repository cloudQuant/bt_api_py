On this page

# Cancel all Option orders on specific symbol (TRADE)

## API Description

Cancel all active order on a symbol.

## HTTP Request

DELETE `/eapi/v1/allOpenOrders`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES| Option trading pair, e.g BTC-200730-9000-C

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
      "code": 0,
      "msg": "success"
    }


  - [API Description](</docs/derivatives/option/trade/Cancel-all-Option-orders-on-specific-symbol#api-description>)
  - [HTTP Request](</docs/derivatives/option/trade/Cancel-all-Option-orders-on-specific-symbol#http-request>)
  - [Request Weight](</docs/derivatives/option/trade/Cancel-all-Option-orders-on-specific-symbol#request-weight>)
  - [Request Parameters](</docs/derivatives/option/trade/Cancel-all-Option-orders-on-specific-symbol#request-parameters>)
  - [Response Example](</docs/derivatives/option/trade/Cancel-all-Option-orders-on-specific-symbol#response-example>)
