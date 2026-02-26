On this page

# Get Current Position Mode(USER_DATA)

## API Description

Get user's position mode (Hedge Mode or One-way Mode ) on _**EVERY symbol**_

## HTTP Request

GET `/dapi/v1/positionSide/dual`

## Request Weight

**30**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"dualSidePosition": true // "true": Hedge Mode; "false": One-way Mode  
    }  
    

  * [API Description](</docs/derivatives/coin-margined-futures/account/Get-Current-Position-Mode#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/account/Get-Current-Position-Mode#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/account/Get-Current-Position-Mode#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/account/Get-Current-Position-Mode#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/account/Get-Current-Position-Mode#response-example>)

