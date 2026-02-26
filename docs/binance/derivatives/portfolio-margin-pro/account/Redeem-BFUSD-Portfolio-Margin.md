On this page

# Redeem BFUSD for Portfolio Margin(TRADE)

## API Description

Redeem BFUSD for all types of Portfolio Margin account

## HTTP Request

POST `/sapi/v1/portfolio/redeem`

## Request Weight(UID)")

**1500**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
fromAsset| STRING| YES| `BFUSD` only  
targetAsset| STRING| YES| `USDT` only  
amount| DECIMAL| YES|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
      "fromAsset": "BFUSD",  
      "targetAsset": "USDT",  
      "fromAssetQty": 9.99800001,  
      "targetAssetQty": 9.996000409998,  
      "rate": 0.9998   //redeem rate  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account/Redeem-BFUSD-Portfolio-Margin#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Redeem-BFUSD-Portfolio-Margin#http-request>)
  * [Request Weight(UID)](</docs/derivatives/portfolio-margin-pro/account/Redeem-BFUSD-Portfolio-Margin#request-weightuid>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Redeem-BFUSD-Portfolio-Margin#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account/Redeem-BFUSD-Portfolio-Margin#response-example>)

