On this page

# Mint BFUSD for Portfolio Margin(TRADE)

## API Description

Mint BFUSD for all types of Portfolio Margin account

## HTTP Request

POST `/sapi/v1/portfolio/mint`

## Request Weight(UID)")

**1500**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
fromAsset| STRING| YES| `USDT` only  
targetAsset| STRING| YES| `BFUSD` only  
amount| DECIMAL| YES|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
      "fromAsset":"USDT",  
      "targetAsset": "BFUSD",  
      "fromAssetQty": 10,  
      "targetAssetQty": 9.9980,  
      "rate": 0.9998  // mint rate  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account/Mint-BFUSD-Portfolio-Margin#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Mint-BFUSD-Portfolio-Margin#http-request>)
  * [Request Weight(UID)](</docs/derivatives/portfolio-margin-pro/account/Mint-BFUSD-Portfolio-Margin#request-weightuid>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Mint-BFUSD-Portfolio-Margin#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account/Mint-BFUSD-Portfolio-Margin#response-example>)

