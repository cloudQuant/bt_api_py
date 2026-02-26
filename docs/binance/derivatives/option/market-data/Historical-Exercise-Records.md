On this page

# Historical Exercise Records

## API Description

Get historical exercise records.

  * REALISTIC_VALUE_STRICKEN -> Exercised
  * EXTRINSIC_VALUE_EXPIRED -> Expired OTM

## HTTP Request

GET `/eapi/v1/exerciseHistory`

## Request Weight

**3**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
underlying| STRING| NO| Underlying index like BTCUSDT  
startTime| LONG| NO| Start Time  
endTime| LONG| NO| End Time  
limit| INT| NO| Number of records Default:100 Max:100  
  
## Response Example
    
    
    [  
      {   
        "symbol": "BTC-220121-60000-P",            // symbol    
        "strikePrice": "60000",                    // strike price  
        "realStrikePrice": "38844.69652571",       // real strike price  
        "expiryDate": 1642752000000,               // Exercise time  
        "strikeResult": "REALISTIC_VALUE_STRICKEN" // strike result  
      }  
    ]  
    

  * [API Description](</docs/derivatives/option/market-data/Historical-Exercise-Records#api-description>)
  * [HTTP Request](</docs/derivatives/option/market-data/Historical-Exercise-Records#http-request>)
  * [Request Weight](</docs/derivatives/option/market-data/Historical-Exercise-Records#request-weight>)
  * [Request Parameters](</docs/derivatives/option/market-data/Historical-Exercise-Records#request-parameters>)
  * [Response Example](</docs/derivatives/option/market-data/Historical-Exercise-Records#response-example>)

