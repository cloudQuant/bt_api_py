On this page

# Recent Trades List

## API Description

Get recent market trades

## HTTP Request

GET `/eapi/v1/trades`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES| Option trading pair, e.g BTC-200730-9000-C  
limit| INT| NO| Number of records Default:100 Max:500  
  
## Response Example
    
    
    [  
      {   
        "id":"1",             // TradeId  
        "symbol": "BTC-220722-19000-C",  
        "price": "1000",      // Completed trade price  
        "qty": "-0.1",        // Completed trade quantity  
        "quoteQty": "-100",   // Completed trade amount  
        "side": -1            // Completed trade direction（-1 Sell，1 Buy）  
        "time": 1592449455993,// Time   
      }  
    ]    
    

  * [API Description](</docs/derivatives/option/market-data/Recent-Trades-List#api-description>)
  * [HTTP Request](</docs/derivatives/option/market-data/Recent-Trades-List#http-request>)
  * [Request Weight](</docs/derivatives/option/market-data/Recent-Trades-List#request-weight>)
  * [Request Parameters](</docs/derivatives/option/market-data/Recent-Trades-List#request-parameters>)
  * [Response Example](</docs/derivatives/option/market-data/Recent-Trades-List#response-example>)

