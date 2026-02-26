On this page

# Position ADL Quantile Estimation(USER_DATA)

## API Description

Query position ADL quantile estimation

>   * Values update every 30s.
>   * Values 0, 1, 2, 3, 4 shows the queue position and possibility of ADL from low to high.
>   * For positions of the symbol are in One-way Mode or isolated margined in Hedge Mode, "LONG", "SHORT", and "BOTH" will be returned to show the positions' adl quantiles of different position sides.
>   * If the positions of the symbol are crossed margined in Hedge Mode: 
>     * "HEDGE" as a sign will be returned instead of "BOTH";
>     * A same value caculated on unrealized pnls on long and short sides' positions will be shown for "LONG" and "SHORT" when there are positions in both of long and short sides.
> 

## HTTP Request

GET `/dapi/v1/adlQuantile`

## Request Weight

**5**

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
## Response Example
    
    
    [  
    	{  
    		"symbol": "BTCUSD_200925",   
    		"adlQuantile":   
    			{  
    				// if the positions of the symbol are crossed margined in Hedge Mode, "LONG" and "SHORT" will be returned a same quantile value, and "HEDGE" will be returned instead of "BOTH".  
    				"LONG": 3,    
    				"SHORT": 3,   
    				"HEDGE": 0   // only a sign, ignore the value  
    			}  
    		},  
     	{  
     		"symbol": "BTCUSD_201225",   
     		"adlQuantile":   
     			{  
     				// for positions of the symbol are in One-way Mode or isolated margined in Hedge Mode  
     				"LONG": 1, 	    // adl quantile for "LONG" position in hedge mode  
     				"SHORT": 2, 	// adl qauntile for "SHORT" position in hedge mode  
     				"BOTH": 0		// adl qunatile for position in one-way mode  
     			}  
     	}  
     ]  
    

  * [API Description](</docs/derivatives/coin-margined-futures/trade/Position-ADL-Quantile-Estimation#api-description>)
  * [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Position-ADL-Quantile-Estimation#http-request>)
  * [Request Weight](</docs/derivatives/coin-margined-futures/trade/Position-ADL-Quantile-Estimation#request-weight>)
  * [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Position-ADL-Quantile-Estimation#request-parameters>)
  * [Response Example](</docs/derivatives/coin-margined-futures/trade/Position-ADL-Quantile-Estimation#response-example>)

