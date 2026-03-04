On this page

# Event: Order Update

## Event Description

When new order created, order status changed will push such event. event type is `ORDER_TRADE_UPDATE`.

- *Side**

  - BUY
  - SELL

- *Order Type**

  - LIMIT
  - MARKET
  - STOP
  - STOP_MARKET
  - TAKE_PROFIT
  - TAKE_PROFIT_MARKET
  - TRAILING_STOP_MARKET
  - LIQUIDATION

- *Execution Type**

  - NEW
  - CANCELED
  - CALCULATED - Liquidation Execution
  - EXPIRED
  - TRADE
  - AMENDMENT - Order Modified

- *Order Status**

  - NEW
  - PARTIALLY_FILLED
  - FILLED
  - CANCELED
  - EXPIRED
  - EXPIRED_IN_MATCH

- *Time in force**

  - GTC
  - IOC
  - FOK
  - GTX

- *Working Type**

  - MARK_PRICE
  - CONTRACT_PRICE

- *Liquidation and ADL:**

  - If user gets liquidated due to insufficient margin balance:

    - `c` shows as "autoclose-XXX"，`X` shows as "NEW"
  - If user has enough margin balance but gets ADL:

    - `c` shows as “adl_autoclose”，`X` shows as “NEW”

## Event Name

`ORDER_TRADE_UPDATE`

## Response Example


    {
      "e":"ORDER_TRADE_UPDATE",           // Event Type
      "E":1568879465651,                   // Event Time
      "T":1568879465650,                   // Transaction Time
      "o":{
        "s":"BTCUSDT",                     // Symbol
        "c":"TEST",                           // Client Order Id
          // special client order id:
          // starts with "autoclose-": liquidation order
          // "adl_autoclose": ADL auto close order
          // "settlement_autoclose-": settlement order for delisting or delivery
        "S":"SELL",                             // Side
        "o":"TRAILING_STOP_MARKET",     // Order Type
        "f":"GTC",                             // Time in Force
        "q":"0.001",                         // Original Quantity
        "p":"0",                               // Original Price
        "ap":"0",                               // Average Price
        "sp":"7103.04",                       // Stop Price. Please ignore with TRAILING_STOP_MARKET order
        "x":"NEW",                             // Execution Type
        "X":"NEW",                             // Order Status
        "i":8886774,                         // Order Id
        "l":"0",                               // Order Last Filled Quantity
        "z":"0",                               // Order Filled Accumulated Quantity
        "L":"0",                               // Last Filled Price
        "N":"USDT",                     // Commission Asset, will not push if no commission
        "n":"0",                        // Commission, will not push if no commission
        "T":1568879465650,                 // Order Trade Time
        "t":0,                               // Trade Id
        "b":"0",                             // Bids Notional
        "a":"9.91",                             // Ask Notional
        "m":false,                             // Is this trade the maker side?
        "R":false,                             // Is this reduce only
        "wt":"CONTRACT_PRICE",            // Stop Price Working Type
        "ot":"TRAILING_STOP_MARKET", // Original Order Type
        "ps":"LONG",                           // Position Side
        "cp":false,                               // If Close-All, pushed with conditional order
        "AP":"7476.89",                       // Activation Price, only puhed with TRAILING_STOP_MARKET order
        "cr":"5.0",                             // Callback Rate, only puhed with TRAILING_STOP_MARKET order
        "pP": false,                 // If price protection is turned on
        "si": 0,                     // ignore
        "ss": 0,                     // ignore
        "rp":"0",                                  // Realized Profit of the trade
        "V":"EXPIRE_TAKER",          // STP mode
        "pm":"OPPONENT",             // Price match mode
        "gtd":0                      // TIF GTD order auto cancel time
      }
    }


  - [Event Description](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update#event-description>)
  - [Event Name](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update#event-name>)
  - [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update#response-example>)
