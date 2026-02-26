On this page

# Cancel All UM Open Orders(TRADE)

## API Description

Cancel all active LIMIT orders on specific symbol

## HTTP Request

DELETE `/papi/v1/um/allOpenOrders`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
        "code": 200,   
        "msg": "The operation of cancel all open order is done."  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Orders#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Orders#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Orders#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Orders#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Cancel-All-UM-Open-Orders#response-example>)

