On this page

# Change CM Position Mode(TRADE)

## API Description

Change user's position mode (Hedge Mode or One-way Mode ) on EVERY symbol in CM

## HTTP Request

POST `/papi/v1/cm/positionSide/dual`

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
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Change-CM-Position-Mode#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Change-CM-Position-Mode#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Change-CM-Position-Mode#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Change-CM-Position-Mode#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Change-CM-Position-Mode#response-example>)

