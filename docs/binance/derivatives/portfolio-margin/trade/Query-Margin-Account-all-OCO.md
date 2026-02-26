On this page

# Query Margin Account's all OCO (USER_DATA)

## API Description

Query all OCO for a specific margin account based on provided optional parameters

## HTTP Request

GET `/papi/v1/margin/allOrderList`

## Weight

**100**

## Parameters:

Name| Type| Mandatory| Description  
---|---|---|---  
fromId| LONG| NO| If supplied, neither startTime or endTime can be provided  
startTime| LONG| NO|   
endTime| LONG| NO|   
limit| INT| NO| Default 500; max 500.  
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
## Response:
    
    
    [  
      {  
        "orderListId": 29,  
        "contingencyType": "OCO",  
        "listStatusType": "EXEC_STARTED",  
        "listOrderStatus": "EXECUTING",  
        "listClientOrderId": "amEEAXryFzFwYF1FeRpUoZ",  
        "transactionTime": 1565245913483,  
        "symbol": "LTCBTC",  
        "orders": [  
          {  
            "symbol": "LTCBTC",  
            "orderId": 4,  
            "clientOrderId": "oD7aesZqjEGlZrbtRpy5zB"  
          },  
          {  
            "symbol": "LTCBTC",  
            "orderId": 5,  
            "clientOrderId": "Jr1h6xirOxgeJOUuYQS7V3"  
          }  
        ]  
      },  
      {  
        "orderListId": 28,  
        "contingencyType": "OCO",  
        "listStatusType": "EXEC_STARTED",  
        "listOrderStatus": "EXECUTING",  
        "listClientOrderId": "hG7hFNxJV6cZy3Ze4AUT4d",  
        "transactionTime": 1565245913407,  
        "symbol": "LTCBTC",  
        "orders": [  
          {  
            "symbol": "LTCBTC",  
            "orderId": 2,  
            "clientOrderId": "j6lFOfbmFMRjTYA7rRJ0LP"  
          },  
          {  
            "symbol": "LTCBTC",  
            "orderId": 3,  
            "clientOrderId": "z0KCjOdditiLS5ekAFtK81"  
          }  
        ]  
      }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-all-OCO#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-all-OCO#http-request>)
  * [Weight](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-all-OCO#weight>)
  * [Parameters:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-all-OCO#parameters>)
  * [Response:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-all-OCO#response>)

