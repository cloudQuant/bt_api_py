
# Get Limit Price Pairs(MARKET_DATA)


## API Description​


Query trading pairs with restriction on limit price range.
In margin trading, you can place orders with limit price. Limit price should be within (-15%, 15%) of current index price for a list of margin trading pairs. This rule only impacts limit sell orders with limit price that is lower than current index price and limit buy orders with limit price that is higher than current index price.

- Buy order: Your order will be rejected with an error message notification if the limit price is 15% above the index price.
- Sell order: Your order will be rejected with an error message notification if the limit price is 15% below the index price.
Please review the limit price order placing strategy, backtest and calibrate the planned order size with the trading volume and order book depth to prevent trading loss.

## HTTP Request​


GET `/sapi/v1/margin/limit-price-pairs`


## Request Weight(IP)​


**1**


## Request Parameters​


NA


## Response Example​


```
 {  "crossMarginSymbols":   	[  "BLURUSDC",    	"SANDBTC",   	"QKCBTC",   	"SEIFDUSD",   	"NEOUSDC",   	"ARBFDUSD",   	"ORDIUSDC"  	]  }
```
