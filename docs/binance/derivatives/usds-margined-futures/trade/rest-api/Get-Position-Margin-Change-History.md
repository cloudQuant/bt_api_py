On this page

# Get Position Margin Change History (TRADE)

## HTTP Request

GET `/fapi/v1/positionMargin/history`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
type| INT| NO| 1: Add position margin，2: Reduce position margin  
startTime| LONG| NO|   
endTime| LONG| NO| Default current time if not pass  
limit| INT| NO| Default: 500  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Support querying future histories that are not older than 30 days
>   * The time between `startTime` and `endTime`can't be more than 30 days
> 

## Response Example
    
    
    [  
    	{  
    	  	"symbol": "BTCUSDT",  
    	  	"type": 1,  
    		"deltaType": "USER_ADJUST",  
    		"amount": "23.36332311",  
    	  	"asset": "USDT",  
    	  	"time": 1578047897183,  
    	  	"positionSide": "BOTH"  
    	},  
    	{  
    		"symbol": "BTCUSDT",  
    	  	"type": 1,   
    		"deltaType": "USER_ADJUST",  
    		"amount": "100",  
    	  	"asset": "USDT",  
    	  	"time": 1578047900425,  
    	  	"positionSide": "LONG"   
    	}  
    ]  
    

  * [HTTP Request](</docs/derivatives/usds-margined-futures/trade/rest-api/Get-Position-Margin-Change-History#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/trade/rest-api/Get-Position-Margin-Change-History#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/trade/rest-api/Get-Position-Margin-Change-History#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/trade/rest-api/Get-Position-Margin-Change-History#response-example>)

