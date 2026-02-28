On this page

# Query All UM Orders(USER_DATA)

## API Description

Get all account UM orders; active, canceled, or filled.

  - These orders will not be found:
    - order status is `CANCELED` or `EXPIRED`, **AND**
    - order has NO filled trade, **AND**
    - created time + 3 days < current time

## HTTP Request

GET `/papi/v1/um/allOrders`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

orderId| LONG| NO|

startTime| LONG| NO|

endTime| LONG| NO|

limit| INT| NO| Default 500; max 1000.

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   *If `orderId` is set, it will get orders >= that orderId. Otherwise most recent orders are returned.
>  * The query time period must be less then 7 days( default as the recent 7 days).
>

## Response Example


    [
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
        "selfTradePreventionMode": "NONE", //self trading preventation mode
        "goodTillDate": 0,
        "priceMatch": "NONE"
      }
    ]


  - [API Description](</docs/derivatives/portfolio-margin/trade/Query-All-UM-Orders#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-All-UM-Orders#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-All-UM-Orders#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-All-UM-Orders#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Query-All-UM-Orders#response-example>)
