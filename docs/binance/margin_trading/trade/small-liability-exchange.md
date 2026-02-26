
# Small Liability Exchange (MARGIN)


## API Description​


Small Liability Exchange


## HTTP Request​


POST `/sapi/v1/margin/exchange-small-liability`


## Request Weight​


**3000(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| assetNames | ARRAY | YES | The assets list of small liability exchange， Example: assetNames = BTC,ETH |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |

- Only convert once within 6 hours
- Only liability valuation less than 10 USDT are supported
- The maximum number of coin is 10

## Response Example​
