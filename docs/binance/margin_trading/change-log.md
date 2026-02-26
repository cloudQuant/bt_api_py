
# Change Log


## 2026-01-21​

- Following the announcement from 2025-11-10, the following endpoints/methods will no longer be available starting from **2026-02-20 07:00 UTC**: **REST API**
  - POST /sapi/v1/userDataStream
  - PUT /sapi/v1/userDataStream
  - DELETE /sapi/v1/userDataStream
  - POST /sapi/v1/userDataStream/isolated
  - PUT /sapi/v1/userDataStream/isolated
  - DELETE /sapi/v1/userDataStream/isolated

## 2026-01-16​

- Update on endpoints restrictions
  - `GET /sapi/v1/margin/capital-flow` ：Addition of query time range restriction. This restriction will take effect from approximately **2026-02-02 07:00 UTC** .
  - When both `startTime` and `endTime` are specified, the time span cannot exceed 7 days, otherwise, the endpoint is expected to return an error: 
```
{  "code": -4047,  "msg": "Time interval must be within 0-7 days"}
```
  - Please split your query into multiple requests if the time range required exceeds 7 days.

## 2025-12-26​


**Time-sensitive Notice**

- **The following change to REST API will occur at approximately 2026-01-15 07:00 UTC:**  When calling endpoints that require signatures, percent-encode payloads before computing signatures. Requests that do not follow this order will be rejected with [-1022 INVALID_SIGNATURE](/docs/margin_trading/error-code#-1022-invalid_signature) . Please review and update your signing logic accordingly.

**REST API**

- Updated documentation for REST API regarding [Signed Endpoints examples for placing an order](/docs/margin_trading/general-info#signed-endpoint-examples-for-post-apiv3order) .

## 2025-11-10​

- **All documentation related with listenKey for use on wss://stream.binance.com  removed from the Margin Trading SAPI portal on 2025-11-10. The features below will remain available until a future retirement announcement is made..**
  - POST /sapi/v1/userDataStream
  - PUT /sapi/v1/userDataStream
  - DELETE /sapi/v1/userDataStream
  - POST /sapi/v1/userDataStream/isolated
  - PUT /sapi/v1/userDataStream/isolated
  - DELETE /sapi/v1/userDataStream/isolated
- **Users are recommended to move to the new listen token subscription method below, which offers slightly better performance (lower latency):**
  - POST /sapi/v1/userListenToken : Create a new user data stream and return a listenToken.
  - method userDataStream.subscribe.listenToken : Subscribe to the user data stream using listenToken.
- The [User Data Stream](https://developers.binance.com/docs/margin_trading/trade-data-stream) documentation will remain as reference for the payloads users can receive:
  - [Account Update](https://developers.binance.com/docs/margin_trading/trade-data-stream/Event-Account-Update) : outboundAccountPosition is sent any time an account balance has changed and contains the assets that were possibly changed by the event that generated the balance change.
  - [Balance Update](https://developers.binance.com/docs/margin_trading/trade-data-stream/Event-Balance-Update) : Balance Update occurs when transfer of funds between accounts.
  - [Order Update](https://developers.binance.com/docs/margin_trading/trade-data-stream/Event-Order-Update) : Orders are updated with the executionReport event.

## 2025-10-06​

- **Receiving user data stream on wss://stream.binance.com:9443 using a listenKey is now deprecated.**
  - This feature will be removed from our system at a later date.
  - The related documents will also be removed together with the feature removal.
  - Users are recommended to move to the new listen token subscription method below.
- New user data stream subscription with [Websocket API](https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/general-api-information) released:
  - POST /sapi/v1/userListenToken : Create a new user data stream and return a listenToken.
  - method userDataStream.subscribe.listenToken : Subscribe to the user data stream using listenToken.

## 2025-09-16​

- One endpoint updated :
  - POST `/sapi/v1/margin/apiKey` : Supported produects scope and error code description added into the API description.

## 2025-06-17​

- Best Practice section uploaded on the Margin Trading

## 2025-06-16​

- List schedule endpoint is now released for Margin:
  - GET `/sapi/v1/margin/list-schedule` : Get the upcoming tokens or symbols listing schedule for Cross Margin and Isolated Margin.

## 2024-09-19​

- Binance Margin offers low-latency trading through a special key, available exclusively to users with VIP level 4 or higher. If you are VIP level 3 or below, please contact your VIP manager for eligibility criterias. The endpoints below are available :
  - POST /sapi/v1/margin/apiKey
  - DELETE /sapi/v1/margin/apiKey
  - PUT /sapi/v1/margin/apiKey/ip
  - GET /sapi/v1/margin/apiKey
  - GET /sapi/v1/margin/api-key-list
- How to use the special API key
  1. Use SAPI to create a special pair of "margin trade only" key/secret via the endpoint above.
  2. For cross margin account, do not send the symbol parameter.
  3. For isolated margin account of a specific symbol, please send the symbol as the isolated margin pair.
  4. Use the key/secret responded in step 1 to do the margin trading and listenKey creating via SPOT REST api ([https://api.binance.com/api/v3/](https://api.binance.com/api/v3/)) endpoints.

## 2024-09-13​

- One-Triggers-the-Other (OTO) orders and One-Triggers-a-One-Cancels-The-Other (OTOCO) orders are now enabled for Margin:
  - POST `/sapi/v1/margin/order/oto` : Post a new OTO order for margin account
  - POST `/sapi/v1/margin/order/otoco` : Post a new OTOCO order for margin account
- New parameters added into response body to replace the parameter of 'transferEnabled' in the endpoint of GET `/sapi/v1/margin/account` :
  - transferInEnabled
  - transferOutEnabled

---


## 2024-01-09​

- According to the [announcement](https://www.binance.com/en/support/announcement/updates-on-binance-margin-sapi-endpoints-2024-03-31-a1868c686ce7448da8c3061a82a87b0c), Binance Margin will remove the following SAPI interfaces at 4:00 on March 31, 2024 (UTC). Please switch to the corresponding alternative interfaces in time:
  - `POST /sapi/v1/margin/transfer` will be removed, please replace with `POST /sapi/v1/asset/transfer` universal transfer
  - `POST /sapi/v1/margin/isolated/transfer` will be removed, please replace with `POST /sapi/v1/asset/transfer` universal transfer
  - `POST /sapi/v1/margin/loan` will be removed, please replace with the new `POST /sapi/v1/margin/borrow-repay` borrowing and repayment interface
  - `POST /sapi/v1/margin/repay` will be removed, please replace with the new `POST /sapi/v1/margin/borrow-repay` borrowing and repayment interface
  - `GET /sapi/v1/margin/isolated/transfer` will be removed, please replace it with `GET /sapi/v1/margin/transfer` to get total margin transfer history
  - `GET /sapi/v1/margin/asset` will be removed, please replace with `GET /sapi/v1/margin/allAssets`
  - `GET /sapi/v1/margin/pair` will be removed, please replace with `GET /sapi/v1/margin/allPairs`
  - `GET /sapi/v1/margin/isolated/pair` will be removed, please replace with `GET /sapi/v1/margin/isolated/allPairs`
  - `GET /sapi/v1/margin/loan` will be removed, please replace with `GET /sapi/v1/margin/borrow-repay`
  - `GET /sapi/v1/margin/repay` will be removed, please replace with `GET /sapi/v1/margin/borrow-repay`
  - `GET /sapi/v1/margin/dribblet` will be removed, please replace with `GET /sapi/v1/asset/dribblet`
  - `GET /sapi/v1/margin/dust` will be removed, please replace with `POST /sapi/v1/asset/dust-btc`
  - `POST /sapi/v1/margin/dust` will be removed, please replace with `POST /sapi/v1/asset/dust`
- New Endpoints for Margin:
  - `POST /sapi/v1/margin/borrow-repay` : Margin account borrow/repay
  - `GET /sapi/v1/margin/borrow-repay` : Query borrow/repay records in Margin account
- Update Endpoints fpr Margin:
  - `GET /sapi/v1/margin/transfer` : add parameter `isolatedSymbol` , add response body
  - `GET /sapi/v1/margin/allAssets` : add parameter `asset` , add response body
  - `GET /sapi/v1/margin/allPairs` : add parameter `symbol`
  - `GET /sapi/v1/margin/isolated/allPairs` : add parameter `symbol`

---


## 2023-12-22​

- New Websocket for Margin Trading:
  - New Base url `wss://margin-stream.binance.com` for two events: Liability update event and Margin call event

---


## 2023-11-21​

- Update endpoints for Margin
  - `POST /sapi/v1/margin/order` ：New enumerate value `AUTO_BORROW_REPAY` for the field of `sideEffectType`
  - `POST /sapi/v1/margin/order/oco` ：New enumerate value `AUTO_BORROW_REPAY` for the field of `sideEffectType`
  - `GET /sapi/v1/margin/available-inventory` ：New response field of `updateTime` which indicates the acquisition time of lending inventory.

---


## 2023-11-17​

- New endpoint for Margin to support cross margin Pro mode [FAQ](https://www.binance.com/en/support/faq/introduction-to-binance-cross-margin-pro-0b5441a1c1ff431bb2e135dfa8e6ffba) :
  - `GET /sapi/v1/margin/leverageBracket` : query Liability coin leverage bracket in cross margin Pro mode
- Update endpoints for Margin:
  - `POST /sapi/v1/margin/max-leverage` : field `maxLeverage` adds value 10 for Cross Margin Pro
  - `GET /sapi/v1/margin/account` : add new response field `accountType` , `MARGIN_2` for Cross Margin Pro

---


## 2023-10-16​

- New endpoint for Margin:
  - `GET /sapi/v1/margin/available-inventory` : Query margin available inventory
  - `POST /sapi/v1/margin/manual-liquidation` : Margin manual liquidation

---


## 2023-08-31​

- New endpoint for Margin:
  - `/sapi/v1/margin/capital-flow` : Get cross or isolated margin capital flow

---


## 2023-07-07​

- New endpoints for Margin:
  - `POST /sapi/v1/margin/max-leverage` : Adjust cross margin max leverage

---


## 2023-06-29​

- New endpoints for Margin:
  - `GET /sapi/v1/margin/dust` : Get Assets That Can Be Converted Into BNB
  - `POST /sapi/v1/margin/dust` : Convert dust assets to BNB.

---


## 2023-06-22​

- Update endpoints for Margin:
  - `POST /sapi/v1/margin/order` : add fields `autoRepayAtCancel` and `selfTradePreventionMode`
  - `POST /sapi/v1/margin/order/oco` : add field `selfTradePreventionMode`

---


## 2023-06-20​

- Update endpoints for Margin:
  - `GET /sapi/v1/margin/delist-schedule` : get tokens or symbols delist schedule for cross margin and isolated margin

---


## 2023-02-27​

- New endpoints for Margin:
  - `/sapi/v1/margin/next-hourly-interest-rate` : Get user the next hourly estimate interest

---


## 2023-02-02​

- New endpoints for Margin:
  - `GET /sapi/v1/margin/exchange-small-liability` : Query the coins which can be small liability exchange
  - `POST /sapi/v1/margin/exchange-small-liability` : Cross Margin Small Liability Exchange
  - `GET /sapi/v1/margin/exchange-small-liability-history` : Get Small liability Exchange History

---


## 2022-09-16​

- New endpoint for Margin：
  - `GET /sapi/v1/margin/tradeCoeff` : Get personal margin level information

---


## 2022-07-01​

- New endpoint for Margin:
  - `GET /sapi/v1/margin/dribblet` to query the historical information of user's margin account small-value asset conversion BNB.
- Update endpoint for Margin:
  - `GET /sapi/v1/margin/repay` : Add response field rawAsset.

---


## 2022-05-26​

- Update info for the following margin account endpoints: The max interval between `startTime` and `endTime` is 30 days.:
  - `GET /sapi/v1/margin/transfer`
  - `GET /sapi/v1/margin/loan`
  - `GET /sapi/v1/margin/repay`
  - `GET /sapi/v1/margin/isolated/transfer`
  - `GET /sapi/v1/margin/interestHistory`

---


## 2022-04-26​

- `GET /sapi/v1/margin/rateLimit/order` added
  - The endpoint will display the user's current margin order count usage for all intervals.

---


## 2021-12-30​

- Update endpoint for Margin：
  - Removed out `limit` from `GET /sapi/v1/margin/interestRateHistory` ; The max interval between startTime and endTime is 30 days.

---


## 2021-12-03​

- New endpoints for Margin:
  - `GET  /sapi/v1/margin/crossMarginData` to get cross margin fee data collection
  - `GET  /sapi/v1/margin/isolatedMarginData` to get isolated margin fee data collection
  - `GET  /sapi/v1/margin/isolatedMarginTier` to get isolated margin tier data collection

---


## 2021-10-14​

- Update the time range of the response data for the following margin account endpoints, `startTime` and `endTime` time span will not exceed 30 days, without time parameter sent the system will return the last 7 days of data by default, while the `archived` parameter is `true` , the system will return the last 7 days of data 6 months ago by default:
  - `GET /sapi/v1/margin/transfer`
  - `GET /sapi/v1/margin/loan`
  - `GET /sapi/v1/margin/repay`
  - `GET /sapi/v1/margin/isolated/transfer`
  - `GET /sapi/v1/margin/interestHistory`

---


## 2021-09-08​

- Add endpoints for enabled isolated margin account limit:
  - `DELETE /sapi/v1/margin/isolated/account` to disable isolated margin account for a specific symbol
  - `POST /sapi/v1/margin/isolated/account` to enable isolated margin account for a specific symbol
  - `GET /sapi/v1/margin/isolated/accountLimit` to query enabled isolated margin account limit
- New field "enabled" in response of `GET /sapi/v1/margin/isolated/account` to check if the isolated margin account is enabled

---


## 2021-08-23​

- New endpoints for Margin Account OCO:
  - `POST /sapi/v1/margin/order/oco`
  - `DELETE /sapi/v1/margin/orderList`
  - `GET /sapi/v1/margin/orderList`
  - `GET /sapi/v1/margin/allOrderList`
  - `GET /sapi/v1/margin/openOrderList`

Same usage as spot account OCO


---


## 2021-04-28​


On **May 15, 2021 08:00 UTC** the SAPI Create Margin Account endpoint will be discontinued:

- `POST /sapi/v1/margin/isolated/create`

Isolated Margin account creation and trade preparation can be completed directly through Isolated Margin funds transfer `POST /sapi/v1/margin/isolated/transfer`


---


## 2021-03-05​

- New endpoints for Margin:
  - `GET /sapi/v1/margin/interestRateHistory` to support margin interest rate history query

---


## 2021-01-15​

- New endpoint `DELETE /sapi/v1/margin/openOrders` for Margin Trade
  - This will allow a user to cancel all open orders on a single symbol for margin account.
  - This endpoint will cancel all open orders including OCO orders for margin account.

---


## 2020-12-01​

- Update Margin Trade Endpoint:
  - `POST /sapi/v1/margin/order` new parameter `quoteOrderQty` allow a user to specify the total `quoteOrderQty` spent or received in the `MARKET` order.

---


## 2020-11-16​

- Updated endpoints for Margin, new parameter `archived` to query data from 6 months ago:
  - `GET /sapi/v1/margin/loan`
  - `GET /sapi/v1/margin/repay`
  - `GET /sapi/v1/margin/interestHistory`

---


## 2020-11-10​

- New endpoint to toggle BNB Burn:
  - `POST /sapi/v1/bnbBurn` to toggle BNB Burn on spot trade and margin interest.
  - `GET /sapi/v1/bnbBurn` to get BNB Burn status.

---


## 2020-09-30​

- Update endpoints for Margin Account:
  - `GET /sapi/v1/margin/maxBorrowable` new field `borrowLimit` in response for account borrow limit.

---


## 2020-08-26​

- New parameter `symbols` added in the endpoint `GET /sapi/v1/margin/isolated/account` .

---


## 2020-07-28​


ISOLATED MARGIN

- New parameters "isIsolated" and "symbol" added for isolated margin in the following endpoints:
  - `POST /sapi/v1/margin/loan`
  - `POST /sapi/v1/margin/repay`
- New parameter "isIsolated" and new response field "isIsolated" added for isolated margin in the following endpoints:
  - `POST /sapi/v1/margin/order`
  - `DELETE /sapi/v1/margin/order`
  - `GET /sapi/v1/margin/order`
  - `GET /sapi/v1/margin/openOrders`
  - `GET /sapi/v1/margin/allOrders`
  - `GET /sapi/v1/margin/myTrades`
- New parameter "isolatedSymbol" and new response field "isolatedSymbol" added for isolated margin in the following endpoints:
  - `GET /sapi/v1/margin/loan`
  - `GET /sapi/v1/margin/repay`
  - `GET /sapi/v1/margin/interestHistory`
- New parameter "isolatedSymbol" and new response field "isIsolated" added for isolated margin in the following endpoint `GET /sapi/v1/margin/forceLiquidationRec`
- New parameter "isolatedSymbol" added for isolated margin in the following endpoints:
  - `GET /sapi/v1/margin/maxBorrowable`
  - `GET /sapi/v1/margin/maxTransferable`
- New endpoints for isolated margin:
  - `POST /sapi/v1/margin/isolated/create`
  - `POST /sapi/v1/margin/isolated/transfer`
  - `GET /sapi/v1/margin/isolated/transfer`
  - `GET /sapi/v1/margin/isolated/account`
  - `GET /sapi/v1/margin/isolated/pair`
  - `GET /sapi/v1/margin/isolated/allPairs`
- New endpoints for listenKey management of isolated margin account:
  - `POST /sapi/v1/userDataStream/isolated`
  - `PUT /sapi/v1/userDataStream/isolated`
  - `DELETE /sapi/v1/userDataStream/isolated`

---


## 2019-12-18​

- New endpoint to get daily snapshot of account:  `GET /sapi/v1/accountSnapshot`

---


## 2019-11-30​

- Added parameter `sideEffectType` in `POST  /sapi/v1/margin/order (HMAC SHA256)` with enums:
  - `NO_SIDE_EFFECT` for normal trade order;
  - `MARGIN_BUY` for margin trade order;
  - `AUTO_REPAY` for making auto repayment after order filled.
- New field `marginBuyBorrowAmount` and `marginBuyBorrowAsset` in `FULL` response to `POST  /sapi/v1/margin/order (HMAC SHA256)`

---


## 2019-11-28​

- New SAPI endpont to disable fast withdraw switch:  `POST /sapi/v1/account/disableFastWithdrawSwitch (HMAC SHA256)`
- New SAPI endpont to enable fast withdraw switch:  `POST /sapi/v1/account/enableFastWithdrawSwitch (HMAC SHA256)`