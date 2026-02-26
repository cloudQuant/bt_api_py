On this page

# New Margin Order(TRADE)

## API Description

New Margin Order

## HTTP Request

POST `/papi/v1/margin/order`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
side| ENUM| YES| BUY ; SELL  
type| ENUM| YES|   
quantity| DECIMAL| NO|   
quoteOrderQty| DECIMAL| NO|   
price| DECIMAL| NO|   
stopPrice| DECIMAL| NO| Used with `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, and `TAKE_PROFIT_LIMIT` orders.  
newClientOrderId| STRING| NO| A unique id among open orders. Automatically generated if not sent.  
newOrderRespType| ENUM| NO| Set the response JSON. ACK, RESULT, or FULL; MARKET and LIMIT order types default to FULL, all other orders default to ACK.  
icebergQty| DECIMAL| NO| Used with `LIMIT`, `STOP_LOSS_LIMIT`, and `TAKE_PROFIT_LIMIT` to create an iceberg order  
sideEffectType| ENUM| NO| `NO_SIDE_EFFECT`, `MARGIN_BUY`, `AUTO_REPAY`,`AUTO_BORROW_REPAY`; default `NO_SIDE_EFFECT`.  
timeInForce| ENUM| NO| GTC,IOC,FOK  
selfTradePreventionMode| ENUM| NO| `NONE`:No STP / `EXPIRE_TAKER`:expire taker order when STP triggers/ `EXPIRE_MAKER`:expire taker order when STP triggers/ `EXPIRE_BOTH`:expire both orders when STP triggers  
autoRepayAtCancel| BOOLEAN| NO| 只有在自动借款单或者自动借还单生效，true表示的是撤单后需要把订单产生的借款归还，默认为true  
recvWindow| LONG| NO| The value cannot be greater than `60000`  
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
      "symbol": "BTCUSDT",  
      "orderId": 28,  
      "clientOrderId": "6gCrw2kRUAF9CvJDGP16IP",  
      "transactTime": 1507725176595,  
      "price": "1.00000000",  
      "origQty": "10.00000000",  
      "executedQty": "10.00000000",  
      "cummulativeQuoteQty": "10.00000000",  
      "status": "FILLED",  
      "timeInForce": "GTC",  
      "type": "MARKET",  
      "side": "SELL",  
      "marginBuyBorrowAmount": 5,       // will not return if no margin trade happens  
      "marginBuyBorrowAsset": "BTC",    // will not return if no margin trade happens  
      "fills": [  
        {  
          "price": "4000.00000000",  
          "qty": "1.00000000",  
          "commission": "4.00000000",  
          "commissionAsset": "USDT"  
        },  
        {  
          "price": "3999.00000000",  
          "qty": "5.00000000",  
          "commission": "19.99500000",  
          "commissionAsset": "USDT"  
        }  
      ]  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/New-Margin-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/New-Margin-Order#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/New-Margin-Order#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/New-Margin-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/New-Margin-Order#response-example>)

