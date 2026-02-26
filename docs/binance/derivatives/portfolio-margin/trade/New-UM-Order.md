On this page

# New UM Order (TRADE)

## API Description

Place new UM order

## HTTP Request

POST `/papi/v1/um/order`

## Request Weight(Order)")

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
side| ENUM| YES|   
positionSide| ENUM| NO| Default `BOTH` for One-way Mode ; `LONG` or `SHORT` for Hedge Mode. It must be sent in Hedge Mode.  
type| ENUM| YES| `LIMIT`, `MARKET`  
timeInForce| ENUM| NO|   
quantity| DECIMAL| NO|   
reduceOnly| STRING| NO| "true" or "false". default "false". Cannot be sent in Hedge Mode .  
price| DECIMAL| NO|   
newClientOrderId| STRING| NO| A unique id among open orders. Automatically generated if not sent. Can only be string following the rule: `^[\.A-Z\:/a-z0-9_-]{1,32}$`  
newOrderRespType| ENUM| NO| `ACK`, `RESULT`, default `ACK`  
priceMatch| ENUM| NO| only avaliable for `LIMIT`/`STOP`/`TAKE_PROFIT` order; can be set to `OPPONENT`/ `OPPONENT_5`/ `OPPONENT_10`/ `OPPONENT_20`: /`QUEUE`/ `QUEUE_5`/ `QUEUE_10`/ `QUEUE_20`; Can't be passed together with `price`  
selfTradePreventionMode| ENUM| NO| `NONE`:No STP / `EXPIRE_TAKER`:expire taker order when STP triggers/ `EXPIRE_MAKER`:expire taker order when STP triggers/ `EXPIRE_BOTH`:expire both orders when STP triggers  
goodTillDate| LONG| NO| order cancel time for timeInForce `GTD`, mandatory when `timeInforce` set to `GTD`; order the timestamp only retains second-level precision, ms part will be ignored; The goodTillDate timestamp must be greater than the current time plus 600 seconds and smaller than 253402300799000Mode. It must be sent in Hedge Mode.  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
Additional mandatory parameters based on type:

Type| Additional mandatory parameters  
---|---  
`LIMIT`| `timeInForce`, `quantity`, `price`  
`MARKET`| `quantity`  
  
>   * If `newOrderRespType` is sent as `RESULT` : 
>     * `MARKET` order: the final FILLED result of the order will be return directly.
>     * `LIMIT` order with special `timeInForce`: the final status result of the order(FILLED or EXPIRED) will be returned directly.
>   * `selfTradePreventionMode` is only effective when `timeInForce` set to `IOC` or `GTC` or `GTD`.
>   * In extreme market conditions, timeInForce `GTD` order auto cancel time might be delayed comparing to `goodTillDate`
> 

## Response Example
    
    
    {  
        "clientOrderId": "testOrder",  
        "cumQty": "0",  
        "cumQuote": "0",  
        "executedQty": "0",  
        "orderId": 22542179,  
        "avgPrice": "0.00000",  
        "origQty": "10",  
        "price": "0",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "SHORT",  
        "status": "NEW",  
        "symbol": "BTCUSDT",  
        "timeInForce": "GTD",  
        "type": "MARKET",  
        "selfTradePreventionMode": "NONE", //self trading preventation mode  
        "goodTillDate": 1693207680000,      //order pre-set auot cancel time for TIF GTD order   
        "updateTime": 1566818724722,  
        "priceMatch": "NONE"  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade#http-request>)
  * [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade#request-weightorder>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade#response-example>)

