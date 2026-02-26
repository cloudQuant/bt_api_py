On this page

# Change Multi-Assets Mode (TRADE)

## API Description

Change user's Multi-Assets mode (Multi-Assets Mode or Single-Asset Mode) on _**Every symbol**_

## HTTP Request

POST `/fapi/v1/multiAssetsMargin`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
multiAssetsMargin| STRING| YES| "true": Multi-Assets Mode; "false": Single-Asset Mode  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"code": 200,  
    	"msg": "success"  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Multi-Assets-Mode#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Multi-Assets-Mode#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Multi-Assets-Mode#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Multi-Assets-Mode#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Multi-Assets-Mode#response-example>)

