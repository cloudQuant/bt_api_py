On this page

# Change Position Mode(TRADE)

## API Description

Change user's position mode (Hedge Mode or One-way Mode ) on _**EVERY symbol**_

## HTTP Request

POST `/dapi/v1/positionSide/dual`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
dualSidePosition| STRING| YES| "true": Hedge Mode; "false": One-way Mode  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"code": 200,  
    	"msg": "success"  
    }  
    

  * [API Description](</docs/derivatives/coin-margined-futures/trade/Change-Position-Mode#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Change-Position-Mode#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/trade/Change-Position-Mode#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Change-Position-Mode#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/trade/Change-Position-Mode#response-example>)

