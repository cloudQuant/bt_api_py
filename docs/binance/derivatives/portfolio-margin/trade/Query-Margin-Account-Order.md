On this page

# Query Margin Account Order (USER_DATA)

## API Description

Query Margin Account Order

## HTTP Request

GET `/papi/v1/margin/order`

## Weight

**10**

## Parameters:

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
orderId| LONG| NO|   
origClientOrderId| STRING| NO|   
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
**Notes:**

  * Either `orderId` or `origClientOrderId` must be sent.
  * For some historical orders cummulativeQuoteQty will be < 0, meaning the data is not available at this time.

## Response:
    
    
    {  
       "clientOrderId": "ZwfQzuDIGpceVhKW5DvCmO",  
       "cummulativeQuoteQty": "0.00000000",  
       "executedQty": "0.00000000",  
       "icebergQty": "0.00000000",  
       "isWorking": true,  
       "orderId": 213205622,  
       "origQty": "0.30000000",  
       "price": "0.00493630",  
       "side": "SELL",  
       "status": "NEW",  
       "stopPrice": "0.00000000",  
       "symbol": "BNBBTC",  
       "time": 1562133008725,  
       "timeInForce": "GTC",  
       "type": "LIMIT",  
       "updateTime": 1562133008725，  
       "accountId": 152950866,  
       "selfTradePreventionMode": "EXPIRE_TAKER",  
       "preventedMatchId": null,  
       "preventedQuantity": null  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Order#http-request>)
  * [Weight](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Order#weight>)
  * [Parameters:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Order#parameters>)
  * [Response:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Order#response>)

