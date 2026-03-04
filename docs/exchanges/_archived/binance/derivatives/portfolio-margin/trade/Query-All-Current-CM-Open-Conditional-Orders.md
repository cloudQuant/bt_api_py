On this page

# Query All Current CM Open Conditional Orders (USER_DATA)

## API Description

Get all open conditional orders on a symbol. **Careful** when accessing this with no symbol.

## HTTP Request

GET `/papi/v1/cm/conditional/openOrders`

## Request Weight

- *1**for a single symbol;**40** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   * If the symbol is not sent, orders for all symbols will be returned in an array.
>

## Response Example


    [
      {
        "newClientStrategyId": "abc",
        "strategyId":123445,
        "strategyStatus":"NEW",
        "strategyType": "TRAILING_STOP_MARKET",
        "origQty": "0.40",
        "price": "0",
        "reduceOnly": false,
        "side": "BUY",
        "positionSide": "SHORT",
        "stopPrice": "9300",                // please ignore when order type is TRAILING_STOP_MARKET
        "symbol": "BTCUSD",
        "bookTime": 1566818724710,              // order time
        "updateTime": 1566818724722,
        "timeInForce": "GTC",
        "activatePrice": "9020",            // activation price, only return with TRAILING_STOP_MARKET order
        "priceRate": "0.3"                // callback rate, only return with TRAILING_STOP_MARKET order
      }
    ]


  - [API Description](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Conditional-Orders#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Conditional-Orders#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Conditional-Orders#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Conditional-Orders#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Conditional-Orders#response-example>)
