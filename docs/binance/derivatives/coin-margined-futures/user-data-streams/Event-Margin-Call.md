On this page

# Event: Margin Call

## Event Description

  * When the user's position risk ratio is too high, this stream will be pushed.
  * This message is only used as risk guidance information and is not recommended for investment strategies.
  * In the case of a highly volatile market, there may be the possibility that the user's position has been liquidated at the same time when this stream is pushed out.

## Event Name

`MARGIN_CALL`

## Response Example
    
    
    {  
        "e":"MARGIN_CALL",    	  // Event Type  
        "E":1587727187525,		  // Event Time  
        "i": "SfsR",			  // Account Alias  
        "cw":"3.16812045",		  // Cross Wallet Balance. Only pushed with crossed position margin call  
        "p":[					  // Position(s) of Margin Call  
          {  
            "s":"BTCUSD_200925",  // Symbol  
            "ps":"LONG",		  // Position Side  
            "pa":"132",			  // Position Amount  
            "mt":"CROSSED",		  // Margin Type  
            "iw":"0",			  // Isolated Wallet (if isolated position)  
            "mp":"9187.17127000", // Mark Price  
            "up":"-1.166074",	  // Unrealized PnL  
            "mm":"1.614445"		  // Maintenance Margin Required  
          }  
        ]  
    }    
    

  * [Event Description](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Margin-Call#event-description>)
  * [Event Name](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Margin-Call#event-name>)
  * [Response Example](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Margin-Call#response-example>)

