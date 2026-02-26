On this page

# Get Margin Borrow/Loan Interest History(USER_DATA)

## API Description

Get Margin Borrow/Loan Interest History

## HTTP Request

GET `/papi/v1/margin/marginInterestHistory`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
asset| STRING| NO|   
startTime| LONG| NO|   
endTime| LONG| NO|   
current| LONG| NO| Currently querying page. Start from 1. Default:1  
size| LONG| NO| Default:10 Max:100  
archived| STRING| NO| Default: `false`. Set to `true` for archived data from 6 months ago  
recvWindow| LONG| NO| The value cannot be greater than `60000`  
timestamp| LONG| YES|   
  
>   * Response in descending order
>   * The max interval between startTime and endTime is 30 days. It is a MUST to ensure data correctness.
>   * If `startTime` and `endTime` not sent, return records of the last 7 days by default
>   * If `startTime` is sent and `endTime` is not sent, the records from `startTime` to the present will be returned; if `startTime` is more than 30 days ago, the records of the past 30 days will be returned.
>   * If `startTime` is not sent and `endTime` is sent, the records of the 7 days before `endTime` is returned.
>   * Type in response has 5 enums: 
>     * `PERIODIC` interest charged per hour
>     * `ON_BORROW` first interest charged on borrow
>     * `PERIODIC_CONVERTED` interest charged per hour converted into BNB
>     * `ON_BORROW_CONVERTED` first interest charged on borrow converted into BNB
>     * `PORTFOLIO` Portfolio Margin negative balance daily interest
> 

## Response Example
    
    
    {  
      "rows": [  
        {              
          "txId": 1352286576452864727,             
          "interestAccuredTime": 1672160400000,              
          "asset": "USDT",   
          "rawAsset": “USDT”,             
          "principal": "45.3313",              
          "interest": "0.00024995",              
          "interestRate": "0.00013233",              
          "type": "ON_BORROW"            
        }  
      ],  
      "total": 1  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Get-Margin-BorrowLoan-Interest-History#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-Margin-BorrowLoan-Interest-History#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Get-Margin-BorrowLoan-Interest-History#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-Margin-BorrowLoan-Interest-History#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Get-Margin-BorrowLoan-Interest-History#response-example>)

