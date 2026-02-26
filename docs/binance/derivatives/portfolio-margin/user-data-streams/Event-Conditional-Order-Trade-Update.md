On this page

# Event: Conditional Order Trade Update

## Event Description

When new order created, order status changed will push such event. event type is `CONDITIONAL_ORDER_TRADE_UPDATE`.

**Side**

  * BUY
  * SELL

**Conditional Order Type**

  * STOP
  * TAKE_PROFIT
  * STOP_MARKET
  * TAKE_PROFIT_MARKET
  * TRAILING_STOP_MARKET

**Execution Type**

  * NEW
  * CANCELED
  * CALCULATED - Liquidation Execution
  * EXPIRED
  * TRADE

**Order Status**

  * NEW
  * CANCELED
  * EXPIRED
  * TRIGGERED
  * FINISHED

**Time in force**

  * GTC
  * IOC
  * FOK
  * GTX

## Event Name

`CONDITIONAL_ORDER_TRADE_UPDATE`

## Response Example
    
    
    {  
        "e": "CONDITIONAL_ORDER_TRADE_UPDATE", // Event Type  
        "T": 1669262908216,                    // Transaction Time  
        "E": 1669262908218,                    // Event Time  
        "fs": "UM",                            // Event business unit  
        "so": {               
                "s": "BTCUSDT",                // Symbol  
                "c":"TEST",                    // Strategy Client Order Id  
                "si": 176057039,               // Strategy ID  
                "S":"SELL",                    // Side  
                "st": "TRAILING_STOP_MARKET",  // Strategy Type  
                "f":"GTC",                     // Time in Force  
                "q":"0.001",                   //Quantity  
                "p":"0",                       //Price  
                "sp":"7103.04",                // Stop Price. Please ignore with TRAILING_STOP_MARKET order  
                "os":"NEW",                    // Strategy Order Status  
                "T":1568879465650,             // Order book Time  
                "ut": 1669262908216,           // Order update Time   
                "R":false,                     // Is this reduce only  
                "wt":"MARK_PRICE",             // Stop Price Working Type  
                "ps":"LONG",                   // Position Side  
                "cp":false,                    // If Close-All, pushed with conditional order  
                "AP":"7476.89",                // Activation Price, only pushed with TRAILING_STOP_MARKET order  
                "cr":"5.0",                    // Callback Rate, only puhed with TRAILING_STOP_MARKET order  
                "i":8886774,                   // Order Id  
                "V":"EXPIRE_TAKER",         // STP mode  
                "gtd":0  
            }  
    }  
    

  * [Event Description](</docs/derivatives/portfolio-margin/user-data-streams/Event-Conditional-Order-Trade-Update#event-description>)
  * [Event Name](</docs/derivatives/portfolio-margin/user-data-streams/Event-Conditional-Order-Trade-Update#event-name>)
  * [Response Example](</docs/derivatives/portfolio-margin/user-data-streams/Event-Conditional-Order-Trade-Update#response-example>)

