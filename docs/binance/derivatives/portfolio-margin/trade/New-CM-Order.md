On this page

# New CM Order(TRADE)

## API Description

Place new CM order

## HTTP Request

POST `/papi/v1/cm/order`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
side| ENUM| YES|   
positionSide| ENUM| NO| Default `BOTH` for One-way Mode ; `LONG` or `SHORT` for Hedge Mode. It must be sent in Hedge Mode.  
type| ENUM| YES| "LIMIT", "MARKET"  
timeInForce| ENUM| NO|   
quantity| DECIMAL| NO|   
reduceOnly| STRING| NO| "true" or "false". default "false". Cannot be sent in Hedge Mode.  
price| DECIMAL| NO|   
newClientOrderId| STRING| NO| A unique id among open orders. Automatically generated if not sent. Can only be string following the rule: `^[\.A-Z\:/a-z0-9_-]{1,32}$`  
newOrderRespType| ENUM| NO| "ACK", "RESULT", default "ACK"  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
Additional mandatory parameters based on `type`:

Type| Additional mandatory parameters  
---|---  
`LIMIT`| `timeInForce`, `quantity`, `price`  
`MARKET`| `quantity`  
  
  * If `newOrderRespType` is sent as `RESULT` : 
    * `MARKET` order: the final FILLED result of the order will be return directly.
    * `LIMIT` order with special `timeInForce`: the final status result of the order(FILLED or EXPIRED) will be returned directly.

## Response Example
    
    
    {  
        "clientOrderId": "testOrder",  
        "cumQty": "0",  
        "cumBase": "0",  
        "executedQty": "0",  
        "orderId": 22542179,  
        "avgPrice": "0.0",  
        "origQty": "10",  
        "price": "0",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "SHORT",   
        "status": "NEW",  
        "symbol": "BTCUSD_200925",  
        "pair": "BTCUSD",  
        "timeInForce": "GTC",  
        "type": "MARKET",  
        "updateTime": 1566818724722  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/New-CM-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/New-CM-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/New-CM-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/New-CM-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/New-CM-Order#response-example>)

