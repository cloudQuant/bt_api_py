On this page

# Open Interest

## API Description

Get present open interest of a specific symbol.

## HTTP Request

GET `/dapi/v1/openInterest`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
  
## Response Example
    
    
    {  
    	"symbol": "BTCUSD_200626",  
    	"pair": "BTCUSD",  
    	"openInterest": "15004",  
    	"contractType": "CURRENT_QUARTER",  
    	"time": 1591261042378  
    }  
    

  * [API Description](</docs/derivatives/coin-margined-futures/market-data/Open-Interest#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Open-Interest#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Open-Interest#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Open-Interest#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/market-data/Open-Interest#response-example>)

