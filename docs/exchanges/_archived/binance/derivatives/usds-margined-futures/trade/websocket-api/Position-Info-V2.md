On this page

# Position Information V2 (USER_DATA)

## API Description

Get current position information(only symbol that has position or open orders will be returned).

## Method

`v2/account.position`

## Request


    {
           "id": "605a6d20-6588-4cb9-afa0-b0ab087507ba",
        "method": "v2/account.position",
        "params": {
            "apiKey": "xTaDyrmvA9XT2oBHHjy39zyPzKCvMdtH3b9q4xadkAg2dNSJXQGCxzui26L823W2",
            "symbol": "BTCUSDT",
            "timestamp": 1702920680303,
            "signature": "31ab02a51a3989b66c29d40fcdf78216978a60afc6d8dc1c753ae49fa3164a2a"
        }
    }


## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

- *Note**

>   * Please use with user data stream `ACCOUNT_UPDATE` to meet your timeliness and accuracy needs.
>

## Response Example

> For One-way position mode:


    {
      "id": "605a6d20-6588-4cb9-afa0-b0ab087507ba",
      "status": 200,
      "result": [
        {
            "symbol": "BTCUSDT",
            "positionSide": "BOTH",            // 持仓方向
            "positionAmt": "1.000",
            "entryPrice": "0.00000",
            "breakEvenPrice": "0.0",
            "markPrice": "6679.50671178",
            "unrealizedProfit": "0.00000000",  // 持仓未实现盈亏
            "liquidationPrice": "0",
            "isolatedMargin": "0.00000000",
            "notional": "0",
            "marginAsset": "USDT",
            "isolatedWallet": "0",
            "initialMargin": "0",              // 初始保证金
            "maintMargin": "0",                // 维持保证金
            "positionInitialMargin": "0",      // 仓位初始保证金
            "openOrderInitialMargin": "0",     // 订单初始保证金
            "adl": 0,
            "bidNotional": "0",
            "askNotional": "0",
            "updateTime": 0                    // 更新时间
        }
    ],
      "rateLimits": [
        {
          "rateLimitType": "REQUEST_WEIGHT",
          "interval": "MINUTE",
          "intervalNum": 1,
          "limit": 2400,
          "count": 20
        }
      ]
    }


> For Hedge position mode:


    {
      "id": "605a6d20-6588-4cb9-afa0-b0ab087507ba",
      "status": 200,
      "result": [
       {
            "symbol": "BTCUSDT",
            "positionSide": "LONG",
            "positionAmt": "1.000",
            "entryPrice": "0.00000",
            "breakEvenPrice": "0.0",
            "markPrice": "6679.50671178",
            "unrealizedProfit": "0.00000000",
            "liquidationPrice": "0",
            "isolatedMargin": "0.00000000",
            "notional": "0",
            "marginAsset": "USDT",
            "isolatedWallet": "0",
            "initialMargin": "0",
            "maintMargin": "0",
            "positionInitialMargin": "0",
            "openOrderInitialMargin": "0",
            "adl": 0,
            "bidNotional": "0",
            "askNotional": "0",
            "updateTime": 0
        },
        {
            "symbol": "BTCUSDT",
            "positionSide": "SHORT",
            "positionAmt": "1.000",
            "entryPrice": "0.00000",
            "breakEvenPrice": "0.0",
            "markPrice": "6679.50671178",
            "unrealizedProfit": "0.00000000",
            "liquidationPrice": "0",
            "isolatedMargin": "0.00000000",
            "notional": "0",
            "marginAsset": "USDT",
            "isolatedWallet": "0",
            "initialMargin": "0",
            "maintMargin": "0",
            "positionInitialMargin": "0",
            "openOrderInitialMargin": "0",
            "adl": 0,
            "bidNotional": "0",
            "askNotional": "0",
            "updateTime": 0
        }
      ],
      "rateLimits": [
        {
          "rateLimitType": "REQUEST_WEIGHT",
          "interval": "MINUTE",
          "intervalNum": 1,
          "limit": 2400,
          "count": 20
        }
      ]
    }


  - [API Description](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#api-description>)
  - [Method](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#method>)
  - [Request](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/trade/websocket-api/Position-Info-V2#response-example>)
