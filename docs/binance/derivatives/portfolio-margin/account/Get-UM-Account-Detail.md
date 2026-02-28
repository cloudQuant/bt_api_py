On this page

# Get UM Account Detail(USER_DATA)

## API Description

Get current UM account asset and position information.

## HTTP Request

GET `/papi/v1/um/account`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "assets": [
            {
                "asset": "USDT",            // asset name
                "crossWalletBalance": "23.72469206",      // wallet balance
                "crossUnPnl": "0.00000000",    // unrealized profit
                "maintMargin": "0.00000000",        // maintenance margin required
                "initialMargin": "0.00000000",    // total initial margin required with current mark price
                "positionInitialMargin": "0.00000000",    //initial margin required for positions with current mark price
                "openOrderInitialMargin": "0.00000000",   // initial margin required for open orders with current mark price
                "updateTime": 1625474304765 // last update time
            }
        ],
        "positions": [// positions of all symbols in the market are returned
            // only "BOTH" positions will be returned with One-way mode
            // only "LONG" and "SHORT" positions will be returned with Hedge mode
            {
                "symbol": "BTCUSDT",    // symbol name
                "initialMargin": "0",   // initial margin required with current mark price
                "maintMargin": "0",     // maintenance margin required
                "unrealizedProfit": "0.00000000",  // unrealized profit
                "positionInitialMargin": "0",      // initial margin required for positions with current mark price
                "openOrderInitialMargin": "0",     // initial margin required for open orders with current mark price
                "leverage": "100",      // current initial leverage
                "entryPrice": "0.00000",    // average entry price
                "maxNotional": "250000",    // maximum available notional with current leverage
                "bidNotional": "0",  // bids notional, ignore
                "askNotional": "0",  // ask notional, ignore
                "positionSide": "BOTH",     // position side
                "positionAmt": "0",         // position amount
                "updateTime": 0           // last update time
            }
       ]
    }


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-UM-Account-Detail#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-UM-Account-Detail#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-UM-Account-Detail#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-UM-Account-Detail#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-UM-Account-Detail#response-example>)
