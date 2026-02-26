On this page

# Event: User Data Stream Expired

## Event Description

When the `listenKey` used for the user data stream turns expired, this event will be pushed.

**Notice:**

  * This event is not related to the websocket disconnection.
  * This event will be received only when a valid `listenKey` in connection got expired.
  * No more user data event will be updated after this event received until a new valid `listenKey` used.

## Event Name

`listenKeyExpired`

## Response Example
    
    
    {  
        'e': 'listenKeyExpired',      // event type  
        'E': 1576653824250              // event time  
    }  
    

  * [Event Description](</docs/derivatives/portfolio-margin/user-data-streams/Event-User-Data-Stream-Expired#event-description>)
  * [Event Name](</docs/derivatives/portfolio-margin/user-data-streams/Event-User-Data-Stream-Expired#event-name>)
  * [Response Example](</docs/derivatives/portfolio-margin/user-data-streams/Event-User-Data-Stream-Expired#response-example>)

