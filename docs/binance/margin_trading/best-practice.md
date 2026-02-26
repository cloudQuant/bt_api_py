
# Best Practice


### Activating & Enabling Margin Trading via AP​


#### Enable Margin on Your Account​


Before using the API, please ensure margin trading is enabled on your Binance account. For first time users, you will be required to complete the margin quiz and agree to the margin terms, once completed you will need to transfer supported tokens into the cross margin or isolated margin wallet to activate it. For existing users, you will just need to transfer supported tokens into the cross margin or isolated margin wallet to activate the margin wallet. When creating your API key, check the setting to “Enable Spot & Margin Trading, and Enable Margin Loan, Repay & Transfer”, otherwise margin API calls will be rejected. For your security, please also consider IP whitelisting on your API key.


Please refer to the article [“How to Create API Keys on Binance?”](https://www.binance.com/en/support/faq/detail/360002502072) for more details.


If you are looking for a low-latency connectivity similar to spot trading, VIP 4 and above users are automatically eligible,for further information please refer to [Margin Special Key Api Portal](https://developers.binance.com/docs/margin_trading/trade/Create-Special-Key-of-Low-Latency-Trading)


#### Tips to Avoid Common Mistakes:​

- Account activation: Double-check that your margin account is enabled. Otherwise, your API calls will return the following error {"code":-3003, "msg":"Margin account does not exist."}
- Error handling: If you get an error like {"code":-2015, "msg":"Invalid API-key, IP, or permissions for action."}, it usually means either your API key lacks permission. To resolve this, please enable the API key settings via the website.

### Funding the Margin Account​


Before trading on margin, you need to fund your margin account by transferring assets into it as collateral. Binance keeps Spot (exchange wallet) and Margin wallets separate. For cross margin, you have to transfer assets to the cross margin account; for isolated margin, you have to transfer to the specific isolated account for the trading pair.


#### Cross Margin Transfer​


Users can invoke the following REST API to transfer assets to the cross margin account:


[POST /sapi/v1/asset/transfer](https://developers.binance.com/docs/wallet/asset/user-universal-transfer)


This endpoint uses a parameter type to indicate direction:

- MAIN_MARGIN: Spot account transfer to Margin (cross) account
- UMFUTURE_MARGIN: USDⓈ-M Futures account transfer to Margin (cross) account
- CMFUTURE_MARGIN: COIN-M Futures account transfer to Margin (cross) account
- FUNDING_MARGIN: Funding account transfer to Margin (cross) account
- OPTION_MARGIN: Options account transfer to Margin (cross) account

Other required parameters are asset (e.g. "USDT", "BTC"), amount (the quantity to transfer as a string) and timestamp. Please ensure you have that asset available for use in the source account.

- Request Parameter:

| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| type | ENUM | YES |  |
| asset | STRING | YES |  |
| amount | DECIMAL | YES |  |
| fromSymbol | STRING | NO |  |
| toSymbol | STRING | NO |  |
| recvWindow | LONG | NO |  |
| timestamp | LONG | YES |  |


#### Isolated Margin Transfer​


For isolated account, the type of direction is as follows:

- MARGIN_ISOLATEDMARGIN: Margin(cross) account transfer to Isolated margin account
- ISOLATEDMARGIN_ISOLATEDMARGIN: Isolated margin account transfer to Isolated margin account

When direction types are ISOLATEDMARGIN_MARGIN and ISOLATEDMARGIN_ISOLATEDMARGIN, you should specify the source and destination explicitly. Additional parameters, fromSymbol, is required.


On the other hand, when direction types are MARGIN_ISOLATEDMARGIN and ISOLATEDMARGIN_ISOLATEDMARGIN, toSymbol is required.


You will receive a successful response once the transfer is completed successfully.


{ "tranId": 1234567890 }


This tranId can be used to query transfer status if needed (though usually, a successful response means the transfer is complete).


With funds now in your margin account, you have collateral to borrow against. Next, we’ll borrow funds to perform a leverage trade.


#### Tips to Avoid Common Mistakes:​

- Insufficient collateral margin level: Sometimes, your collateral margin level may be too low to allow a transfer out of your account. You will get an error response {"code":-3020,"msg":"Transfer out amount exceeds max amount."}. To resolve it, you can reduce your outstanding debt or add more assets to meet the required margin level for the transfer.

### Borrowing Funds​


One key feature of margin trading is the ability to borrow funds to increase your position size. On Binance, you can borrow different assets as long as you have sufficient collateral in your margin account for your chosen leverage. Borrowing is subject to interest (accrued hourly), and each asset has a maximum borrowable amount based on your collateral value and a chosen leverage.


In Binance, we use the same endpoint to execute borrow and repay


[POST /sapi/v1/margin/borrow-repay](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Margin-Account-Borrow-Repay)


#### Borrow​


You must specify the asset you want to borrow and the amount. For isolated margin, you will include an extra parameter to indicate the isolated account. Binance uses a boolean isolated flag and a symbol parameter.

- Request Parameter:

| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | YES |  |
| isIsolated | STRING | YES | TRUE for Isolated Margin, FALSE for Cross Margin, Default FALSE |
| symbol | STRING | YES | Only for Isolated margin |
| amount | STRING | YES |  |
| type | STRING | YES | BORROW or REPAY |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


On success, you’ll get a JSON with a transaction ID for the loan. For example:


{ "tranId": 1234567891 }


This tranId is the identifier of the borrowing transaction (you can use it to query the loan status or history later).


After a successful borrow, the borrowed funds will be credited to the corresponding margin account (increasing your “borrowed” balance for that asset). You are now free to use those funds to trade. Keep in mind interest immediately starts accruing on the loan until it’s repaid.


#### Tips to Avoid Common Mistakes:​

- Maximum borrowable: If you get an error {"code": -3006, "msg": "Your borrow amount has exceed maximum borrow amount."}, it means the amount you want to borrow  exceeds your limit. Binance enforces an initial margin requirement – borrowing too much will cause you to be below the initial margin requirement and hence the additional borrowings will be rejected. You can check your max borrowable amount via [GET /sapi/v1/margin/maxBorrowable](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Query-Max-Borrow) for a given asset (and optionally symbol for isolated). This can be useful to see how much you could borrow against your available collateral.
- Interest: Each borrowed asset accrues interest, which you can query via [GET /sapi/v1/margin/interestHistory](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Get-Interest-History). Interest is usually deducted from your margin account (adding to the “interest” field for that asset). Ensure you account for interest when repaying (please note that repayment covers interest first, then principal).
- Asset availability: Not all assets may be borrowable at all times. Binance may have limits or temporarily suspend borrowing for certain assets if liquidity is low. If you get an error that an asset is not borrowable, you may need to check [GET /sapi/v1/margin/allAssets](https://developers.binance.com/docs/margin_trading/market-data/Get-All-Margin-Assets) or try later.
- Insufficient available assets:  If you get an error {"code": -3045, "msg": The system does not have enough asset now."} This can occur to both manual Margin borrow requests and auto-borrow Margin orders that require actual borrowing. This can be due to:
  - The Margin system's assets available for borrowing are less than the requested borrowing amount.
  - The system's inventory is critically low, leading to the rejection of all borrowing requests, irrespective of the amount.

We recommend monitoring the system status and adjusting your borrowing strategies accordingly.

- Cross margin collateral haircuts: If you are trading in Cross Margin/Cross Margin Pro mode, collateral haircuts are factored into the collateral margin level calculation. Meaning assets have tiered collateral ratios that discount their value for margin calculations. For example, higher asset holdings may be valued at lower collateral percentages across tiers, reducing total collateral value accordingly. Please note that this does not affect Isolated Margin mode and the collateral margin level uses collateral asset value, not normal asset value:
  - For Cross Margin Classic, Collateral margin level will be used to calculate maximum borrowing and transfer-out amount, but will NOT be used to trigger Margin Call and Liquidation, where margin level will still be used.
  - For Cross Margin Pro, Collateral margin level will be used to calculate the maximum borrowing and transfer-out amounts, and will be used to trigger Margin Call and Liquidation as well.

You can find out more in [How to Calculate the Margin Level on Cross Margin Pro?](https://www.binance.com/en/support/faq/detail/12a78d8aa813470f96be283b45f75410)

- Portfolio margin collateral haircuts: The USD value of all assets in the Cross Margin, USDⓈ-M Futures, and COIN-M Futures Wallets will be calculated based on the specified collateral rate (Not the same collateral ratio as Cross Margin Classic/Cross Margin Pro). You can find out the collateral ratio in [Tiered Collateral Ratio for PM Pro](https://www.binance.com/en/futures/trading-rules/perpetual/portfolio-margin/tiered-collateral-ratio)

Upon successfully borrowing, the token will be transferred to your margin wallet.Next, we will use the borrowed tokens to place a margin trade (buy or sell).


### Placing a Margin Trade​


With your collateral (including funds you may have borrowed), you can create orders (limit, market, etc.) on your margin account. Binance provides a dedicated endpoint for margin orders:


[POST /sapi/v1/margin/order](https://developers.binance.com/docs/margin_trading/trade/Margin-Account-New-Order)

- Request Parameter:

| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| isIsolated | STRING | NO | for isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| side | ENUM | YES | BUY  SELL |
| type | ENUM | YES |  |
| quantity | DECIMAL | NO |  |
| quoteOrderQty | DECIMAL | NO |  |
| price | DECIMAL | NO |  |
| stopPrice | DECIMAL | NO | Used with STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, and TAKE_PROFIT_LIMIT orders. |
| newClientOrderId | STRING | NO | A unique id among open orders. Automatically generated if not sent. |
| icebergQty | DECIMAL | NO | Used with LIMIT, STOP_LOSS_LIMIT, and TAKE_PROFIT_LIMIT to create an iceberg order. |
| newOrderRespType | ENUM | NO | Set the response JSON. ACK, RESULT, or FULL; MARKET and LIMIT order types default to FULL, all other orders default to ACK. |
| sideEffectType | ENUM | NO | NO_SIDE_EFFECT, MARGIN_BUY, AUTO_REPAY,AUTO_BORROW_REPAY; default NO_SIDE_EFFECT. More info inFAQ |
| timeInForce | ENUM | NO | GTC,IOC,FOK |
| selfTradePreventionMode | ENUM | NO | The allowed enums is dependent on what is configured on the symbol. The possible supported values are EXPIRE_TAKER, EXPIRE_MAKER, EXPIRE_BOTH, NONE |
| autoRepayAtCancel | BOOLEAN | NO | Only when MARGIN_BUY or AUTO_BORROW_REPAY order takes effect, true means that the debt generated by the order needs to be repay after the order is cancelled. The default is true |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


#### Auto-Borrow​


If you have manually borrowed funds, you can use those in your margin trade. If you have not borrowed manually, Binance’s margin order endpoint can auto-borrow or auto-repay for you, depending on a parameter called sideEffectType.


The sideEffectType parameter lets you automate borrowing or repaying as part of the order placement.

- NO_SIDE_EFFECT: No automatic borrow or repay. This is the default setting. You should have sufficient balance in your margin account (either from deposits or prior manual borrows) to execute the order. If you do not, the order placement will fail due to insufficient balance. Users essentially self-manage their borrowing and repayment themselves.
- MARGIN_BUY: Automatic borrowing when needed for a BUY order. If you place a BUY order and do not have enough quote assets to deduct, Binance will automatically borrow the necessary amount of the quote asset up to your leverage limit. For a SELL order, if using MARGIN_BUY mode, it would auto-borrow the asset to sell (though typically one would use MARGIN_BUY for buys; see AUTO_BORROW_REPAY for a comprehensive mode). The borrow occurs only if needed and only when the order is executed (not at order creation). Essentially this equates to “borrow asset + place order” in one step.
- AUTO_REPAY: Automatic repayment after the order executes. If your order results in you obtaining the asset that you owe (borrowed), the system will immediately use the proceeds to repay the loan. For example, if you had borrowed BTC and you sold some BTC (thus obtaining the quoted assets), setting AUTO_REPAY would use the quote assets you have to repay the BTC loan (converting the quote asset to BTC as needed to repay). As an example: if you borrowed USDT to buy BTC, when you sell BTC (for USDT) with AUTO_REPAY, it will use the USDT you receive to repay the USDT loan. Important: auto-repay will repay as much as possible of that asset’s liability (interest first) and will only work if the trade yields the same asset that was borrowed. This is useful to close out a margin position in one step (“place order + upon fill, repay debt”).
- AUTO_BORROW_REPAY: Combines both of the above – automatic borrow and automatic repay in one step. This mode will borrow as needed to execute the order, and then after execution, immediately try to repay with whatever assets were obtained. It’s effectively a margin flip: e.g., if you have nothing and submit a BUY with AUTO_BORROW_REPAY, it will borrow the quote asset, buy the base asset, then if that base asset was what you needed to repay (in case of short position) it would repay, etc. In practice, this mode is a bit complex and is not allowed for certain multi-leg orders (like OCO/OTOCO).

For beginners, you may wish to start with NO_SIDE_EFFECT (ensuring you manually borrowed what you need) or use MARGIN_BUY if you want to skip the manual borrow step for a buy. Always verify that these automated steps did what you expected (please check your balances and loans after the order).


#### Tips to Avoid Common Mistakes:​

- Order Rules: Margin orders obey the same trading rules as spot (lot size, price step, minimum notional value, etc.). You can check the exchange information( [GET /api/v3/exchangeInfo](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-endpoints) ) to find min/max order sizes or the margin symbol information ( [GET /sapi/v1/margin/allAssets](https://developers.binance.com/docs/margin_trading/market-data/Get-All-Margin-Assets) ) to find the minimum borrow/repay amount. If an order is invalid (e.g., too small), you’ll get an error about lot size or minimum notional.
- Canceling Orders: If you need to cancel an open margin order, use [DELETE /sapi/v1/margin/order](https://developers.binance.com/docs/margin_trading/trade/Margin-Account-Cancel-Order#http-request) with similar parameters (symbol, orderId or newclientOrderId, and if isolated, isIsolated=TRUE). There is also an endpoint to cancel all open margin orders on a symbol ( [DELETE /sapi/v1/margin/openOrders](https://developers.binance.com/docs/margin_trading/trade/Margin-Account-Cancel-All-Open-Orders) ). Canceling a margin order that had an auto-borrow does not automatically repay the borrow (unless you set autoRepayAtCancel=true for One-Triggers-a-One-Cancels-the-Other (OTOCO) orders – a parameter autoRepayAtCancel exists for that purpose.)
- Limit price restriction: This error {“code”: -3064, “msg”: “Limit price needs to be within (-15%,15%) of current index price for this margin trading pair.”} often occurs when the limit price is not allowed. For certain low liquidity pairs or stablecoin to stablecoin pairs on Margin (e.g. USDT/DAI), there will be a price bracket of [-15%, 15%] (which is subject to changes). Please adjust the limit price accordingly.

At this stage, you have executed margin trades. Now it is important that you monitor your margin account to manage risk, as margin trading carries the risk of liquidation if the market moves against you.


### Monitoring the Margin Account​


Properly monitoring your margin account is vital. You need to keep track of your balances, margin level (risk ratio), and any accumulating interest. Binance provides endpoints to fetch account details for both cross and isolated margin accounts.


#### Cross Margin Account Details​


The endpoint: [GET /sapi/v1/margin/account](https://developers.binance.com/docs/margin_trading/account/Query-Cross-Margin-Account-Details) returns an overview of your cross margin account. This includes your current margin level, total asset value, total liability (debt) value, and a breakdown of each asset. Key fields in the response:


{


"assets":[


```
{    "baseAsset":     {      "asset": "BTC",      "borrowEnabled": true,      "borrowed": "0.00000000",      "free": "0.00000000",      "interest": "0.00000000",      "locked": "0.00000000",      "netAsset": "0.00000000",      "netAssetOfBtc": "0.00000000",      "repayEnabled": true,      "totalAsset": "0.00000000"    },    "quoteAsset":     {      "asset": "USDT",      "borrowEnabled": true,      "borrowed": "0.00000000",      "free": "0.00000000",      "interest": "0.00000000",      "locked": "0.00000000",      "netAsset": "0.00000000",      "netAssetOfBtc": "0.00000000",      "repayEnabled": true,      "totalAsset": "0.00000000"    },    "symbol": "BTCUSDT",    "isolatedCreated": true,     "enabled": true, // true-enabled, false-disabled    "marginLevel": "0.00000000",     "marginLevelStatus": "EXCESSIVE", // "EXCESSIVE", "NORMAL", "MARGIN\_CALL", "PRE\_LIQUIDATION", "FORCE\_LIQUIDATION"    "marginRatio": "0.00000000",    "indexPrice": "10000.00000000",    "liquidatePrice": "1000.00000000",    "liquidateRate": "1.00000000",    "tradeEnabled": true  }],"totalAssetOfBtc": "0.00000000","totalLiabilityOfBtc": "0.00000000","totalNetAssetOfBtc": "0.00000000"
```


}


#### Isolated Margin Account Details​


For isolated accounts, there is a similar endpoint: [GET /sapi/v1/margin/isolated/account](https://developers.binance.com/docs/margin_trading/account/Query-Isolated-Margin-Account-Info). This response includes details for each isolated margin account you have enabled. Each isolated account will have fields including isolatedMarginLevel, totalAsset, totalLiability, etc., specific to that pair, along with an array of assets (two assets per isolated pair, typically).


{


"assets":[


```
{    "baseAsset":     {      "asset": "BTC",      "borrowEnabled": true,      "borrowed": "0.00000000",      "free": "0.00000000",      "interest": "0.00000000",      "locked": "0.00000000",      "netAsset": "0.00000000",      "netAssetOfBtc": "0.00000000",      "repayEnabled": true,      "totalAsset": "0.00000000"    },    "quoteAsset":     {      "asset": "USDT",      "borrowEnabled": true,      "borrowed": "0.00000000",      "free": "0.00000000",      "interest": "0.00000000",      "locked": "0.00000000",      "netAsset": "0.00000000",      "netAssetOfBtc": "0.00000000",      "repayEnabled": true,      "totalAsset": "0.00000000"    },    "symbol": "BTCUSDT",    "isolatedCreated": true,     "enabled": true, // true-enabled, false-disabled    "marginLevel": "0.00000000",     "marginLevelStatus": "EXCESSIVE", // "EXCESSIVE", "NORMAL", "MARGIN\_CALL", "PRE\_LIQUIDATION", "FORCE\_LIQUIDATION"    "marginRatio": "0.00000000",    "indexPrice": "10000.00000000",    "liquidatePrice": "1000.00000000",    "liquidateRate": "1.00000000",    "tradeEnabled": true  }],"totalAssetOfBtc": "0.00000000","totalLiabilityOfBtc": "0.00000000","totalNetAssetOfBtc": "0.00000000"
```


}


#### User Data Stream (Advanced)​


Binance provides a WebSocket User Data Stream for margin accounts that can push real-time updates on account balance changes and order updates. If you need real-time monitoring (instead of polling REST endpoints), you can create a listenKey via [POST /sapi/v1/userDataStream](https://developers.binance.com/docs/margin_trading/trade-data-stream/Start-Margin-User-Data-Stream) (for margin) and subscribe to events. The use of data stream is advanced and beyond the scope of this guide, but you may wish to keep it in mind if you want instantaneous alerts on margin calls or fills.


After actively managing your positions, you will eventually want to close them and repay any borrowed funds. Let’s move to repaying the loans.


### Repaying Borrowed Tokens​


Once you’ve finished using the borrowed tokens (for example, after closing a leveraged trade), you can repay your margin loans. Repaying returns the borrowed tokens to Binance and frees up your collateral. You can repay partially or in full.


In Binance, we use the same endpoint to execute borrow and repay


[POST /sapi/v1/margin/borrow-repay](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Margin-Account-Borrow-Repay)

- Request Parameter:

| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| asset | STRING | YES |  |
| isIsolated | STRING | YES | TRUE for Isolated Margin, FALSE for Cross Margin, Default FALSE |
| symbol | STRING | YES | Only for Isolated margin |
| amount | STRING | YES |  |
| type | STRING | YES | BORROW or REPAY |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


Upon repayment successfully, you will receive a JSON with a transaction ID for the loan. For example:


{ "tranId": 1234567894 }


This indicates the repay transaction was successful. After repayment, your outstanding loan for that asset should decrease by the amount repaid (and interest for that asset may drop to 0 if fully repaid).


With borrowing and repaying covered, you have essentially completed a margin trade lifecycle: fund account -> borrow -> trade -> (optional: trade back) -> repay. The last piece is keeping track of what happened – your trade and transaction history.


### Reviewing Trading and Account History​


Iti’s important to review your margin trades and account activities, both for understanding your performance and for record-keeping. It can also be helpful for debugging your trading bot. Binance’s API provides endpoints to query past orders, trades, and account actions (transfers, loans, repayments, etc.).


#### Trade History​


To get a list of trades (fills) executed on your margin account, use [GET /sapi/v1/margin/myTrades.](https://developers.binance.com/docs/margin_trading/trade/Query-Margin-Account-Trade-List#http-request)

- Request Parameter:

| Name | Type | Mandatory | Description |
| --- | --- | --- | --- |
| symbol | STRING | YES |  |
| isIsolated | STRING | NO | For isolated margin or not, "TRUE", "FALSE"，default "FALSE" |
| orderId | LONG | NO |  |
| startTime | LONG | NO |  |
| endTime | LONG | NO |  |
| fromId | LONG | NO | TradeId to fetch from. Default gets most recent trades. |
| limit | INT | NO | Default 500; max 1000. |
| recvWindow | LONG | NO | The value cannot be greater than 60000 |
| timestamp | LONG | YES |  |


For example, after an earlier BNB buy, a call to myTrades for BNBBTC may return something like:


[
{
"commission": "0.00006000",
"commissionAsset": "BTC",
"id": 34,
"isBestMatch": true,
"isBuyer": false,
"isMaker": false,
"orderId": 39324,
"price": "0.02000000",
"qty": "3.00000000",
"symbol": "BNBBTC",
"isIsolated": false,
"time": 1561973357171
}
]


#### Order History​


If you need the order details (including orders that might not have any fills, such as canceled orders), you can use:

- [GET /sapi/v1/margin/allOrders](https://developers.binance.com/docs/margin_trading/trade/Query-Margin-Account-All-Orders) – to fetch all orders (filled, canceled, etc.) on a symbol.
- [GET /sapi/v1/margin/openOrders](https://developers.binance.com/docs/margin_trading/trade/Query-Margin-Account-Open-Orders#http-request) – for current open orders (similar to spot).
- [GET /sapi/v1/margin/order](https://developers.binance.com/docs/margin_trading/trade/Query-Margin-Account-Order) – to query a specific order by ID.

#### Account Activity History​


Binance provides endpoints to review other account activities:

- [GET /sapi/v1/margin/borrow-repay](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Query-Borrow-Repay#http-request) – Query borrow/repay history. You can filter by asset, isolated symbol, etc. This returns records of each loan and repay transaction with amounts, interest, status. For example, it can show when you borrowed 100 USDC, with a tranId and timestamp.
- [GET /sapi/v1/margin/transfer](https://developers.binance.com/docs/margin_trading/transfer#http-request) – Query transfer history between spot and margin. You can see deposits and withdrawals from margin accounts, with timestamps and amounts.
- [GET /sapi/v1/margin/interestHistory](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Get-Interest-History#http-request) – List of interest charged over time per asset.
- [GET /sapi/v1/margin/forceLiquidationRec](https://developers.binance.com/docs/margin_trading/trade/Get-Force-Liquidation-Record#http-request) – Record of any forced liquidations (hopefully none!).