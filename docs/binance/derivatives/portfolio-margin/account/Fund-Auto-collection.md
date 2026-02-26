On this page

# Fund Auto-collection(TRADE)

## API Description

Fund collection for Portfolio Margin

## HTTP Request

`POST /papi/v1/auto-collection`

## Request Weight(IP)")

**750**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
>   * The BNB would not be collected from UM-PM account to the Portfolio Margin account.
>   * You can only use this function 500 times per hour in a rolling manner.
> 

## Response Example
    
    
    {  
        "msg": "success"  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Fund-Auto-collection#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Fund-Auto-collection#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin/account/Fund-Auto-collection#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Fund-Auto-collection#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Fund-Auto-collection#response-example>)

