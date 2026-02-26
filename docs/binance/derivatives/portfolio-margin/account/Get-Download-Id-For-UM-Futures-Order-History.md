On this page

# Get Download Id For UM Futures Order History (USER_DATA)

## API Description

Get download id for UM futures order history

## HTTP Request

GET `/papi/v1/um/order/asyn`

## Request Weight

**1500**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
startTime| LONG| YES| Timestamp in ms  
endTime| LONG| YES| Timestamp in ms  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Request Limitation is 10 times per month, shared by front end download page and rest api
>   * The time between `startTime` and `endTime` can not be longer than 1 year
> 

## Response Example
    
    
    {  
    	"avgCostTimestampOfLast30d":7241837, // Average time taken for data download in the past 30 days  
      	"downloadId":"546975389218332672",  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Get-Download-Id-For-UM-Futures-Order-History#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-Download-Id-For-UM-Futures-Order-History#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Get-Download-Id-For-UM-Futures-Order-History#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-Download-Id-For-UM-Futures-Order-History#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Get-Download-Id-For-UM-Futures-Order-History#response-example>)

