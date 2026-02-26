On this page

# Symbol Price Ticker

## API Description

Latest price for a symbol or symbols.

## Method

`ticker.price`

## Request
    
    
    {  
       	"id": "9d32157c-a556-4d27-9866-66760a174b57",  
        "method": "ticker.price",  
        "params": {  
            "symbol": "BTCUSDT"  
        }  
    }  
    

**Weight:**

**1** for a single symbol;  
**2** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
  
>   * If the symbol is not sent, prices for all symbols will be returned in an array.
> 

## Response Example
    
    
    {  
      "id": "9d32157c-a556-4d27-9866-66760a174b57",  
      "status": 200,  
      "result": {  
    	"symbol": "BTCUSDT",  
    	"price": "6000.01",  
    	"time": 1589437530011   // Transaction time  
      },  
      "rateLimits": [  
        {  
          "rateLimitType": "REQUEST_WEIGHT",  
          "interval": "MINUTE",  
          "intervalNum": 1,  
          "limit": 2400,  
          "count": 2  
        }  
      ]  
    }  
    

> OR
    
    
    {  
      "id": "9d32157c-a556-4d27-9866-66760a174b57",  
      "status": 200,  
      "result": [  
    	{  
        	"symbol": "BTCUSDT",  
          	"price": "6000.01",  
          	"time": 1589437530011  
      	}  
      ],  
      "rateLimits": [  
        {  
          "rateLimitType": "REQUEST_WEIGHT",  
          "interval": "MINUTE",  
          "intervalNum": 1,  
          "limit": 2400,  
          "count": 2  
        }  
      ]  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Price-Ticker#api-description>)
  * [Method](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Price-Ticker#method>)
  * [Request](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Price-Ticker#request>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Price-Ticker#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Price-Ticker#response-example>)

