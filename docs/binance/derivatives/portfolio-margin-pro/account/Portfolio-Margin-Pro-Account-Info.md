On this page

# Get Portfolio Margin Pro Account Info(USER_DATA)

## API Description

Get Portfolio Margin Pro Account Info

## HTTP Request

GET `/sapi/v1/portfolio/account`

## Request Weight(IP)")

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
            "uniMMR": "5167.92171923",        // Classic Portfolio margin account maintenance margin rate  
            "accountEquity": "122607.35137903",  // Account equity, unit：USD  
            "actualEquity": "142607.35137903",   // Actual equity, unit：USD  
            "accountMaintMargin": "23.72469206", // Classic Portfolio margin account maintenance margin, unit：USD  
            "accountInitialMargin": "47.44938412", // Ignored for PM PRO and PM PRO SPAN  
            "totalAvailableBalance" : "122,559.90199491",// Ignored for PM PRO and PM PRO SPAN  
            "accountStatus": "NORMAL",   // Classic Portfolio margin account status:"NORMAL", "MARGIN_CALL", "SUPPLY_MARGIN", "REDUCE_ONLY", "ACTIVE_LIQUIDATION", "FORCE_LIQUIDATION", "BANKRUPTED"  
            "accountType": "PM_1"     //PM_1 for PM PRO, PM_2 for PM, PM_3 for PM PRO SPAN   
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin-pro/account#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin-pro/account#http-request>)
  * [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account#request-weightip>)
  * [Request Parameters](</docs/derivatives/portfolio-margin-pro/account#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin-pro/account#response-example>)

