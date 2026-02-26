On this page

# Query Margin repay Record(USER_DATA)

## API Description

Query margin repay record.

## HTTP Request

GET `/papi/v1/margin/repayLoan`

## Request Weight

**10**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
asset| STRING| YES|   
txId| LONG| NO| the tranId in `POST/papi/v1/repayLoan`  
startTime| LONG| NO|   
endTime| LONG| NO|   
current| LONG| NO| Currently querying page. Start from 1. Default:1  
size| LONG| NO| Default:10 Max:100  
archived| STRING| NO| Default: `false`. Set to `true` for archived data from 6 months ago  
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
>   * txId or startTime must be sent. txId takes precedence.
>   * Response in descending order
>   * The max interval between `startTime` and `endTime` is 30 days.
>   * If `startTime` and `endTime` not sent, return records of the last 7 days by default
>   * Set `archived` to `true` to query data from 6 months ago
> 

## Response Example
    
    
    {  
         "rows": [  
             {  
                    "amount": "14.00000000",   //Total amount repaid  
                    "asset": "BNB",     
                    "interest": "0.01866667",    //Interest repaid  
                    "principal": "13.98133333",   //Principal repaid  
                    "status": "CONFIRMED",   //one of PENDING (pending execution), CONFIRMED (successfully execution), FAILED (execution failed, nothing happened to your account)  
                    "timestamp": 1563438204000,  
                    "txId": 2970933056  
             }  
         ],  
         "total": 1  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Query-Margin-repay-Record#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Query-Margin-repay-Record#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Query-Margin-repay-Record#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Query-Margin-repay-Record#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Query-Margin-repay-Record#response-example>)

