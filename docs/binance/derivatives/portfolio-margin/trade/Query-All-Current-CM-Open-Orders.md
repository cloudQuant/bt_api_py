On this page

# Query All Current CM Open Orders(USER_DATA)

## API Description

Get all open orders on a symbol.

## HTTP Request

`GET /papi/v1/cm/openOrders`

## Request Weight

**1** for a single symbol; **40** when the symbol parameter is omitted **Careful** when accessing this with no symbol.

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
pair| STRING| NO|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * If the symbol is not sent, orders for all symbols will be returned in an array.
> 

## Response Example
    
    
    [  
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
        "positionSide": "SHORT",  
        "status": "NEW",  
        "symbol": "BTCUSD_200925",  
        "pair":"BTCUSD",  
        "time": 1579276756075,              // order time  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "updateTime": 1579276756075        // update time  
      }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Orders#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Orders#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Orders#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Orders#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Query-All-Current-CM-Open-Orders#response-example>)

