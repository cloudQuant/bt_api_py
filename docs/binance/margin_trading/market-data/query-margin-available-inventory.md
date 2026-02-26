
# Query Margin Available Inventory(USER_DATA)


## API Description​


Margin available Inventory query


## HTTP Request​


GET `/sapi/v1/margin/available-inventory`


## Request Weight(UID)​


**50**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| type | STRING | YES | MARGIN,ISOLATED |


## Response Example​


```
{    "assets": {        "MATIC": "100000000",        "STPT": "100000000",        "TVK": "100000000",        "SHIB": "97409653"    }   "updateTime": 1699272487}
```
