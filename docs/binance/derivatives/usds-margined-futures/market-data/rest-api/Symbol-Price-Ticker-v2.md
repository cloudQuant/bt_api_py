On this page

# Symbol Price Ticker V2

## API Description

Latest price for a symbol or symbols.

## HTTP Request

GET `/fapi/v2/ticker/price`

**Weight:**

**1** for a single symbol;  
**2** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
  
>   * If the symbol is not sent, prices for all symbols will be returned in an array.
>   * The field `X-MBX-USED-WEIGHT-1M` in response header is not accurate from this endpoint, please ignore.
> 

## Response Example
    
    
    {  
      "symbol": "BTCUSDT",  
      "price": "6000.01",  
      "time": 1589437530011   // Transaction time  
    }  
    

> OR
    
    
    [  
    	{  
      		"symbol": "BTCUSDT",  
      		"price": "6000.01",  
      		"time": 1589437530011  
    	}  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker-v2#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker-v2#http-request>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker-v2#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Price-Ticker-v2#response-example>)

