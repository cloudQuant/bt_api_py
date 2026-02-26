On this page

# Mark Price Stream

## Stream Description

Mark price update stream

## Stream Name

`<symbol>@markPrice` OR `<symbol>@markPrice@1s`

## Update Speed

**3000ms** OR **1000ms**

## Response Example
    
    
    {  
    	"e":"markPriceUpdate",	// Event type  
      	"E":1596095725000,		// Event time  
       	"s":"BTCUSD_201225",	// Symbol  
      	"p":"10934.62615417",	// Mark Price  
      	"P":"10962.17178236",	// Estimated Settle Price, only useful in the last hour before the settlement starts.  
    	"i":"10933.62615417",   // Index Price   
      	"r":"",					// funding rate for perpetual symbol, "" will be shown for delivery symbol  
      	"T":0					// next funding time for perpetual symbol, 0 will be shown for delivery symbol  
    }  
    

  * [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-Stream#update-speed>)
  * [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Mark-Price-Stream#response-example>)

