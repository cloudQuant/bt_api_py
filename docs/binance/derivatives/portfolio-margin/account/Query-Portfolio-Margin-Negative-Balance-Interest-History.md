On this page

# Query Portfolio Margin Negative Balance Interest History(USER_DATA)

## API Description

Query interest history of negative balance for portfolio margin.

## HTTP Request

`GET /papi/v1/portfolio/interest-history`

## Request Weight

**50**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
asset| STRING| NO|   
startTime| LONG| NO|   
endTime| LONG| NO|   
size| LONG| NO| Default:10 Max:100  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Response in descending order
>   * The max interval between startTime and endTime is 30 days. It is a MUST to ensure data correctness.
>   * If `startTime` and `endTime` not sent, return records of the last 7 days by default
>   * If `startTime` is sent and `endTime` is not sent, the records from `startTime` to the present will be returned; if `startTime` is more than 30 days ago, the records of the past 30 days will be returned.
>   * If `startTime` is not sent and `endTime` is sent, the records of the 7 days before `endTime` is returned.
> 

## Response Example
    
    
    [  
        {  
            "asset": "USDT",      
            "interest": "24.4440",               //interest amount  
            "interestAccuredTime": 1670227200000,  
            "interestRate": "0.0001164",         //daily interest rate  
            "principal": "210000"  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Query-Portfolio-Margin-Negative-Balance-Interest-History#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Query-Portfolio-Margin-Negative-Balance-Interest-History#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Query-Portfolio-Margin-Negative-Balance-Interest-History#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Query-Portfolio-Margin-Negative-Balance-Interest-History#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Query-Portfolio-Margin-Negative-Balance-Interest-History#response-example>)

