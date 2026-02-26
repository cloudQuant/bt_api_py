On this page

# User Commission Rate (USER_DATA)

## API Description

Query user commission rate

## HTTP Request

GET `/dapi/v1/commissionRate`

## Request Weight

**20**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
    	"symbol": "BTCUSD_PERP",  
      	"makerCommissionRate": "0.00015",  // 0.015%  
      	"takerCommissionRate": "0.00040"   // 0.040%  
    }  
    

  * [API Description](</docs/derivatives/coin-margined-futures/account/User-Commission-Rate#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/account/User-Commission-Rate#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/account/User-Commission-Rate#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/account/User-Commission-Rate#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/account/User-Commission-Rate#response-example>)

