On this page

# Contract Info Stream

## Stream Description

ContractInfo stream pushes when contract info updates(listing/settlement/contract bracket update). `bks` field only shows up when bracket gets updated.

## Stream Name

`!contractInfo`

## Update Speed

**Real-time**

## Response Example
    
    
    {  
        "e":"contractInfo",          // Event Type  
        "E":1669647330375,           // Event Time  
        "s":"APTUSD_PERP",           // Symbol  
        "ps":"APTUSD",               // Pair  
        "ct":"PERPETUAL",            // Contract type  
        "dt":4133404800000,          // Delivery date time   
        "ot":1666594800000,          // onboard date time   
        "cs":"TRADING",              // Contract status   
        "bks":[  
            {  
                "bs":1,              // Notional bracket  
                "bnf":0,             // Floor notional of this bracket  
                "bnc":5000,          // Cap notional of this bracket  
                "mmr":0.01,          // Maintenance ratio for this bracket  
                "cf":0,              // Auxiliary number for quick calculation   
                "mi":21,             // Min leverage for this bracket  
                "ma":50              // Max leverage for this bracket  
            },  
            {  
                "bs":2,  
                "bnf":5000,  
                "bnc":25000,  
                "mmr":0.025,  
                "cf":75,  
                "mi":11,  
                "ma":20  
            }  
        ]  
    }  
    

  * [Stream Description](</docs/derivatives/coin-margined-futures/websocket-market-streams/Contract-Info-Stream#stream-description>)
  * [Stream Name](</docs/derivatives/coin-margined-futures/websocket-market-streams/Contract-Info-Stream#stream-name>)
  * [Update Speed](</docs/derivatives/coin-margined-futures/websocket-market-streams/Contract-Info-Stream#update-speed>)
  * [Response Example](</docs/derivatives/coin-margined-futures/websocket-market-streams/Contract-Info-Stream#response-example>)

