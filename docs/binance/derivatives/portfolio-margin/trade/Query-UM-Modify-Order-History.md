On this page

# Query UM Modify Order History(TRADE)

## API Description

Get order modification history

## HTTP Request

GET `/papi/v1/um/orderAmendment`

## Request Weight(Order)")

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
orderId| LONG| NO|   
origClientOrderId| STRING| NO|   
startTime| LONG| NO| Timestamp in ms to get modification history from INCLUSIVE  
endTime| LONG| NO| Timestamp in ms to get modification history until INCLUSIVE  
limit| INT| NO| Default 500, max 1000  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Either `orderId` or `origClientOrderId` must be sent, and the `orderId` will prevail if both are sent.
> 

## Response Example
    
    
    [  
        {  
            "amendmentId": 5363,    // Order modification ID  
            "symbol": "BTCUSDT",  
            "pair": "BTCUSDT",  
            "orderId": 20072994037,  
            "clientOrderId": "LJ9R4QZDihCaS8UAOOLpgW",  
            "time": 1629184560899,  // Order modification time  
            "amendment": {  
                "price": {  
                    "before": "30004",  
                    "after": "30003.2"  
                },  
                "origQty": {  
                    "before": "1",  
                    "after": "1"  
                },  
                "count": 3  // Order modification count, representing the number of times the order has been modified  
            },  
            "priceMatch": "NONE"  
        },  
        {  
            "amendmentId": 5361,  
            "symbol": "BTCUSDT",  
            "pair": "BTCUSDT",  
            "orderId": 20072994037,  
            "clientOrderId": "LJ9R4QZDihCaS8UAOOLpgW",  
            "time": 1629184533946,  
            "amendment": {  
                "price": {  
                    "before": "30005",  
                    "after": "30004"  
                },  
                "origQty": {  
                    "before": "1",  
                    "after": "1"  
                },  
                "count": 2  
            },  
            "priceMatch": "NONE"  
        },  
        {  
            "amendmentId": 5325,  
            "symbol": "BTCUSDT",  
            "pair": "BTCUSDT",  
            "orderId": 20072994037,  
            "clientOrderId": "LJ9R4QZDihCaS8UAOOLpgW",  
            "time": 1629182711787,  
            "amendment": {  
                "price": {  
                    "before": "30002",  
                    "after": "30005"  
                },  
                "origQty": {  
                    "before": "1",  
                    "after": "1"  
                },  
                "count": 1  
            },  
            "priceMatch": "NONE"  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-UM-Modify-Order-History#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-UM-Modify-Order-History#http-request>)
  * [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Query-UM-Modify-Order-History#request-weightorder>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Query-UM-Modify-Order-History#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Query-UM-Modify-Order-History#response-example>)

