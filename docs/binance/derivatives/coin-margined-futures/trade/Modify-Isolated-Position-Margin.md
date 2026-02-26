On this page

# Modify Isolated Position Margin(TRADE)

## API Description

Modify Isolated Position Margin

## HTTP Request

POST `/dapi/v1/positionMargin`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
positionSide| ENUM| NO| Default `BOTH` for One-way Mode ; `LONG` or `SHORT` for Hedge Mode. It must be sent with Hedge Mode.  
amount| DECIMAL| YES|   
type| INT| YES| 1: Add position margin,2: Reduce position margin  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Only for isolated symbol
> 

## Response Example
    
    
    {  
    	"amount": 100.0,  
      	"code": 200,  
      	"msg": "Successfully modify position margin.",  
      	"type": 1  
    }  
    

  * [API Description](</docs/derivatives/coin-margined-futures/trade/Modify-Isolated-Position-Margin#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Modify-Isolated-Position-Margin#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/trade/Modify-Isolated-Position-Margin#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Modify-Isolated-Position-Margin#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/trade/Modify-Isolated-Position-Margin#response-example>)

