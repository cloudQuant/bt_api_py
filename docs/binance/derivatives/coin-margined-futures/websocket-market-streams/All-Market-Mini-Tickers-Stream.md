On this page

# All Market Mini Tickers Stream

## Stream Description

24hr rolling window mini-ticker statistics for all symbols. These are NOT the statistics of the UTC day, but a 24hr rolling window from requestTime to 24hrs before. Note that only tickers that have changed will be present in the array.

## Stream Name

`!miniTicker@arr`

## Update Speed

**1000ms**

## Response Example
    
    
    [    
    	{  
    	  "e":"24hrMiniTicker",			// Event type  
    	  "E":1591267704450,			// Event time  
    	  "s":"BTCUSD_200626",			// Symbol  
    	  "ps":"BTCUSD",				// Pair  
    	  "c":"9561.7",					// Close price  
    	  "o":"9580.9",					// Open price  
    	  "h":"10000.0",				// High price  
    	  "l":"7000.0",					// Low price  
    	  "v":"487476",					// Total traded volume  
    	  "q":"33264343847.22378500"	// Total traded base asset volume  
    	}  
    ]  
    

  * [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#update-speed>)
  * [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#response-example>)

