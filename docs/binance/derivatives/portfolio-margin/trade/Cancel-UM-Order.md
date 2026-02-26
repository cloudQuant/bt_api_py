On this page

# Cancel UM Order(TRADE)

## API Description

Cancel an active UM LIMIT order

## HTTP Request

DELETE `/papi/v1/um/order`

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
  
>   * Either `orderId` or `origClientOrderId` must be sent.
> 

## Response Example
    
    
    {  
        "avgPrice": "0.00000",  
        "clientOrderId": "myOrder1",  
        "cumQty": "0",  
        "cumQuote": "0",  
        "executedQty": "0",  
        "orderId": 4611875134427365377,  
        "origQty": "0.40",  
        "price": "0",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "SHORT",  
        "status": "CANCELED",  
        "symbol": "BTCUSDT",  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "updateTime": 1571110484038,  
        "selfTradePreventionMode": "NONE",   
        "goodTillDate": 0,  
        "priceMatch": "NONE"    
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Cancel-UM-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Cancel-UM-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Cancel-UM-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Cancel-UM-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Cancel-UM-Order#response-example>)

