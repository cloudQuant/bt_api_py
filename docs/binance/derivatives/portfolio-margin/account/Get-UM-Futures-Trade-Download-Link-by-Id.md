On this page

# Get UM Futures Trade Download Link by Id(USER_DATA)

## API Description

Get UM futures trade download link by Id

## HTTP Request

GET `/papi/v1/um/trade/asyn/id`

## Request Weight

**10**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
downloadId| STRING| YES| get by download id api  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Download link expiration: 24h
> 

## Response Example

> **Response:**
    
    
    {  
    	"downloadId":"545923594199212032",  
      	"status":"completed",     // Enum：completed，processing  
      	"url":"www.binance.com",  // The link is mapped to download id  
    	"s3Link": null,  
      	"notified":true,          // ignore  
      	"expirationTimestamp":1645009771000,  // The link would expire after this timestamp  
      	"isExpired":null,  
    }  
    

> **OR** (Response when server is processing)
    
    
    {  
    	"downloadId":"545923594199212032",  
      	"status":"processing",  
      	"url":"",   
    	"s3Link": null,  
      	"notified":false,  
      	"expirationTimestamp":-1  
      	"isExpired":null,  
      	  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Trade-Download-Link-by-Id#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Trade-Download-Link-by-Id#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Trade-Download-Link-by-Id#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Trade-Download-Link-by-Id#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Trade-Download-Link-by-Id#response-example>)

