On this page

# UM Futures Account Configuration(USER_DATA)

## API Description

Query UM Futures account configuration

## HTTP Request

GET `/papi/v1/um/accountConfig`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {     
        "feeTier": 0,               // account commission tier   
        "canTrade": true,           // if can trade  
        "canDeposit": true,         // if can transfer in asset  
        "canWithdraw": true,        // if can transfer out asset  
        "dualSidePosition": true,  
        "updateTime": 1724416653850,            // reserved property, please ignore   
        "multiAssetsMargin": false,  
        "tradeGroupId": -1  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Account-Config#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Account-Config#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Account-Config#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Account-Config#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Account-Config#response-example>)

