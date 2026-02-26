On this page

# Query Margin Account's Open OCO (USER_DATA)

## API Description

Query Margin Account's Open OCO

## HTTP Request

GET `/papi/v1/margin/openOrderList`

## Weight

**5**

## Parameters:

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
## Response:
    
    
    [  
      {  
        "orderListId": 31,  
        "contingencyType": "OCO",  
        "listStatusType": "EXEC_STARTED",  
        "listOrderStatus": "EXECUTING",  
        "listClientOrderId": "wuB13fmulKj3YjdqWEcsnp",  
        "transactionTime": 1565246080644,  
        "symbol": "LTCBTC",  
        "orders": [  
          {  
            "symbol": "LTCBTC",  
            "orderId": 4,  
            "clientOrderId": "r3EH2N76dHfLoSZWIUw1bT"  
          },  
          {  
            "symbol": "LTCBTC",  
            "orderId": 5,  
            "clientOrderId": "Cv1SnyPD3qhqpbjpYEHbd2"  
          }  
        ]  
      }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Open-OCO#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Open-OCO#http-request>)
  * [Weight](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Open-OCO#weight>)
  * [Parameters:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Open-OCO#parameters>)
  * [Response:](</docs/derivatives/portfolio-margin/trade/Query-Margin-Account-Open-OCO#response>)

