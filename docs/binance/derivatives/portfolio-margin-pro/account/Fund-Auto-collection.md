On this page

# Fund Auto-collection(USER_DATA)

## API Description

Transfers all assets from Futures Account to Margin account

## HTTP Request

POST `/sapi/v1/portfolio/auto-collection`

## Request Weight(IP)")

**1500**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * The BNB would not be collected from UM-PM account to the Portfolio Margin account.
>   * You can only use this function 500 times per hour in a rolling manner.
> 

## Response Example
    
    
    {  
        "msg": "success"  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account/Fund-Auto-collection#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Fund-Auto-collection#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account/Fund-Auto-collection#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Fund-Auto-collection#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account/Fund-Auto-collection#response-example>)

