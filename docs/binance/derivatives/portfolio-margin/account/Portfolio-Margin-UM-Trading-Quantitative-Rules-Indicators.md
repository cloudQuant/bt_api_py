On this page

# Portfolio Margin UM Trading Quantitative Rules Indicators(USER_DATA)

## API Description

Portfolio Margin UM Trading Quantitative Rules Indicators

## HTTP Request

GET `/papi/v1/um/apiTradingStatus`

## Request Weight

**1** for a single symbol **10** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    {  
        "indicators": { // indicator: quantitative rules indicators, value: user's indicators value, triggerValue: trigger indicator value threshold of quantitative rules.   
            "BTCUSDT": [  
                {  
                    "isLocked": true,  
                    "plannedRecoverTime": 1545741270000,  
                    "indicator": "UFR",  // Unfilled Ratio (UFR)  
                    "value": 0.05,  // Current value  
                    "triggerValue": 0.995  // Trigger value  
                },  
                {  
                    "isLocked": true,  
                    "plannedRecoverTime": 1545741270000,  
                    "indicator": "IFER",  // IOC/FOK Expiration Ratio (IFER)  
                    "value": 0.99,  // Current value  
                    "triggerValue": 0.99  // Trigger value  
                },  
                {  
                    "isLocked": true,  
                    "plannedRecoverTime": 1545741270000,  
                    "indicator": "GCR",  // GTC Cancellation Ratio (GCR)  
                    "value": 0.99,  // Current value  
                    "triggerValue": 0.99  // Trigger value  
                },  
                {  
                    "isLocked": true,  
                    "plannedRecoverTime": 1545741270000,  
                    "indicator": "DR",  // Dust Ratio (DR)  
                    "value": 0.99,  // Current value  
                    "triggerValue": 0.99  // Trigger value  
                }  
            ]  
        },  
        "updateTime": 1545741270000  
    }  
    

Or (account violation triggered)
    
    
    {  
        "indicators":{  
            "ACCOUNT":[  
                {  
                    "indicator":"TMV",  //  Too many violations under multiple symbols trigger account violation  
                    "value":10,  
                    "triggerValue":1,  
                    "plannedRecoverTime":1644919865000,  
                    "isLocked":true  
                }  
            ]  
        },  
        "updateTime":1644913304748  
    }  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Portfolio-Margin-UM-Trading-Quantitative-Rules-Indicators#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Portfolio-Margin-UM-Trading-Quantitative-Rules-Indicators#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Portfolio-Margin-UM-Trading-Quantitative-Rules-Indicators#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/account/Portfolio-Margin-UM-Trading-Quantitative-Rules-Indicators#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Portfolio-Margin-UM-Trading-Quantitative-Rules-Indicators#response-example>)

