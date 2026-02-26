On this page

# Diff. Book Depth Streams

## Stream Description

Bids and asks, pushed every 250 milliseconds, 500 milliseconds, 100 milliseconds (if existing)

## Stream Name

`<symbol>@depth` OR `<symbol>@depth@500ms` OR `<symbol>@depth@100ms`

## Update Speed

**250ms** , **500ms** , **100ms**

## Response Example
    
    
    {  
      "e": "depthUpdate", // Event type  
      "E": 123456789,     // Event time  
      "T": 123456788,     // Transaction time   
      "s": "BTCUSDT",     // Symbol  
      "U": 157,           // First update ID in event  
      "u": 160,           // Final update ID in event  
      "pu": 149,          // Final update Id in last stream(ie `u` in last stream)  
      "b": [              // Bids to be updated  
        [  
          "0.0024",       // Price level to be updated  
          "10"            // Quantity  
        ]  
      ],  
      "a": [              // Asks to be updated  
        [  
          "0.0026",       // Price level to be updated  
          "100"          // Quantity  
        ]  
      ]  
    }  
    

  * [Stream Description](</docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#stream-description>)
  * [Stream Name](</docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#stream-name>)
  * [Update Speed](</docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#update-speed>)
  * [Response Example](</docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams#response-example>)

