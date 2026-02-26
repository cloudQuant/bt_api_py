On this page

# Query CM Order(USER_DATA)

## API Description

Check an CM order's status.

## HTTP Request

GET `/papi/v1/cm/order`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
orderId| LONG| NO|   
origClientOrderId| STRING| NO|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
Notes:

>   * Either `orderId` or `origClientOrderId` must be sent.
>   * These orders will not be found: 
>     * order status is `CANCELED` or `EXPIRED`, **AND**
>     * order has NO filled trade, **AND**
>     * created time + 3 days < current time
> 

## Response Example
    
    
    {  
        "avgPrice": "0.0",  
        "clientOrderId": "abc",  
        "cumBase": "0",  
        "executedQty": "0",  
        "orderId": 1917641,  
        "origQty": "0.40",  
        "origType": "LIMIT",  
        "price": "0",  
        "reduceOnly": false,  
        "side": "BUY",  
        "status": "NEW",  
        "symbol": "BTCUSD_200925",  
        "pair": "BTCUSD",  
        "positionSide": "SHORT",  
        "time": 1579276756075,             // order time  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "updateTime": 1579276756075        // update time  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-CM-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-CM-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-CM-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-CM-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Query-CM-Order#response-example>)

