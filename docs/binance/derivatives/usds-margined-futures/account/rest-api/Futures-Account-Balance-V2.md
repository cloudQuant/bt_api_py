On this page

# Futures Account Balance V2 (USER_DATA)

## API Description

Query account balance info

## HTTP Request

GET `/fapi/v2/balance`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    [  
     	{  
     		"accountAlias": "SgsR",    // unique account code  
     		"asset": "USDT",  	// asset name  
     		"balance": "122607.35137903", // wallet balance  
     		"crossWalletBalance": "23.72469206", // crossed wallet balance  
      		"crossUnPnl": "0.00000000"  // unrealized profit of crossed positions  
      		"availableBalance": "23.72469206",       // available balance  
      		"maxWithdrawAmount": "23.72469206",     // maximum amount for transfer out  
      		"marginAvailable": true,    // whether the asset can be used as margin in Multi-Assets mode  
      		"updateTime": 1617939110373  
    	}  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Futures-Account-Balance-V2#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Futures-Account-Balance-V2#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Futures-Account-Balance-V2#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Futures-Account-Balance-V2#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Futures-Account-Balance-V2#response-example>)

