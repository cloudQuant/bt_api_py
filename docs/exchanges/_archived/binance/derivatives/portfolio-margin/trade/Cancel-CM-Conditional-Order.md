On this page

# Cancel CM Conditional Order(TRADE)

## API Description

Cancel CM Conditional Order

## HTTP Request

DELETE `/papi/v1/cm/conditional/order`

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

>   * Either `strategyId` or `newClientStrategyId` must be sent.
>

## Response Example


    {
        "newClientStrategyId": "myOrder1",
        "strategyId":123445,
        "strategyStatus":"CANCELED",
        "strategyType": "TRAILING_STOP_MARKET",
        "origQty": "11",
        "price": "0",
        "reduceOnly": false,
        "side": "BUY",
        "positionSide": "SHORT",
        "stopPrice": "9300",                // please ignore when order type is TRAILING_STOP_MARKET
        "symbol": "BTCUSD",
        "timeInForce": "GTC",
        "activatePrice": "9020",            // activation price, only return with TRAILING_STOP_MARKET order
        "priceRate": "0.3",                 // callback rate, only return with TRAILING_STOP_MARKET order
        "bookTime": 1566818724710,
        "updateTime": 1566818724722,
        "workingType":"CONTRACT_PRICE",
        "priceProtect": false
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Cancel-CM-Conditional-Order#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Cancel-CM-Conditional-Order#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Cancel-CM-Conditional-Order#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Cancel-CM-Conditional-Order#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Cancel-CM-Conditional-Order#response-example>)
