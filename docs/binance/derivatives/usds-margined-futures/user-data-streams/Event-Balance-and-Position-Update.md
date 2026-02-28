On this page

# Event: Balance and Position Update

## Event Description

Event type is `ACCOUNT_UPDATE`.

  - When balance or position get updated, this event will be pushed.

    - `ACCOUNT_UPDATE` will be pushed only when update happens on user's account, including changes on balances, positions, or margin type.
    - Unfilled orders or cancelled orders will not make the event `ACCOUNT_UPDATE` pushed, since there's no change on positions.
    - "position" in `ACCOUNT_UPDATE`: Only symbols of changed positions will be pushed.
  - When "FUNDING FEE" changes to the user's balance, the event will be pushed with the brief message:

    - When "FUNDING FEE" occurs in a **crossed position**, `ACCOUNT_UPDATE` will be pushed with only the balance `B`(including the "FUNDING FEE" asset only), without any position `P` message.
    - When "FUNDING FEE" occurs in an**isolated position** , `ACCOUNT_UPDATE` will be pushed with only the balance `B`(including the "FUNDING FEE" asset only) and the relative position message `P`( including the isolated position on which the "FUNDING FEE" occurs only, without any other position message).
  - The field "m" represents the reason type for the event and may shows the following possible types:

    - DEPOSIT
    - WITHDRAW
    - ORDER
    - FUNDING_FEE
    - WITHDRAW_REJECT
    - ADJUSTMENT
    - INSURANCE_CLEAR
    - ADMIN_DEPOSIT
    - ADMIN_WITHDRAW
    - MARGIN_TRANSFER
    - MARGIN_TYPE_CHANGE
    - ASSET_TRANSFER
    - OPTIONS_PREMIUM_FEE
    - OPTIONS_SETTLE_PROFIT
    - AUTO_EXCHANGE
    - COIN_SWAP_DEPOSIT
    - COIN_SWAP_WITHDRAW
  - The field "bc" represents the balance change except for PnL and commission.

## Event Name

`ACCOUNT_UPDATE`

## Response Example


    {
      "e": "ACCOUNT_UPDATE",                // Event Type
      "E": 1564745798939,                    // Event Time
      "T": 1564745798938 ,                   // Transaction
      "a":                                  // Update Data
        {
          "m":"ORDER",                        // Event reason type
          "B":[// Balances
            {
              "a":"USDT",                   // Asset
              "wb":"122624.12345678",        // Wallet Balance
              "cw":"100.12345678",            // Cross Wallet Balance
              "bc":"50.12345678"            // Balance Change except PnL and Commission
            },
            {
              "a":"BUSD",
              "wb":"1.00000000",
              "cw":"0.00000000",
              "bc":"-49.12345678"
            }
        ],
          "P":[
            {
              "s":"BTCUSDT",              // Symbol
              "pa":"0",                   // Position Amount
              "ep":"0.00000",            // Entry Price
              "bep":"0",                // breakeven price
              "cr":"200",                 // (Pre-fee) Accumulated Realized
              "up":"0",                        // Unrealized PnL
              "mt":"isolated",                // Margin Type
              "iw":"0.00000000",            // Isolated Wallet (if isolated position)
              "ps":"BOTH"                    // Position Side
            }，
            {
                "s":"BTCUSDT",
                "pa":"20",
                "ep":"6563.66500",
                "bep":"0",                // breakeven price
                "cr":"0",
                "up":"2850.21200",
                "mt":"isolated",
                "iw":"13200.70726908",
                "ps":"LONG"
               },
            {
                "s":"BTCUSDT",
                "pa":"-10",
                "ep":"6563.86000",
                "bep":"6563.6",          // breakeven price
                "cr":"-45.04000000",
                "up":"-1423.15600",
                "mt":"isolated",
                "iw":"6570.42511771",
                "ps":"SHORT"
            }
          ]
        }
    }


  - [Event Description](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update#event-description>)
  - [Event Name](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update#event-name>)
  - [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update#response-example>)
