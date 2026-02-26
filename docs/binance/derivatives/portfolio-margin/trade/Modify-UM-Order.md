On this page

# Modify UM Order(TRADE)

## API Description

Order modify function, currently only LIMIT order modification is supported, modified orders will be reordered in the match queue

## HTTP Request

PUT `/papi/v1/um/order`

## Request Weight(Order)")

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
orderId| LONG| NO|   
origClientOrderId| STRING| NO|   
symbol| STRING| YES|   
side| ENUM| YES| SELL, BUY  
quantity| DECIMAL| YES| Order quantity  
price| DECIMAL| YES|   
priceMatch| ENUM| NO| only avaliable for `LIMIT`/`STOP`/`TAKE_PROFIT` order; can be set to `OPPONENT`/ `OPPONENT_5`/ `OPPONENT_10`/ `OPPONENT_20`: /`QUEUE`/ `QUEUE_5`/ `QUEUE_10`/ `QUEUE_20`; Can't be passed together with `price`  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Either orderId or origClientOrderId must be sent, and the orderId will prevail if both are sent.
>   * Both quantity and price must be sent
>   * When the new quantity or price doesn't satisfy PRICE_FILTER / PERCENT_FILTER / LOT_SIZE, amendment will be rejected and the order will stay as it is.
>   * However the order will be cancelled by the amendment in the following situations: 
>     * when the order is in partially filled status and the new quantity <= executedQty
>     * When the order is GTX and the new price will cause it to be executed immediately
> 

## Response Example
    
    
    {  
        "orderId": 20072994037,  
        "symbol": "BTCUSDT",  
        "status": "NEW",  
        "clientOrderId": "LJ9R4QZDihCaS8UAOOLpgW",  
        "price": "30005",  
        "avgPrice": "0.0",  
        "origQty": "1",  
        "executedQty": "0",  
        "cumQty": "0",  
        "cumQuote": "0",  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "LONG",  
        "origType": "LIMIT",  
        "selfTradePreventionMode": "NONE", //self trading preventation mode  
        "goodTillDate": 0      //order pre-set auot cancel time for TIF GTD order  
        "updateTime": 1629182711600,  
        "priceMatch": "NONE"  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Modify-UM-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Modify-UM-Order#http-request>)
  * [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Modify-UM-Order#request-weightorder>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Modify-UM-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Modify-UM-Order#response-example>)

