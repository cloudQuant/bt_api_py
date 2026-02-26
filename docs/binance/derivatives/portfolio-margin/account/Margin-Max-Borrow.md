On this page

# Margin Max Borrow(USER_DATA)

## API Description

Query margin max borrow

## HTTP Request

GET `/papi/v1/margin/maxBorrowable`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
asset| STRING| YES|   
recvWindow| LONG| NO| The value cannot be greater than `60000`  
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
      "amount": 125 // account's currently max borrowable amount with sufficient system availability  
      "borrowLimit": 60 // max borrowable amount limited by the account level  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Margin-Max-Borrow#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Margin-Max-Borrow#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Margin-Max-Borrow#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Margin-Max-Borrow#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Margin-Max-Borrow#response-example>)

