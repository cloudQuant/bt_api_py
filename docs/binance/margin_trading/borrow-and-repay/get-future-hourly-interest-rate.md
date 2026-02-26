
# Get future hourly interest rate (USER_DATA)


## API Description​


Get future hourly interest rate


## HTTP Request​


GET `/sapi/v1/margin/next-hourly-interest-rate`


## Request Weight(IP)​


**100**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| assets | String | YES | List of assets, separated by commas, up to 20 |
| isIsolated | Boolean | YES | for isolated margin or not, "TRUE", "FALSE" |


## Response Example​


```
[    {        "asset": "BTC",        "nextHourlyInterestRate": "0.00000571"    },    {        "asset": "ETH",        "nextHourlyInterestRate": "0.00000578"    }]
```
