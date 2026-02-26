On this page

# Query Margin Max Withdraw(USER_DATA)

## API Description

Query Margin Max Withdraw

## HTTP Request

GET `/papi/v1/margin/maxWithdraw`

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
      "amount": "60"  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Query-Margin-Max-Withdraw#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Query-Margin-Max-Withdraw#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Query-Margin-Max-Withdraw#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Query-Margin-Max-Withdraw#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Query-Margin-Max-Withdraw#response-example>)

