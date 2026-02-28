On this page

# Query Current Open Order(USER_DATA)

## API Description

Query Current Open Order

## HTTP Request

GET `/dapi/v1/openOrder`

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

>   *Either`orderId` or `origClientOrderId` must be sent
>  * If the queried order has been filled or cancelled, the error message "Order does not exist" will be returned.
>

## Response Example


    {
          "avgPrice": "0.0",
          "clientOrderId": "abc",
          "cumBase": "0",
          "executedQty": "0",
          "orderId": 1917641,
          "origQty": "0.40",
          "origType": "TRAILING_STOP_MARKET",
          "price": "0",
          "reduceOnly": false,
          "side": "BUY",
          "positionSide": "SHORT",
          "status": "NEW",
          "stopPrice": "9300",                // please ignore when order type is TRAILING_STOP_MARKET
          "closePosition": false,               // if Close-All
          "symbol": "BTCUSD_200925",
          "pair": "BTCUSD"
          "time": 1579276756075,                // order time
          "timeInForce": "GTC",
          "type": "TRAILING_STOP_MARKET",
          "activatePrice": "9020",            // activation price, only return with TRAILING_STOP_MARKET order
          "priceRate": "0.3",                    // callback rate, only return with TRAILING_STOP_MARKET order
          "updateTime": 1579276756075,
          "workingType": "CONTRACT_PRICE",
          "priceProtect": false               // if conditional order trigger is protected
          "priceMatch": "NONE",               // price match mode
          "selfTradePreventionMode": "NONE"   // self trading preventation mode
    }


  - [API Description](</docs/derivatives/coin-margined-futures/trade/Query-Current-Open-Order#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Query-Current-Open-Order#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/trade/Query-Current-Open-Order#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Query-Current-Open-Order#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/trade/Query-Current-Open-Order#response-example>)
