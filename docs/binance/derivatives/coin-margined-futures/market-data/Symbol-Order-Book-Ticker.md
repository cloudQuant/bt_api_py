On this page

# Symbol Order Book Ticker

## API Description

Best price/qty on the order book for a symbol or symbols.

## HTTP Request

GET `/dapi/v1/ticker/bookTicker`

## Request Weight

**2** for a single symbol, **5** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
pair| STRING| NO|   
  
>   * Symbol and pair cannot be sent together
>   * If a pair is sent,tickers for all symbols of the pair will be returned
>   * If either a pair or symbol is sent, tickers for all symbols of all pairs will be returned
> 

## Response Example
    
    
    [  
    	{  
    	    "lastUpdateId": 1027024,  
      		"symbol": "BTCUSD_200626",  
      		"pair": "BTCUSD",  
      		"bidPrice": "9650.1",  
      		"bidQty": "16",  
      		"askPrice": "9650.3",  
      		"askQty": "7",  
      		"time": 1591257300345  
    	}  
    ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/market-data/Symbol-Order-Book-Ticker#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Symbol-Order-Book-Ticker#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Symbol-Order-Book-Ticker#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Symbol-Order-Book-Ticker#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/market-data/Symbol-Order-Book-Ticker#response-example>)

