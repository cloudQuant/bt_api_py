On this page

# Historical BLVT NAV Kline/Candlestick

## API Description

The BLVT NAV system is based on Binance Futures, so the endpoint is based on fapi

## HTTP Request

GET `/fapi/v1/lvtKlines`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES| token name, e.g. "BTCDOWN", "BTCUP"  
interval| ENUM| YES|   
startTime| LONG| NO|   
endTime| LONG| NO|   
limit| INT| NO| default 500, max 1000  
  
## Response Example
    
    
    [  
    	[  
      		1598371200000,		// Open time  
          	"5.88275270",		// Open NAV price  
          	"6.03142087",		// Highest NAV price  
          	"5.85749741",		// Lowest NAV price  
          	"5.99403551",		// Close (or the latest) NAV price  
          	"2.28602984",		// Real leverage  
          	1598374799999,		// Close time  
          	"0",				// Ignore  
          	6209,				// Number of NAV update  
          	"14517.64507907",	// Ignore  
          	"0",				// Ignore  
          	"0"					// Ignore  
    	]  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Historical-BLVT-NAV-Kline-Candlestick#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Historical-BLVT-NAV-Kline-Candlestick#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Historical-BLVT-NAV-Kline-Candlestick#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Historical-BLVT-NAV-Kline-Candlestick#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Historical-BLVT-NAV-Kline-Candlestick#response-example>)

