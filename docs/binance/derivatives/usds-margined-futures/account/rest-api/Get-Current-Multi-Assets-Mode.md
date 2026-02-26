On this page

# Get Current Multi-Assets Mode (USER_DATA)

## API Description

Get user's Multi-Assets mode (Multi-Assets Mode or Single-Asset Mode) on _**Every symbol**_

## HTTP Request

GET `/fapi/v1/multiAssetsMargin`

## Request Weight

**30**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"multiAssetsMargin": true // "true": Multi-Assets Mode; "false": Single-Asset Mode  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Multi-Assets-Mode#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Multi-Assets-Mode#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Multi-Assets-Mode#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Multi-Assets-Mode#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Multi-Assets-Mode#response-example>)

