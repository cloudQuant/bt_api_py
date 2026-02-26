
# Margin account borrow/repay(MARGIN)


## API Description​


Margin account borrow/repay(MARGIN)


## HTTP Request​


POST `/sapi/v1/margin/borrow-repay`


## Request Weight​


1500


## Request Parameters​


| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | YES |  |
| isIsolated | STRING | YES | TRUE for Isolated Margin, FALSE for Cross Margin, Default FALSE |
| symbol | STRING | YES | Only for Isolated margin |
| amount | STRING | YES |  |
| type | STRING | YES | BORROW or REPAY |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


## Response Example​


```
{  //transaction id  "tranId": 100000001}
```


**Error Code Description: **

- **INSUFFICIENT_INVENTORY** The error {"code": -3045, "msg": "The system does not have enough asset now."} can occur to both manual Margin borrow requests and auto-borrow Margin orders that require actual borrowing. The error can be due to: We recommend monitoring the system status and adjusting your borrowing strategies accordingly.
  - The Margin system's available assets are below the requested borrowing amount.
  - The system's inventory is critically low, leading to the rejection of all borrowing requests, irrespective of the amount.
- **EXCEED_MAX_BORROWABLE** The error {"code": -3006, "msg": "Your borrow amount has exceed maximum borrow amount."}  occurs when your borrow request exceeds the maximum allowable amount. You can check the maximum borrowable amount using [GET /sapi/v1/margin/maxBorrowable](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Query-Max-Borrow) and adjust your request accordingly.
- **REPAY_EXCEED_LIABILITY** When repaying your debt, ensure that your repayment does not exceed the outstanding borrowed amount. Otherwise, the error {“code”: -3015, “msg”: “Repay amount exceeds borrow amount.”} will occur.
- **ASSET_ADMIN_BAN_BORROW** This error {“code”: -3012, “msg”: “Borrow is banned for this asset.”} indicates that borrowing is currently prohibited for the specified asset. You can check the availability of borrowing via [GET /sapi/v1/margin/allAssets](https://developers.binance.com/docs/margin_trading/market-data/Get-All-Margin-Assets). You can also check if there are any announcements or updates regarding the asset's borrowing status on Binance's official channels.
- **FEW_LIABILITY_LEFT** If you get an error {"code": -3015, "msg": "The unpaid debt is too small after this repayment."}, this means your repayment would leave a remaining debt below Binance's minimum threshold. You can resolve this by adjusting the repayment to meet the minimum requirement.
- **HAS_PENDING_TRANSACTION** This error {“code”: -3007, “msg”: “You have pending transaction, please try again later.”} indicates that there is an ongoing borrow or repayment process in your account, preventing new borrow or repayment actions. This can occur in both manual and auto-borrow margin orders. Key points to consider:
  - Concurrent Transactions: The system processes borrow and repay requests sequentially, even if they involve different assets. An ongoing transaction can block new requests temporarily.
  - Processing Time: Typically, these borrow/repay complete within 100 milliseconds. To lower the potential of encountering this error, you may wish to set your requests apart with at least 100 milliseconds intervals.
  - Auto Repayment: Auto-repay orders might fail silently due to the same issue, without generating an error message. We suggest you check your outstanding loan once the auto-repay orders are triggered.