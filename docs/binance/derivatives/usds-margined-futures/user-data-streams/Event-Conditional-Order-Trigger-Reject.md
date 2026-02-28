On this page

# Event: Conditional_Order_Trigger_Reject

## Event Description

`CONDITIONAL_ORDER_TRIGGER_REJECT` update when a triggered TP/SL order got rejected.

## Event Name

`CONDITIONAL_ORDER_TRIGGER_REJECT`

## Response Example


    {
        "e":"CONDITIONAL_ORDER_TRIGGER_REJECT",      // Event Type
        "E":1685517224945,      // Event Time
        "T":1685517224955,      // me message send Time
        "or":{
          "s":"ETHUSDT",      // Symbol
          "i":155618472834,      // orderId
          "r":"Due to the order could not be filled immediately, the FOK order has been rejected. The order will not be recorded in the order history",      // reject reason
         }
    }


  - [Event Description](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Conditional-Order-Trigger-Reject#event-description>)
  - [Event Name](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Conditional-Order-Trigger-Reject#event-name>)
  - [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Conditional-Order-Trigger-Reject#response-example>)
