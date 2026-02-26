On this page

# Compressed/Aggregate Trades List

## API Description

Get compressed, aggregate trades. Market trades that fill in 100ms with the same price and the same taking side will have the quantity aggregated.

## HTTP Request

GET `/dapi/v1/aggTrades`

## Request Weight

**20**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
fromId| LONG| NO| ID to get aggregate trades from INCLUSIVE.  
startTime| LONG| NO| Timestamp in ms to get aggregate trades from INCLUSIVE.  
endTime| LONG| NO| Timestamp in ms to get aggregate trades until INCLUSIVE.  
limit| INT| NO| Default 500; max 1000.  
  
>   * support querying futures trade histories that are not older than one year
>   * If both `startTime` and `endTime` are sent, time between `startTime` and `endTime` must be less than 1 hour.
>   * If `fromId`, `startTime`, and `endTime` are not sent, the most recent aggregate trades will be returned.
>   * Only market trades will be aggregated and returned, which means the insurance fund trades and ADL trades won't be aggregated.
>   * Sending both `startTime`/`endTime` and `fromId` might cause response timeout, please send either `fromId` or `startTime`/`endTime`
> 

## Response Example
    
    
    [  
      {  
        "a": 416690,			// Aggregate tradeId  
        "p": "9642.4",  	 	// Price  
        "q": "3",  			 	// Quantity  
        "f": 595259,         	// First tradeId  
        "l": 595259,         	// Last tradeId  
        "T": 1591250548649, 	// Timestamp  
        "m": false,          	// Was the buyer the maker?  
      }  
    ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/market-data/Compressed-Aggregate-Trades-List#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Compressed-Aggregate-Trades-List#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Compressed-Aggregate-Trades-List#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Compressed-Aggregate-Trades-List#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/market-data/Compressed-Aggregate-Trades-List#response-example>)

