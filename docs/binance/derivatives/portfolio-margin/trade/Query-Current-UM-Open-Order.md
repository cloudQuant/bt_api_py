On this page

# Query Current UM Open Order(USER_DATA)

## API Description

Query current UM open order

## HTTP Request

GET `/papi/v1/um/openOrder`

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
>   * If the queried order has been filled or cancelled, the error message "Order does not exist" will be returned.
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
        "updateTime": 1579276756075，  
        "selfTradePreventionMode": "NONE",   
        "goodTillDate": 0,  
        "priceMatch": "NONE"              
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-Current-UM-Open-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-Current-UM-Open-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-Current-UM-Open-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-Current-UM-Open-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Query-Current-UM-Open-Order#response-example>)

