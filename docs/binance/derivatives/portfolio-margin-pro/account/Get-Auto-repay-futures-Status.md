On this page

# Get Auto-repay-futures Status(USER_DATA)

## API Description

Query Auto-repay-futures Status

## HTTP Request

GET `/sapi/v1/portfolio/repay-futures-switch`

## Request Weight(IP)")

**30**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
        "autoRepay": true  //  "true" for turn on the auto-repay futures; "false" for turn off the auto-repay futures   
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account/Get-Auto-repay-futures-Status#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Get-Auto-repay-futures-Status#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account/Get-Auto-repay-futures-Status#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Get-Auto-repay-futures-Status#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account/Get-Auto-repay-futures-Status#response-example>)

