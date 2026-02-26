On this page

# Query UM Position Information(USER_DATA)

## API Description

Get current UM position information.

## HTTP Request

GET `/papi/v1/um/positionRisk`

## Request Weight

**5**

**Parameters:**

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
**Note**

>   * Please use with user data stream `ACCOUNT_UPDATE` to meet your timeliness and accuracy needs. 
>   * for One-way Mode user, the response will only show the "BOTH" positions
>   * for Hedge Mode user, the response will show "LONG", and "SHORT" positions.
> 

## Response Example

>   * For One-way position mode:
> 

    
    
     [  
        {  
            "entryPrice": "0.00000",   
            "leverage": "10",   
            "markPrice": "6679.50671178",     
            "maxNotionalValue": "20000000",   
            "positionAmt": "0.000",   
            "notional": "0",  
            "symbol": "BTCUSDT",   
            "unRealizedProfit": "0.00000000",   
            "liquidationPrice": "6170.20509059",  
            "positionSide": "BOTH",   
            "updateTime": 1625474304765     
        }  
    ]  
    

> Or For Hedge position mode(only return with position):
    
    
    [  
        {  
            "symbol": "BTCUSDT",  
            "positionAmt": "0.001",  
            "entryPrice": "22185.2",  
            "markPrice": "21123.05052574",  
            "unRealizedProfit": "-1.06214947",  
            "liquidationPrice": "6170.20509059",  
            "leverage": "4",  
            "maxNotionalValue": "100000000",  
            "positionSide": "LONG",  
            "notional": "21.12305052",  
            "updateTime": 1655217461579  
        },  
        {  
            "symbol": "BTCUSDT",  
            "positionAmt": "0.000",  
            "entryPrice": "0.0",  
            "markPrice": "21123.05052574",  
            "unRealizedProfit": "0.00000000",  
            "liquidationPrice": "6170.20509059",  
            "leverage": "4",  
            "maxNotionalValue": "100000000",  
            "positionSide": "SHORT",  
            "notional": "0",  
            "updateTime": 0  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/account/Query-UM-Position-Information#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/account/Query-UM-Position-Information#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/account/Query-UM-Position-Information#request-weight>)
  * [Response Example](</docs/derivatives/portfolio-margin/account/Query-UM-Position-Information#response-example>)

