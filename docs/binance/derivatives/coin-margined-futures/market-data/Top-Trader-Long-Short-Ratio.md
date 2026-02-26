On this page

# Top Trader Long/Short Ratio (Positions)

## API Description

The proportion of net long and net short positions to total open positions of the top 20% users with the highest margin balance. Long Position % = Long positions of top traders / Total open positions of top traders Short Position % = Short positions of top traders / Total open positions of top traders Long/Short Ratio (Positions) = Long Position % / Short Position %

## HTTP Request

GET `/futures/data/topLongShortPositionRatio`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
pair| STRING| YES| BTCUSD  
period| ENUM| YES| "5m","15m","30m","1h","2h","4h","6h","12h","1d"  
limit| LONG| NO| Default 30,Max 500  
startTime| LONG| NO|   
endTime| LONG| NO|   
  
>   * If startTime and endTime are not sent, the most recent data is returned.
>   * Only the data of the latest 30 days is available.
> 

## Response Example
    
    
    [    
       {  
    	  "pair": "BTCUSD",  
    	  "longShortRatio": "0.7869",  
    	  "longPosition": "0.6442",  //64.42%  
    	  "shortPosition": "0.4404",  //44.04%  
    	  "timestamp": 1592870400000  
       },  
       {  
         "pair": "BTCUSD",  
    	  "longShortRatio": "1.1231",  
    	  "longPosition": "0.2363",    
    	  "shortPosition": "0.4537",    
    	  "timestamp": 1592956800000  
    	}  
    ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/market-data/Top-Trader-Long-Short-Ratio#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Top-Trader-Long-Short-Ratio#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Top-Trader-Long-Short-Ratio#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Top-Trader-Long-Short-Ratio#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/market-data/Top-Trader-Long-Short-Ratio#response-example>)

