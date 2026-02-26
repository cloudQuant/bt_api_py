On this page

# Get BNB Burn Status (USER_DATA)

## API Description

Get user's BNB Fee Discount (Fee Discount On or Fee Discount Off )

## HTTP Request

GET `/fapi/v1/feeBurn`

## Request Weight

**30**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"feeBurn": true // "true": Fee Discount On; "false": Fee Discount Off  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Get-BNB-Burn-Status#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Get-BNB-Burn-Status#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Get-BNB-Burn-Status#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Get-BNB-Burn-Status#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Get-BNB-Burn-Status#response-example>)

