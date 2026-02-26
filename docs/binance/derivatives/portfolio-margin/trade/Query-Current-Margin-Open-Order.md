On this page

# Query Current Margin Open Order (USER_DATA)

## API Description

Query Current Margin Open Order

## HTTP Request

GET `/papi/v1/margin/openOrders`

## Weight

**5**

## Parameters:

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
**Notes:**

  * If the `symbol` is not sent, orders for all symbols will be returned in an array.
  * When all symbols are returned, the number of requests counted against the rate limiter is equal to the number of symbols currently trading on the exchange.

## Response:
    
    
    [  
       {  
           "clientOrderId": "qhcZw71gAkCCTv0t0k8LUK",  
           "cummulativeQuoteQty": "0.00000000",  
           "executedQty": "0.00000000",  
           "icebergQty": "0.00000000",  
           "isWorking": true,  
           "orderId": 211842552,  
           "origQty": "0.30000000",  
           "price": "0.00475010",  
           "side": "SELL",  
           "status": "NEW",  
           "stopPrice": "0.00000000",  
           "symbol": "BNBBTC",  
           "time": 1562040170089,  
           "timeInForce": "GTC",  
           "type": "LIMIT",  
           "updateTime": 1562040170089，  
           "accountId": 152950866,  
           "selfTradePreventionMode": "EXPIRE_TAKER",  
           "preventedMatchId": null,  
           "preventedQuantity": null  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-Current-Margin-Open-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-Current-Margin-Open-Order#http-request>)
  * [Weight](</docs/derivatives/portfolio-margin/trade/Query-Current-Margin-Open-Order#weight>)
  * [Parameters:](</docs/derivatives/portfolio-margin/trade/Query-Current-Margin-Open-Order#parameters>)
  * [Response:](</docs/derivatives/portfolio-margin/trade/Query-Current-Margin-Open-Order#response>)

