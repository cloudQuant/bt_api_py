On this page

# New CM Conditional Order(TRADE)

## API Description

New CM Conditional Order

## HTTP Request

POST `/papi/v1/cm/conditional/order`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
side| ENUM| YES|   
positionSide| ENUM| NO| Default `BOTH` for One-way Mode ; `LONG` or `SHORT` for Hedge Mode. It must be sent in Hedge Mode.  
strategyType| ENUM| YES| "STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET", and "TRAILING_STOP_MARKET"  
timeInForce| ENUM| NO|   
quantity| DECIMAL| NO|   
reduceOnly| STRING| NO| "true" or "false". default "false". Cannot be sent in Hedge Mode  
price| DECIMAL| NO|   
workingType| ENUM| NO| stopPrice triggered by: "MARK_PRICE", "CONTRACT_PRICE". Default "CONTRACT_PRICE"  
priceProtect| STRING| NO| "TRUE" or "FALSE", default "FALSE". Used with `STOP`/`STOP_MARKET` or `TAKE_PROFIT`/`TAKE_PROFIT_MARKET` orders  
newClientStrategyId| STRING| NO| A unique id among open orders. Automatically generated if not sent. Can only be string following the rule: `^[\.A-Z\:/a-z0-9_-]{1,36}$`  
stopPrice| DECIMAL| NO| Used with `STOP/STOP_MARKET` or `TAKE_PROFIT/TAKE_PROFIT_MARKET` orders.  
activationPrice| DECIMAL| NO| Used with `TRAILING_STOP_MARKET` orders, default as the mark price  
callbackRate| DECIMAL| NO| Used with `TRAILING_STOP_MARKET` orders, min 0.1, max 5 where 1 for 1%  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
Additional mandatory parameters based on type:

Type| Additional mandatory parameters  
---|---  
`STOP/TAKE_PROFIT`| `quantity`, `price`, `stopPrice`  
`STOP_MARKET/TAKE_PROFIT_MARKET`| `stopPrice`  
`TRAILING_STOP_MARKET`| `callbackRate`  
  
  * Order with type `STOP/TAKE_PROFIT`, parameter `timeInForce` can be sent ( default `GTC`).

  * Condition orders will be triggered when:

    * `STOP`, `STOP_MARKET`: 
      * BUY: "MARK_PRICE" >= `stopPrice`
      * SELL: "MARK_PRICE" <= `stopPrice`
    * `TAKE_PROFIT`, `TAKE_PROFIT_MARKET`: 
      * BUY: "MARK_PRICE" <= `stopPrice`
      * SELL: "MARK_PRICE" >= `stopPrice`
    * `TRAILING_STOP_MARKET`: 
      * BUY: the lowest mark price after order placed `<= `activationPrice`, and the latest mark price >`= the lowest mark price * (1 + `callbackRate`)
      * SELL: the highest mark price after order placed >= `activationPrice`, and the latest mark price <= the highest mark price * (1 - `callbackRate`)
  * For `TRAILING_STOP_MARKET`, if you got such error code. `{"code": -2021, "msg": "Order would immediately trigger."}` means that the parameters you send do not meet the following requirements:

    * BUY: `activationPrice` should be smaller than latest mark price.
    * SELL: `activationPrice` should be larger than latest mark price.
  * Condition orders will be triggered when:

    * If parameter`priceProtect`is sent as true: 
      * when price reaches the `stopPrice` ，the difference rate between "MARK_PRICE" and "CONTRACT_PRICE" cannot be larger than the "triggerProtect" of the symbol
      * "triggerProtect" of a symbol can be got from `GET /fapi/v1/exchangeInfo`
    * `STOP`, `STOP_MARKET`: 
      * BUY: latest price ("MARK_PRICE" or "CONTRACT_PRICE") >= `stopPrice`
      * SELL: latest price ("MARK_PRICE" or "CONTRACT_PRICE") <= `stopPrice`
    * `TAKE_PROFIT`, `TAKE_PROFIT_MARKET`: 
      * BUY: latest price ("MARK_PRICE" or "CONTRACT_PRICE") <= `stopPrice`
      * SELL: latest price ("MARK_PRICE" or "CONTRACT_PRICE") >= `stopPrice`

## Response Example
    
    
    {  
        "newClientStrategyId": "testOrder",  
        "strategyId":123445,  
        "strategyStatus":"NEW",  
        "strategyType": "TRAILING_STOP_MARKET",   
        "origQty": "10",  
        "price": "0",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "SHORT",  
        "stopPrice": "9300",        // please ignore when order type is TRAILING_STOP_MARKET  
        "symbol": "BTCUSD_200925",  
        "pair": "BTCUSD",  
        "timeInForce": "GTC",  
        "activatePrice": "9020",    // activation price, only return with TRAILING_STOP_MARKET order  
        "priceRate": "0.3",         // callback rate, only return with TRAILING_STOP_MARKET order  
        "bookTime": 1566818724710,  // order place time  
        "updateTime": 1566818724722  
        "workingType":"CONTRACT_PRICE",  
        "priceProtect": false     
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/New-CM-Conditional-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/New-CM-Conditional-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/New-CM-Conditional-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/New-CM-Conditional-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/New-CM-Conditional-Order#response-example>)

