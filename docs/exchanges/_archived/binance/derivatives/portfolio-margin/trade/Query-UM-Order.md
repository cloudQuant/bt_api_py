On this page

# Query UM Order (USER_DATA)

## API Description

Check an UM order's status.

## HTTP Request

GET `/papi/v1/um/order`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

orderId| LONG| NO|

origClientOrderId| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

Notes:

>   *These orders will not be found:
>  *Either `orderId` or `origClientOrderId` must be sent.
>    * order status is `CANCELED` or `EXPIRED`, **AND**
>     * order has NO filled trade, **AND**
>     * created time + 3 days < current time
>

## Response Example


    {
        "avgPrice": "0.00000",
        "clientOrderId": "abc",
        "cumQuote": "0",
        "executedQty": "0",
        "orderId": 1917641,
        "origQty": "0.40",
        "origType": "LIMIT",
        "price": "0",
        "reduceOnly": false,
        "side": "BUY",
        "positionSide": "SHORT",
        "status": "NEW",
        "symbol": "BTCUSDT",
        "time": 1579276756075,              // order time
        "timeInForce": "GTC",
        "type": "LIMIT",
        "updateTime": 1579276756075,        // update time
        "selfTradePreventionMode": "NONE",
        "goodTillDate": 0,
        "priceMatch": "NONE"
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Query-UM-Order#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-UM-Order#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-UM-Order#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-UM-Order#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Query-UM-Order#response-example>)
