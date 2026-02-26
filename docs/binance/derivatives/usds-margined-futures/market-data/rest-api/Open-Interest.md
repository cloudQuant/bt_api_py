On this page

# Open Interest

## API Description

Get present open interest of a specific symbol.

## HTTP Request

GET `/fapi/v1/openInterest`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
  
## Response Example
    
    
    {  
    	"openInterest": "10659.509",   
    	"symbol": "BTCUSDT",  
    	"time": 1589437530011   // Transaction time  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest#response-example>)

