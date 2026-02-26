On this page

# BNB transfer(USER_DATA)

## API Description

BNB transfer can be between Margin Account and USDM Account

## HTTP Request

POST `/sapi/v1/portfolio/bnb-transfer`

## Request Weight(IP)")

**1500**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
amount| DECIMAL| YES|   
transferSide| STRING| YES| "TO_UM","FROM_UM"  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * You can only use this function 2 times per 10 minutes in a rolling manner
> 

## Response Example
    
    
    {  
         "tranId": 100000001  
    }   
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account/BNB-transfer#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/BNB-transfer#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account/BNB-transfer#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/BNB-transfer#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account/BNB-transfer#response-example>)

