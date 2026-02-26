
# Adjust cross margin max leverage (USER_DATA)


## API Description​


Adjust cross margin max leverage


## HTTP Request​


POST `/sapi/v1/margin/max-leverage`


## Request Weight(UID)​


**3000**


## Request Limit​


1 times/min per IP


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| maxLeverage | Integer | YES | Can only adjust 3 , 5 or 10，Example: maxLeverage = 5 or 3 for Cross Margin Classic; maxLeverage=10 for Cross Margin Pro 10x leverage or 20x if compliance allows. |

- The margin level need higher than the initial risk ratio of adjusted leverage, the initial risk ratio of 3x is 1.5 , the initial risk ratio of 5x is 1.25;  The detail conditions on how to switch between Cross Margin Classic and Cross Margin Pro can refer to [the FAQ](https://www.binance.com/en/support/faq/how-to-activate-the-cross-margin-pro-mode-on-binance-e27786da05e743a694b8c625b3bc475d) .

## Response Example​


```
{    "success": true}
```
