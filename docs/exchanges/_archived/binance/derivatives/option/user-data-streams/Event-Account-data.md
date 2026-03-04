On this page

# Event: Account data

## Event Description

  - Update under the following conditions:
    - Account deposit or withdrawal
    - Position info change. Includes a P attribute if there are changes, otherwise does not include a P attribute.
    - Greek update

## Event Name

`ACCOUNT_UPDATE`

## Update Speed

- *50ms**

## Response Example


    {
        "e":"ACCOUNT_UPDATE",                 // Event type
        "E":1591696384141,                    // Event time
        "B":[
            {
              "b":"100007992.26053177",       // Account balance
              "m":"0",                        // Position value
              "u":"458.782655111111",         // Unrealized profit/loss
              "U":200,                        // Positive unrealized profit for long position
              "M":"-15452.328456",            // Maintenance margin
              "i":"-18852.328456",            // Initial margin
              "a":"USDT",                     // Margin asset
            }
        ],
        "G":[
            {
             "ui":"SOLUSDT",                  // Underlying
             "d":-33.2933905,                 // Delta
             "t":35.5926375,                  // Theta
             "g":-14.3023855,                 // Gamma
             "v":-0.1929375                   // Vega
            }
        ],
        "P":[
          {
           "s":"SOL-220912-35-C",             // Contract symbol
           "c":"-50",                         // Number of current positions
           "r":"-50",                         // Number of positions that can be reduced
           "p":"-100",                        // Position value
           "a":"2.2",                         // Average entry price
          }
        ],
        "uid":1000006559949
    }


  - [Event Description](</docs/derivatives/option/user-data-streams/Event-Account-data#event-description>)
  - [Event Name](</docs/derivatives/option/user-data-streams/Event-Account-data#event-name>)
  - [Update Speed](</docs/derivatives/option/user-data-streams/Event-Account-data#update-speed>)
  - [Response Example](</docs/derivatives/option/user-data-streams/Event-Account-data#response-example>)
