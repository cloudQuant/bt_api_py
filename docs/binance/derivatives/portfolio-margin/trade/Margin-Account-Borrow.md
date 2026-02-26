On this page

# Margin Account Borrow(MARGIN)

## API Description

Apply for a margin loan.

## HTTP Request

POST `/papi/v1/marginLoan`

## Request Weight(IP)")

**100**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
asset| STRING| YES|   
amount| DECIMAL| YES|   
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
        //transaction id  
        "tranId": 100000001  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Margin-Account-Borrow#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Margin-Account-Borrow#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin/trade/Margin-Account-Borrow#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/Margin-Account-Borrow#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/Margin-Account-Borrow#response-example>)

