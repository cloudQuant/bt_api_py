
# Delete Special Key(Low-Latency Trading)(TRADE)


## API Description​


This only applies to Special Key for Low Latency Trading.


If apiKey is given, apiName will be ignored. If apiName is given with no apiKey, all apikeys with given apiName will be deleted.


You need to enable Permits “Enable Spot & Margin Trading” option for the API Key which requests this endpoint.


## HTTP Request​


DELETE `/sapi/v1/margin/apiKey`


## Request Weight​


**1(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| apiKey | STRING | NO |  |
| apiName | STRING | NO |  |
| symbol | STRING | NO | isolated margin pair |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{}
```
