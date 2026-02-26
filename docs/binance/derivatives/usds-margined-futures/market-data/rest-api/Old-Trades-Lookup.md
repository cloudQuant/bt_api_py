On this page

# Old Trades Lookup (MARKET_DATA)

## API Description

Get older market historical trades.

## HTTP Request

GET `/fapi/v1/historicalTrades`

## Request Weight

**20**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
limit| INT| NO| Default 100; max 500.  
fromId| LONG| NO| TradeId to fetch from. Default gets most recent trades.  
  
>   * Market trades means trades filled in the order book. Only market trades will be returned, which means the insurance fund trades and ADL trades won't be returned.
>   * Only supports data from within the last three months
> 

## Response Example
    
    
    [  
      {  
        "id": 28457,  
        "price": "4.00000100",  
        "qty": "12.00000000",  
        "quoteQty": "8000.00",  
        "time": 1499865549590,  
        "isBuyerMaker": true,  
      }  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Old-Trades-Lookup#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Old-Trades-Lookup#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Old-Trades-Lookup#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Old-Trades-Lookup#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Old-Trades-Lookup#response-example>)

