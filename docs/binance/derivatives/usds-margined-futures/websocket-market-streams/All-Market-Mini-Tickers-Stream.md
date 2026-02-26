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
        "e": "24hrMiniTicker",  // Event type  
        "E": 123456789,         // Event time  
        "s": "BTCUSDT",         // Symbol  
        "c": "0.0025",          // Close price  
        "o": "0.0010",          // Open price  
        "h": "0.0025",          // High price  
        "l": "0.0010",          // Low price  
        "v": "10000",           // Total traded base asset volume  
        "q": "18"               // Total traded quote asset volume  
      }  
    ]  
    

  * [Stream Description](</docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#update-speed>)
  * [Response Example](</docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Mini-Tickers-Stream#response-example>)

