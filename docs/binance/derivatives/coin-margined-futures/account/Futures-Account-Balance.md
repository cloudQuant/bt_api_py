On this page

# Futures Account Balance (USER_DATA)

## API Description

Check futures account balance

## HTTP Request

GET `/dapi/v1/balance`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    [  
     	{  
     		"accountAlias": "SgsR",    // unique account code  
     		"asset": "BTC",  
     		"balance": "0.00250000",  
     		"withdrawAvailable": "0.00250000",  
     		"crossWalletBalance": "0.00241969",  
      		"crossUnPnl": "0.00000000",  
      		"availableBalance": "0.00241969",  
     		"updateTime": 1592468353979  
    	}  
    ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/account/Futures-Account-Balance#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/account/Futures-Account-Balance#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/account/Futures-Account-Balance#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/account/Futures-Account-Balance#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/account/Futures-Account-Balance#response-example>)

