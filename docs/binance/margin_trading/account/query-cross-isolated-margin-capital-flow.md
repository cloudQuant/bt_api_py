
# Query Cross Isolated Margin Capital Flow (USER_DATA)


## API Description​


Query Cross Isolated Margin Capital Flow


## HTTP Request​


GET `/sapi/v1/margin/capital-flow`


## Request Weight​


**100(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | NO |  |
| symbol | STRING | NO | Mandatory for Isolated data |
| type | STRING | NO |  |
| startTime | LONG | NO | Only supports querying data from the past 90 days. |
| endTime | LONG | NO |  |
| fromId | LONG | NO | If fromId is set, data with id greater than fromId will be returned. Otherwise, the latest data will be returned. |
| limit | LONG | NO | Limit on the number of data records returned per request. Default: 500; Maximum: 1000. |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |

- Only supports querying the data of the last 90 days
- The time between startTime and endTime cannot be longer than 7 days.
- If fromId is set, the data with id > fromId will be returned. Otherwise the latest data will be returned
- To query isolated data, Symbol needs to be entered.
- Supported types:
  - TRANSFER("Transfer")
  - BORROW("Borrow")
  - REPAY("Repay")
  - BUY_INCOME("Buy-Trading Income")
  - BUY_EXPENSE("Buy-Trading Expense")
  - SELL_INCOME("Sell-Trading Income")
  - SELL_EXPENSE("Sell-Trading Expense")
  - TRADING_COMMISSION("Trading Commission")
  - BUY_LIQUIDATION("Buy by Liquidation")
  - SELL_LIQUIDATION("Sell by Liquidation")
  - REPAY_LIQUIDATION("Repay by Liquidation")
  - OTHER_LIQUIDATION("Other Liquidation")
  - LIQUIDATION_FEE("Liquidation Fee")
  - SMALL_BALANCE_CONVERT("Small Balance Convert")
  - COMMISSION_RETURN("Commission Return")
  - SMALL_CONVERT("Small Convert")

## Response Example​


```
[  {     "id": 123456,    "tranId": 123123,    "timestamp": 1691116657000,    "asset": "USDT",    "symbol": "BTCUSDT",    "type": "BORROW",    "amount": "101"  },  {    "id": 123457,    "tranId": 123124,    "timestamp": 1691116658000,    "asset": "BTC",    "symbol": "BTCUSDT",    "type": "REPAY",    "amount": "10"  }]
```
