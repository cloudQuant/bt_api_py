On this page

# Query Index Price Constituents

## API Description

Query index price constituents

## HTTP Request

GET `/fapi/v1/constituents`

## Request Weight

**2**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
  
## Response Example
    
    
    {  
        "symbol": "BTCUSDT",  
        "time": 1697421272043,  
        "constituents": [  
            {  
                "exchange": "binance",  
                "symbol": "BTCUSDT"  
            },  
            {  
                "exchange": "okex",  
                "symbol": "BTC-USDT"  
            },  
            {  
                "exchange": "huobi",  
                "symbol": "btcusdt"  
            },  
            {  
                "exchange": "coinbase",  
                "symbol": "BTC-USDT"  
            }  
        ]  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Constituents#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Constituents#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Constituents#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Constituents#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Constituents#response-example>)

