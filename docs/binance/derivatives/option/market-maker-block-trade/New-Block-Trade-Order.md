On this page

# New Block Trade Order (TRADE)

## API Description

Send in a new block trade order.

## HTTP Request

POST `eapi/v1/block/order/create`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

liquidity| STRING| YES| Taker or Maker

legs| LIST| YES| Max 1 (only single leg supported), list of legs parameters in JSON; example: eapi/v1/block/order/create?orders=[{"symbol":"BTC-210115-35000-C", "price":"100","quantity":"0.0002","side":"BUY","type":"LIMIT"}]

symbol| STRING| YES| Option trading pair, e.g BTC-200730-9000-C

side| ENUM| YES| BUY or SELL

price| DECIMAL| YES| Order Price

quantity| DECIMAL| YES| Order Quantity

recvWindow| INT| NO| The value cannot be greater than 60000

timestamp| INT| YES|

## Response Example


    {
        "blockTradeSettlementKey": "3668822b8-1baa-6a2f-adb8-d3de6289b361",
        "expireTime": 1730171888109,
        "liquidity": "TAKER",
        "status": "RECEIVED",
        "legs": [
            {
                "symbol": "BNB-241101-700-C",
                "side": "BUY",
                "quantity": "1.2",
                "price": "2.8"
            }
        ]
    }


  - [API Description](</docs/derivatives/option/market-maker-block-trade#api-description>)
  - [HTTP Request](</docs/derivatives/option/market-maker-block-trade#http-request>)
  - [Request Weight](</docs/derivatives/option/market-maker-block-trade#request-weight>)
  - [Request Parameters](</docs/derivatives/option/market-maker-block-trade#request-parameters>)
  - [Response Example](</docs/derivatives/option/market-maker-block-trade#response-example>)
