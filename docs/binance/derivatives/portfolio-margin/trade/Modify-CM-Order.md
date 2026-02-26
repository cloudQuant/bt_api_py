On this page

# Modify CM Order(TRADE)

## API Description

Order modify function, currently only LIMIT order modification is supported, modified orders will be reordered in the match queue

## HTTP Request

PUT `/papi/v1/cm/order`

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
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Either `orderId` or `origClientOrderId` must be sent, and the `orderId` will prevail if both are sent.
>   * Both `quantity` and `price` must be sent
>   * When the new `quantity` or `price` doesn't satisfy PRICE_FILTER / PERCENT_FILTER / LOT_SIZE, amendment will be rejected and the order will stay as it is.
>   * However the order will be cancelled by the amendment in the following situations: 
>     * when the order is in partially filled status and the new `quantity` <= `executedQty`
>     * When the order is `GTX` and the new price will cause it to be executed immediately
> 

## Response Example
    
    
    {  
        "orderId": 20072994037,  
        "symbol": "BTCUSD_PERP",  
        "pair": "BTCUSD",  
        "status": "NEW",  
        "clientOrderId": "LJ9R4QZDihCaS8UAOOLpgW",  
        "price": "30005",  
        "avgPrice": "0.0",  
        "origQty": "1",  
        "executedQty": "0",  
        "cumQty": "0",  
        "cumBase": "0",  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "reduceOnly": false,  
        "side": "BUY",  
        "positionSide": "LONG",  
        "origType": "LIMIT",  
        "updateTime": 1629182711600  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Modify-CM-Order#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Modify-CM-Order#http-request>)
  * [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Modify-CM-Order#request-weightorder>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Modify-CM-Order#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Modify-CM-Order#response-example>)

