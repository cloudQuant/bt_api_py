On this page

# Get Auto-Cancel All Open Orders (Kill-Switch) Config (TRADE)

## API Description

This endpoint returns the auto-cancel parameters for each underlying symbol. Note only active auto-cancel parameters will be returned, if countdownTime is set to 0 (ie. countdownTime has been turned off), the underlying symbol and corresponding countdownTime parameter will not be returned in the response.

## HTTP Request

GET `/eapi/v1/countdownCancelAll`

## Request Weight

1

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
underlying| STRING| NO| Option underlying, e.g BTCUSDT  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * countdownTime = 0 means the function is disabled.
> 

## Response Example
    
    
    {  
      "underlying": "ETHUSDT",  
      "countdownTime": 100000  
    }  
    

  * [API Description](</docs/derivatives/option/market-maker-endpoints/Get-Auto-Cancel-All-Open-Orders-Config#api-description>)
  * [HTTP Request](</docs/derivatives/option/market-maker-endpoints/Get-Auto-Cancel-All-Open-Orders-Config#http-request>)
  * [Request Weight](</docs/derivatives/option/market-maker-endpoints/Get-Auto-Cancel-All-Open-Orders-Config#request-weight>)
  * [Request Parameters](</docs/derivatives/option/market-maker-endpoints/Get-Auto-Cancel-All-Open-Orders-Config#request-parameters>)
  * [Response Example](</docs/derivatives/option/market-maker-endpoints/Get-Auto-Cancel-All-Open-Orders-Config#response-example>)

