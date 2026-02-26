On this page

# Taker Buy/Sell Volume

## API Description

Taker Buy Volume: the total volume of buy orders filled by takers within the period. Taker Sell Volume: the total volume of sell orders filled by takers within the period.

## HTTP Request

GET `/futures/data/takerBuySellVol`

## Request Weight

**1**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
pair| STRING| YES| BTCUSD  
contractType| ENUM| YES| ALL, CURRENT_QUARTER, NEXT_QUARTER, PERPETUAL  
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
    	  "contractType": CURRENT_QUARTER,  
    	  "takerBuyVol": "387",  //unit: cont  
    	  "takerSellVol": "248",  //unit: cont  
    	  "takerBuyVolValue": "2342.1220", //unit: base asset  
    	  "takerSellVolValue": "4213.9800", //unit: base asset  
    	  "timestamp": 1591261042378  
       },  
       {  
         "pair": "BTCUSD",  
    	  "contractType": CURRENT_QUARTER,  
    	  "takerBuyVol": "234",  //unit: cont  
    	  "takerSellVol": "121",  //unit: cont  
    	  "takerBuyVolValue": "4563.1320", //unit: base asset  
    	  "takerSellVolValue": "3313.3940", //unit: base asset  
    	  "timestamp": 1585615200000  
       }  
    ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/market-data/Taker-Buy-Sell-Volume#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Taker-Buy-Sell-Volume#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Taker-Buy-Sell-Volume#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Taker-Buy-Sell-Volume#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/market-data/Taker-Buy-Sell-Volume#response-example>)

