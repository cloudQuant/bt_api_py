On this page

# Margin Account Trade List (USER_DATA)

## API Description

Margin Account Trade List

## HTTP Request

GET `/papi/v1/margin/myTrades`

## Weight

**5**

## Parameters:

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| YES|   
orderId| LONG| NO|   
startTime| LONG| NO|   
endTime| LONG| NO|   
fromId| LONG| NO| TradeId to fetch from. Default gets most recent trades.  
limit| INT| NO| Default 500; max 1000.  
recvWindow| LONG| NO| The value cannot be greater than 60000  
timestamp| LONG| YES|   
  
**Notes:**

  * If `fromId` is set, it will get trades >= that `fromId`. Otherwise most recent trades are returned.

## Response:
    
    
    [  
        {  
            "commission": "0.00006000",  
            "commissionAsset": "BTC",  
            "id": 34,  
            "isBestMatch": true,  
            "isBuyer": false,  
            "isMaker": false,  
            "orderId": 39324,  
            "price": "0.02000000",  
            "qty": "3.00000000",  
            "symbol": "BNBBTC",  
            "time": 1561973357171  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/Margin-Account-Trade-List#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/Margin-Account-Trade-List#http-request>)
  * [Weight](</docs/derivatives/portfolio-margin/trade/Margin-Account-Trade-List#weight>)
  * [Parameters:](</docs/derivatives/portfolio-margin/trade/Margin-Account-Trade-List#parameters>)
  * [Response:](</docs/derivatives/portfolio-margin/trade/Margin-Account-Trade-List#response>)

