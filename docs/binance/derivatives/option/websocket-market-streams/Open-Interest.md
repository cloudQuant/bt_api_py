On this page

# Open Interest

## Stream Description

Option open interest for specific underlying asset on specific expiration date. E.g.[ETH@openInterest@221125](<wss://nbstream.binance.com/eoptions/stream?streams=ETH@openInterest@221125>)

## Stream Name

`<underlyingAsset>@openInterest@<expirationDate>`

## Update Speed

**60s**

## Response Example
    
    
    [  
        {  
          "e":"openInterest",         // Event type  
          "E":1668759300045,          // Event time  
          "s":"ETH-221125-2700-C",    // option symbol  
          "o":"1580.87",              // Open interest in contracts  
          "h":"1912992.178168204"     // Open interest in USDT  
        }  
    ]  
    

  * [Stream Description](</docs/derivatives/option/websocket-market-streams/Open-Interest#stream-description>)
  * [Stream Name](</docs/derivatives/option/websocket-market-streams/Open-Interest#stream-name>)
  * [Update Speed](</docs/derivatives/option/websocket-market-streams/Open-Interest#update-speed>)
  * [Response Example](</docs/derivatives/option/websocket-market-streams/Open-Interest#response-example>)

