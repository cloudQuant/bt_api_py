On this page

# Auto-Cancel All Open Orders (Kill-Switch) Heartbeat (TRADE)

## API Description

This endpoint resets the time from which the countdown will begin to the time this messaged is received. It should be called repeatedly as heartbeats. Multiple heartbeats can be updated at once by specifying the underlying symbols as a list (ex. BTCUSDT,ETHUSDT) in the underlyings parameter.

## HTTP Request

POST `/eapi/v1/countdownCancelAllHeartBeat`

## Request Weight

10

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
underlyings| STRING| YES| Option Underlying Symbols, e.g BTCUSDT,ETHUSDT  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * The response will only include underlying symbols where the heartbeat has been successfully updated.
> 

## Response Example
    
    
    {  
     "underlyings":["BTCUSDT","ETHUSDT"]  
    }  
    

  * [API Description](</docs/derivatives/option/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat#api-description>)
  * [HTTP Request](</docs/derivatives/option/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat#http-request>)
  * [Request Weight](</docs/derivatives/option/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat#request-weight>)
  * [Request Parameters](</docs/derivatives/option/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat#request-parameters>)
  * [Response Example](</docs/derivatives/option/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat#response-example>)

