On this page

# Query UM Conditional Order History(USER_DATA)

## API Description

Query UM Conditional Order History

## HTTP Request

GET `/papi/v1/um/conditional/orderHistory`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

strategyId| LONG| NO|

newClientStrategyId| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

- *Notes:**

>   *Either `strategyId` or `newClientStrategyId` must be sent.
>  *`NEW` orders will not be found.
>  *These orders will not be found:
>    * order status is `CANCELED` or `EXPIRED`, **AND**
>     * order has NO filled trade, **AND**
>     * created time + 7 days < current time
>

## Response Example


    {
        "newClientStrategyId": "abc",
        "strategyId":123445,
        "strategyStatus":"TRIGGERED",
        "strategyType": "TRAILING_STOP_MARKET",
        "origQty": "0.40",
        "price": "0",
        "reduceOnly": false,
        "side": "BUY",
        "positionSide": "SHORT",
        "stopPrice": "9300",                // please ignore when order type is TRAILING_STOP_MARKET
        "symbol": "BTCUSDT",
        "orderId":12132343435,     //Normal orderID after trigger if appliable，only have when the strategy is triggered
        "status": "NEW",          //Normal order status after trigger if appliable, only have when the strategy is triggered
        "bookTime": 1566818724710,              // order time
        "updateTime": 1566818724722,
        "triggerTime": 1566818724750,
        "timeInForce": "GTC",
        "type": "MARKET",   //Normal order type after trigger if appliable
        "activatePrice": "9020",            // activation price, only return with TRAILING_STOP_MARKET order
        "priceRate": "0.3",               // callback rate, only return with TRAILING_STOP_MARKET order
        "workingType":"CONTRACT_PRICE",
        "priceProtect": false,
        "selfTradePreventionMode": "NONE", //self trading preventation mode
        "goodTillDate": 0
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Query-UM-Conditional-Order-History#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-UM-Conditional-Order-History#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-UM-Conditional-Order-History#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-UM-Conditional-Order-History#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Query-UM-Conditional-Order-History#response-example>)
