On this page

# UM Account Trade List(USER_DATA)

## API Description

Get trades for a specific account and UM symbol.

## HTTP Request

GET `/papi/v1/um/userTrades`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
startTime| LONG| NO|   
endTime| LONG| NO|   
fromId| LONG| NO| Trade id to fetch from. Default gets most recent trades.  
limit| INT| NO| Default 500; max 1000.  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * If `startTime` and `endTime` are both not sent, then the last '24 hours' data will be returned.
>   * The time between `startTime` and `endTime` cannot be longer than 24 hours.
>   * The parameter `fromId` cannot be sent with `startTime` or `endTime`.
> 

## Response Example
    
    
    [  
        {  
            "symbol": "BTCUSDT",  
            "id": 67880589,  
            "orderId": 270093109,  
            "side": "SELL",  
            "price": "28511.00",  
            "qty": "0.010",  
            "realizedPnl": "2.58500000",  
            "quoteQty": "285.11000",  
            "commission": "-0.11404400",  
            "commissionAsset": "USDT",  
            "time": 1680688557875,  
            "buyer": false,  
            "maker": false,  
            "positionSide": "BOTH"  
        }  
    ]   
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/UM-Account-Trade-List#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/UM-Account-Trade-List#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/UM-Account-Trade-List#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/UM-Account-Trade-List#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/UM-Account-Trade-List#response-example>)

