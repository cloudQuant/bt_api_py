On this page

# Event: Account Configuration Update (Leverage Update)

## Event Description

When the account configuration is changed, the event type will be pushed as `ACCOUNT_CONFIG_UPDATE` When the leverage of a trade pair changes, the payload will contain the object `ac` to represent the account configuration of the trade pair, where `s` represents the specific trade pair and `l` represents the leverage

## Event Name

`ACCOUNT_CONFIG_UPDATE`

## Response Example
    
    
    {  
        "e":"ACCOUNT_CONFIG_UPDATE",       // Event Type  
        "E":1611646737479,		           // Event Time  
        "T":1611646737476,		           // Transaction Time  
        "ac":{								  
        "s":"BTCUSD_PERP",				   // symbol  
        "l":25						       // leverage  
        }  
    }  
    

  * [Event Description](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Account-Configuration-Update#event-description>)
  * [Event Name](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Account-Configuration-Update#event-name>)
  * [Response Example](</docs/derivatives/coin-margined-futures/user-data-streams/Event-Account-Configuration-Update#response-example>)

