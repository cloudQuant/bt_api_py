On this page

# Event: Order Update

## Event Description

When new order created, modified, order status changed will push such event. event type is `ORDER_TRADE_UPDATE`.

**Side**

  * BUY
  * SELL

**Position side:**

  * BOTH
  * LONG
  * SHORT

**Order Type**

  * MARKET
  * LIMIT
  * STOP
  * TAKE_PROFIT
  * LIQUIDATION

**Execution Type**

  * NEW
  * CANCELED
  * CALCULATED - Liquidation Execution
  * EXPIRED
  * TRADE
  * AMENDMENT - Order Modified

**Order Status**

  * NEW
  * PARTIALLY_FILLED
  * FILLED
  * CANCELED
  * EXPIRED
  * EXPIRED_IN_MATCH

**Time in force**

  * GTC
  * IOC
  * FOK
  * GTX

**Liquidation and ADL:**

  * If user gets liquidated due to insufficient margin balance:

    * `c` shows as "autoclose-XXX"，`X` shows as "NEW"
  * If user has enough margin balance but gets ADL:

    * `c` shows as “adl_autoclose”，`X` shows as “NEW”

## Event Name

`ORDER_TRADE_UPDATE`

## Response Example
    
    
    {  
      "e":"ORDER_TRADE_UPDATE",		// Event Type  
      "E":1591274595442,			// Event Time  
      "T":1591274595442,			// Transaction Time  
      "i":"SfsR",					// Account Alias  
      "o":{								  
        "s":"BTCUSD_200925",		// Symbol  
        "c":"TEST",					// Client Order Id  
          // special client order id:  
          // starts with "autoclose-": liquidation order  
          // "adl_autoclose": ADL auto close order  
          // "delivery_autoclose-": settlement order for delisting or delivery  
        "S":"SELL",					// Side  
        "o":"TRAILING_STOP_MARKET",	// Order Type  
        "f":"GTC",					// Time in Force  
        "q":"2",				    // Original Quantity  
        "p":"0",					// Original Price  
        "ap":"0",					// Average Price  
        "sp":"9103.1",				// Stop Price. Please ignore with TRAILING_STOP_MARKET order  
        "x":"NEW",					// Execution Type  
        "X":"NEW",					// Order Status  
        "i":8888888,				// Order Id  
        "l":"0",					// Order Last Filled Quantity  
        "z":"0",					// Order Filled Accumulated Quantity  
        "L":"0",					// Last Filled Price  
        "ma": "BTC", 				// Margin Asset  
        "N":"BTC",            		// Commission Asset of the trade, will not push if no commission  
        "n":"0",               	    // Commission of the trade, will not push if no commission  
        "T":1591274595442,			// Order Trade Time  
        "t":0,			        	// Trade Id  
        "rp": "0",					// Realized Profit of the trade  
        "b":"0",			    	// Bid quantity of base asset  
        "a":"0",					// Ask quantity of base asset  
        "m":false,					// Is this trade the maker side?  
        "R":false,					// Is this reduce only  
        "wt":"CONTRACT_PRICE", 		// Stop Price Working Type  
        "ot":"TRAILING_STOP_MARKET",// Original Order Type  
        "ps":"LONG",				// Position Side  
        "cp":false,					// If Close-All, pushed with conditional order  
        "AP":"9476.8",				// Activation Price, only puhed with TRAILING_STOP_MARKET order  
        "cr":"5.0",					// Callback Rate, only puhed with TRAILING_STOP_MARKET order  
        "pP": false,				// If conditional order trigger is protected  
        "V":"EXPIRE_TAKER",       // STP mode  
        "pm":"OPPONENT"           // Price match mode  
      }  
    }  
    

  * [Event Description](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Order-Update#event-description>)
  * [Event Name](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Order-Update#event-name>)
  * [Response Example](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Order-Update#response-example>)

