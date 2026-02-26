On this page

# Toggle BNB Burn On Futures Trade (TRADE)

## API Description

Change user's BNB Fee Discount (Fee Discount On or Fee Discount Off ) on _**EVERY symbol**_

## HTTP Request

POST `/fapi/v1/feeBurn`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
feeBurn| STRING| YES| "true": Fee Discount On; "false": Fee Discount Off  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"code": 200,  
    	"msg": "success"  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Toggle-BNB-Burn-On-Futures-Trade#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Toggle-BNB-Burn-On-Futures-Trade#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Toggle-BNB-Burn-On-Futures-Trade#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Toggle-BNB-Burn-On-Futures-Trade#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Toggle-BNB-Burn-On-Futures-Trade#response-example>)

