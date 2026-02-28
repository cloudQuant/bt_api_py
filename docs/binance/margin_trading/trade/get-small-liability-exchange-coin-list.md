
# Get Small Liability Exchange Coin List (USER_DATA)


## API Description​


Query the coins which can be small liability exchange


## HTTP Request​


GET `/sapi/v1/margin/exchange-small-liability`


## Request Weight(IP)​


- *100**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| recvWindow | LONG | NO |  |

| timestamp | LONG | YES |  |


## Response Example​


```bash
[    {      "asset": "ETH",      "interest": "0.00083334",      "principal": "0.001",      "liabilityAsset": "USDT",      "liabilityQty": 0.3552    }]

```bash
