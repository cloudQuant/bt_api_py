On this page

# Query User Rate Limit (USER_DATA)

## API Description

Query User Rate Limit

## HTTP Request

GET `/fapi/v1/rateLimit/order`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    [  
      {  
        "rateLimitType": "ORDERS",  
        "interval": "SECOND",  
        "intervalNum": 10,  
        "limit": 10000,  
      },  
      {  
        "rateLimitType": "ORDERS",  
        "interval": "MINUTE",  
        "intervalNum": 1,  
        "limit": 20000,  
      }  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Query-Rate-Limit#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Query-Rate-Limit#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Query-Rate-Limit#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Query-Rate-Limit#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Query-Rate-Limit#response-example>)

