On this page

# List All Convert Pairs

## API Description

Query for all convertible token pairs and the tokens’ respective upper/lower limits

## HTTP Request

GET `/fapi/v1/convert/exchangeInfo`

## Request Weight

**20(IP)**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
fromAsset| STRING| EITHER OR BOTH| User spends coin  
toAsset| STRING| EITHER OR BOTH| User receives coin  
  
>   * User needs to supply either or both of the input parameter
>   * If not defined for both fromAsset and toAsset, only partial token pairs will be returned
>   * Asset BNFCR is only available to convert for MICA region users.
> 

## Response Example
    
    
    [  
      {  
        "fromAsset":"BTC",  
        "toAsset":"USDT",  
        "fromAssetMinAmount":"0.0004",  
        "fromAssetMaxAmount":"50",  
        "toAssetMinAmount":"20",  
        "toAssetMaxAmount":"2500000"  
      }  
    ]  
    

  * [API Description](</docs/derivatives/usds-margined-futures/convert#api-description>)
  * [HTTP Request](</docs/derivatives/usds-margined-futures/convert#http-request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/convert#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/convert#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/convert#response-example>)

