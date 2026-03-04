On this page

# Margin Account New OCO(TRADE)

## API Description

Send in a new OCO for a margin account

## HTTP Request

POST `/papi/v1/margin/order/oco`

## Request Weight(Order)")

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

listClientOrderId| STRING| NO| A unique Id for the entire orderList

side| ENUM| YES|

quantity| DECIMAL| YES|

limitClientOrderId| STRING| NO| A unique Id for the limit order

price| DECIMAL| YES|

limitIcebergQty| DECIMAL| NO|

stopClientOrderId| STRING| NO| A unique Id for the stop loss/stop loss limit leg

stopPrice| DECIMAL| YES|

stopLimitPrice| DECIMAL| NO| If provided, stopLimitTimeInForce is required.

stopIcebergQty| DECIMAL| NO|

stopLimitTimeInForce| ENUM| NO| Valid values are `GTC/FOK/IOC`

newOrderRespType| ENUM| NO| Set the response JSON.

sideEffectType| ENUM| NO| NO_SIDE_EFFECT, MARGIN_BUY, AUTO_REPAY; default NO_SIDE_EFFECT.

recvWindow| LONG| NO| The value cannot be greater than `60000`

timestamp| LONG| YES|

Other Info:

>   *Price Restrictions:
>    *`SELL`: Limit Price > Last Price > Stop Price
>    *`BUY`: Limit Price < Last Price < Stop Price
>  *Quantity Restrictions:
>    *Both legs must have the same quantity
>    *`ICEBERG` quantities however do not have to be the same.
>  *Order Rate Limit
>    * `OCO` counts as 2 orders against the order rate limit.
>

## Response Example


    {
      "orderListId": 0,
      "contingencyType": "OCO",
      "listStatusType": "EXEC_STARTED",
      "listOrderStatus": "EXECUTING",
      "listClientOrderId": "JYVpp3F0f5CAG15DhtrqLp",
      "transactionTime": 1563417480525,
      "symbol": "LTCBTC",
      "marginBuyBorrowAmount": "5",       // will not return if no margin trade happens
      "marginBuyBorrowAsset": "BTC",    // will not return if no margin trade happens
      "orders": [
        {
          "symbol": "LTCBTC",
          "orderId": 2,
          "clientOrderId": "Kk7sqHb9J6mJWTMDVW7Vos"
        },
        {
          "symbol": "LTCBTC",
          "orderId": 3,
          "clientOrderId": "xTXKaGYd4bluPVp78IVRvl"
        }
      ],
      "orderReports": [
        {
          "symbol": "LTCBTC",
          "orderId": 2,
          "orderListId": 0,
          "clientOrderId": "Kk7sqHb9J6mJWTMDVW7Vos",
          "transactTime": 1563417480525,
          "price": "0.000000",
          "origQty": "0.624363",
          "executedQty": "0.000000",
          "cummulativeQuoteQty": "0.000000",
          "status": "NEW",
          "timeInForce": "GTC",
          "type": "STOP_LOSS",
          "side": "BUY",
          "stopPrice": "0.960664"
        },
        {
          "symbol": "LTCBTC",
          "orderId": 3,
          "orderListId": 0,
          "clientOrderId": "xTXKaGYd4bluPVp78IVRvl",
          "transactTime": 1563417480525,
          "price": "0.036435",
          "origQty": "0.624363",
          "executedQty": "0.000000",
          "cummulativeQuoteQty": "0.000000",
          "status": "NEW",
          "timeInForce": "GTC",
          "type": "LIMIT_MAKER",
          "side": "BUY"
        }
      ]
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Margin-Account-New-OCO#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Margin-Account-New-OCO#http-request>)
  - [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Margin-Account-New-OCO#request-weightorder>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Margin-Account-New-OCO#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Margin-Account-New-OCO#response-example>)
