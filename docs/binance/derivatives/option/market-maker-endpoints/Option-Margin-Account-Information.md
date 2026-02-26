On this page

# Option Margin Account Information (USER_DATA)

## API Description

Get current account information.

## HTTP Request

GET `/eapi/v1/marginAccount`

## Request Weight

**3**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
      "asset": [                    
        {  
          "asset": "USDT",                  // Asset type  
          "marginBalance": "10099.448"      // Account balance  
          "equity": "10094.44662",          // Account equity  
          "available": "8725.92524",        // Available funds  
          "initialMargin": "1084.52138",    // Initial margin  
          "maintMargin": "151.00138",       // Maintenance margin  
          "unrealizedPNL": "-5.00138",      // Unrealized profit/loss  
          "lpProfit": "-5.00138",           // Unrealized profit for long position   
         }  
      ],   
      "greek": [  
        {  
          "underlying":"BTCUSDT"            // Option Underlying  
          "delta": "-0.05"                  // Account delta  
          "gamma": "-0.002"                 // Account gamma  
          "theta": "-0.05"                  // Account theta  
          "vega": "-0.002"                  // Account vega    
        }  
      ],  
      "time": 1592449455993                 // Time    
    }     
    

  * [API Description](</docs/derivatives/option/market-maker-endpoints#api-description>)
  * [HTTP Request](</docs/derivatives/option/market-maker-endpoints#http-request>)
  * [Request Weight](</docs/derivatives/option/market-maker-endpoints#request-weight>)
  * [Request Parameters](</docs/derivatives/option/market-maker-endpoints#request-parameters>)
  * [Response Example](</docs/derivatives/option/market-maker-endpoints#response-example>)

