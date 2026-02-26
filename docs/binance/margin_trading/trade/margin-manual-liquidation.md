
# Margin Manual Liquidation(MARGIN)


## API Description​


Margin Manual Liquidation


## HTTP Request​


POST `/sapi/v1/margin/manual-liquidation`


## Request Weight(UID)​


**3000**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| type | STRING | YES | MARGIN,ISOLATED |
| symbol | STRING | NO | When type selects ISOLATED, symbol must be filled in |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |


Additional notes:

- This endpoint can support Cross Margin Classic Mode and Pro Mode.
- And only support Isolated Margin for restricted region.

## Response Example​


```
{  "asset": "ETH",  "interest": "0.00083334",  "principal": "0.001",  "liabilityAsset": "USDT",  "liabilityQty": 0.3552}
```
