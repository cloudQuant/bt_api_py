
# Edit ip for Special Key(Low-Latency Trading)(TRADE)


## API Description​


Edit ip restriction. This only applies to Special Key for Low Latency Trading.


You need to enable Permits “Enable Spot & Margin Trading” option for the API Key which requests this endpoint.


## HTTP Request​


PUT `/sapi/v1/margin/apiKey/ip`


## Request Weight​


**1(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| apiKey | STRING | YES |  |
| symbol | STRING | NO | isolated margin pair |
| ip | STRING | YES | Can be added in batches, separated by commas. Max 30 for an API key |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{}
```
