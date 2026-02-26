On this page

# Individual Symbol Mini Ticker Stream

## Stream Description

24hr rolling window mini-ticker statistics for a single symbol. These are NOT the statistics of the UTC day, but a 24hr rolling window from requestTime to 24hrs before.

## Stream Name

`<symbol>@miniTicker`

## Update Speed

**500ms**

## Response Example
    
    
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
    

  * [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Individual-Symbol-Mini-Ticker-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Individual-Symbol-Mini-Ticker-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Individual-Symbol-Mini-Ticker-Stream#update-speed>)
  * [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Individual-Symbol-Mini-Ticker-Stream#response-example>)

