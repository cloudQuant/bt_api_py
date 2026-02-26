On this page

# All Book Tickers Stream

## Stream Description

Pushes any update to the best bid or ask's price or quantity in real-time for all symbols.

## Stream Name

`!bookTicker`

## Update Speed

`Real-time`

## Response Example
    
    
    {  
      "e":"bookTicker",         // Event type  
      "u":17242169,             // Order book update Id  
      "s":"BTCUSD_200626",      // Symbol  
      "ps":"BTCUSD",            // Pair  
      "b":"9548.1",             // Best bid price  
      "B":"52",                 // Best bid qty  
      "a":"9548.5",             // Best ask price  
      "A":"11",                 // Best ask qty  
      "T":1591268628155,        // Transaction time  
      "E":1591268628166         // Event time  
    }  
    

  * [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Book-Tickers-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Book-Tickers-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Book-Tickers-Stream#update-speed>)
  * [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/All-Book-Tickers-Stream#response-example>)

