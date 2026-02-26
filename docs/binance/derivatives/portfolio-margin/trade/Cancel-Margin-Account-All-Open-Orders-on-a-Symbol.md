On this page

# Cancel Margin Account All Open Orders on a Symbol(TRADE)

## API Description

Cancel Margin Account All Open Orders on a Symbol

## HTTP Request

DELETE `/papi/v1/margin/allOpenOrders`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
## Response Example
    
    
    [  
      {  
        "symbol": "BTCUSDT",  
        "origClientOrderId": "E6APeyTJvkMvLMYMqu1KQ4",  
        "orderId": 11,  
        "orderListId": -1,  
        "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",  
        "price": "0.089853",  
        "origQty": "0.178622",  
        "executedQty": "0.000000",  
        "cummulativeQuoteQty": "0.000000",  
        "status": "CANCELED",  
        "timeInForce": "GTC",  
        "type": "LIMIT",  
        "side": "BUY"  
      },  
      {  
        "orderListId": 1929,  
        "contingencyType": "OCO",  
        "listStatusType": "ALL_DONE",  
        "listOrderStatus": "ALL_DONE",  
        "listClientOrderId": "2inzWQdDvZLHbbAmAozX2N",  
        "transactionTime": 1585230948299,  
        "symbol": "BTCUSDT",  
        "orders": [  
          {  
            "symbol": "BTCUSDT",  
            "orderId": 20,  
            "clientOrderId": "CwOOIPHSmYywx6jZX77TdL"  
          },  
          {  
            "symbol": "BTCUSDT",  
            "orderId": 21,  
            "clientOrderId": "461cPg51vQjV3zIMOXNz39"  
          }  
        ],  
        "orderReports": [  
          {  
            "symbol": "BTCUSDT",  
            "origClientOrderId": "CwOOIPHSmYywx6jZX77TdL",  
            "orderId": 20,  
            "orderListId": 1929,  
            "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",  
            "price": "0.668611",  
            "origQty": "0.690354",  
            "executedQty": "0.000000",  
            "cummulativeQuoteQty": "0.000000",  
            "status": "CANCELED",  
            "timeInForce": "GTC",  
            "type": "STOP_LOSS_LIMIT",  
            "side": "BUY",  
            "stopPrice": "0.378131",  
            "icebergQty": "0.017083"  
          },  
          {  
            "symbol": "BTCUSDT",  
            "origClientOrderId": "461cPg51vQjV3zIMOXNz39",  
            "orderId": 21,  
            "orderListId": 1929,  
            "clientOrderId": "pXLV6Hz6mprAcVYpVMTGgx",  
            "price": "0.008791",  
            "origQty": "0.690354",  
            "executedQty": "0.000000",  
            "cummulativeQuoteQty": "0.000000",  
            "status": "CANCELED",  
            "timeInForce": "GTC",  
            "type": "LIMIT_MAKER",  
            "side": "BUY",  
            "icebergQty": "0.639962"  
          }  
        ]  
      }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Cancel-Margin-Account-All-Open-Orders-on-a-Symbol#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Cancel-Margin-Account-All-Open-Orders-on-a-Symbol#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/Cancel-Margin-Account-All-Open-Orders-on-a-Symbol#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Cancel-Margin-Account-All-Open-Orders-on-a-Symbol#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Cancel-Margin-Account-All-Open-Orders-on-a-Symbol#response-example>)

