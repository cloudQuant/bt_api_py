On this page

# Error Codes

> The error JSON payload:
    
    
    {  
      "code":-1121,  
      "msg":"Invalid symbol."  
    }  
    

Errors consist of two parts: an error code and a message. Codes are universal, but messages can vary.

## 10xx - General Server or Network issues

### -1000 UNKNOWN

  * An unknown error occurred while processing the request.
  * An unknown error occurred while processing the request.[%s]

### -1001 DISCONNECTED

  * Internal error; unable to process your request. Please try again.

### -1002 UNAUTHORIZED

  * You are not authorized to execute this request.

### -1003 TOO_MANY_REQUESTS

  * Too many requests queued.
  * Too much request weight used; current limit is %s request weight per %s. Please use WebSocket Streams for live updates to avoid polling the API.
  * Way too much request weight used; IP banned until %s. Please use WebSocket Streams for live updates to avoid bans.

### -1004 SERVER_BUSY

  * Server is busy, please wait and try again

### -1006 UNEXPECTED_RESP

  * An unexpected response was received from the message bus. Execution status unknown.

### -1007 TIMEOUT

  * Timeout waiting for response from backend server. Send status unknown; execution status unknown.

### -1008 SERVER_BUSY

  * Spot server is currently overloaded with other requests. Please try again in a few minutes.

### -1014 UNKNOWN_ORDER_COMPOSITION

  * Unsupported order combination.

### -1015 TOO_MANY_ORDERS

  * Too many new orders.
  * Too many new orders; current limit is %s orders per %s.

### -1016 SERVICE_SHUTTING_DOWN

  * This service is no longer available.

### -1020 UNSUPPORTED_OPERATION

  * This operation is not supported.

### -1021 INVALID_TIMESTAMP

  * Timestamp for this request is outside of the recvWindow.
  * Timestamp for this request was 1000ms ahead of the server's time.

### -1022 INVALID_SIGNATURE

  * Signature for this request is not valid.

### -1099 Not found, authenticated, or authorized

  * This replaces error code -1999

## 11xx - 2xxx Request issues

### -1100 ILLEGAL_CHARS

  * Illegal characters found in a parameter.
  * Illegal characters found in a parameter. %s
  * Illegal characters found in parameter `%s`; legal range is `%s`.

### -1101 TOO_MANY_PARAMETERS

  * Too many parameters sent for this endpoint.
  * Too many parameters; expected `%s` and received `%s`.
  * Duplicate values for a parameter detected.

### -1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED

  * A mandatory parameter was not sent, was empty/null, or malformed.
  * Mandatory parameter `%s` was not sent, was empty/null, or malformed.
  * Param `%s` or `%s` must be sent, but both were empty/null!

### -1103 UNKNOWN_PARAM

  * An unknown parameter was sent.

### -1104 UNREAD_PARAMETERS

  * Not all sent parameters were read.
  * Not all sent parameters were read; read `%s` parameter(s) but was sent `%s`.

### -1105 PARAM_EMPTY

  * A parameter was empty.
  * Parameter `%s` was empty.

### -1106 PARAM_NOT_REQUIRED

  * A parameter was sent when not required.
  * Parameter `%s` sent when not required.

### -1111 BAD_PRECISION

  * Precision is over the maximum defined for this asset.

### -1112 NO_DEPTH

  * No orders on book for symbol.

### -1114 TIF_NOT_REQUIRED

  * TimeInForce parameter sent when not required.

### -1115 INVALID_TIF

  * Invalid timeInForce.

### -1116 INVALID_ORDER_TYPE

  * Invalid orderType.

### -1117 INVALID_SIDE

  * Invalid side.

### -1118 EMPTY_NEW_CL_ORD_ID

  * New client order ID was empty.

### -1119 EMPTY_ORG_CL_ORD_ID

  * Original client order ID was empty.

### -1120 BAD_INTERVAL

  * Invalid interval.

### -1121 BAD_SYMBOL

  * Invalid symbol.

### -1125 INVALID_LISTEN_KEY

  * This listenKey does not exist.

### -1127 MORE_THAN_XX_HOURS

  * Lookup interval is too big.
  * More than %s hours between startTime and endTime.

### -1128 OPTIONAL_PARAMS_BAD_COMBO

  * Combination of optional parameters invalid.

### -1130 INVALID_PARAMETER

  * Invalid data sent for a parameter.
  * Data sent for parameter `%s` is not valid.

### -1131 BAD_RECV_WINDOW

  * recvWindow must be less than 60000

### -1134 BAD_STRATEGY_TYPE

  * `strategyType` was less than 1000000.

#### -1145 INVALID_CANCEL_RESTRICTIONS

  * `cancelRestrictions` has to be either `ONLY_NEW` or `ONLY_PARTIALLY_FILLED`.

#### -1151 DUPLICATE_SYMBOLS

  * Symbol is present multiple times in the list.

### -2010 NEW_ORDER_REJECTED

  * NEW_ORDER_REJECTED

### -2011 CANCEL_REJECTED

  * CANCEL_REJECTED

### -2013 NO_SUCH_ORDER

  * Order does not exist.

### -2014 BAD_API_KEY_FMT

  * API-key format invalid.

### -2015 REJECTED_MBX_KEY

  * Invalid API-key, IP, or permissions for action.

### -2016 NO_TRADING_WINDOW

  * No trading window could be found for the symbol. Try ticker/24hrs instead.

#### -2026 ORDER_ARCHIVED

  * Order was canceled or expired with no executed qty over 90 days ago and has been archived.

## 3xxx-5xxx SAPI-specific issues

### -3000 INNER_FAILURE

  * Internal server error.

### -3001 NEED_ENABLE_2FA

  * Please enable 2FA first.

### -3002 ASSET_DEFICIENCY

  * We don't have this asset.

### -3003 NO_OPENED_MARGIN_ACCOUNT

  * Margin account does not exist.

### -3004 TRADE_NOT_ALLOWED

  * Trade not allowed.

### -3005 TRANSFER_OUT_NOT_ALLOWED

  * Transferring out not allowed.

### -3006 EXCEED_MAX_BORROWABLE

  * Your borrow amount has exceed maximum borrow amount.

### -3007 HAS_PENDING_TRANSACTION

  * You have pending transaction, please try again later.

### -3008 BORROW_NOT_ALLOWED

  * Borrow not allowed.

### -3009 ASSET_NOT_MORTGAGEABLE

  * This asset are not allowed to transfer into margin account currently.

### -3010 REPAY_NOT_ALLOWED

  * Repay not allowed.

### -3011 BAD_DATE_RANGE

  * Your input date is invalid.

### -3012 ASSET_ADMIN_BAN_BORROW

  * Borrow is banned for this asset.

### -3013 LT_MIN_BORROWABLE

  * Borrow amount less than minimum borrow amount.

### -3014 ACCOUNT_BAN_BORROW

  * Borrow is banned for this account.

### -3015 REPAY_EXCEED_LIABILITY

  * Repay amount exceeds borrow amount.

### -3016 LT_MIN_REPAY

  * Repay amount less than minimum repay amount.

### -3017 ASSET_ADMIN_BAN_MORTGAGE

  * This asset are not allowed to transfer into margin account currently.

### -3018 ACCOUNT_BAN_MORTGAGE

  * Transferring in has been banned for this account.

### -3019 ACCOUNT_BAN_ROLLOUT

  * Transferring out has been banned for this account.

### -3020 EXCEED_MAX_ROLLOUT

  * Transfer out amount exceeds max amount.

### -3021 PAIR_ADMIN_BAN_TRADE

  * Margin account are not allowed to trade this trading pair.

### -3022 ACCOUNT_BAN_TRADE

  * You account's trading is banned.

### -3023 WARNING_MARGIN_LEVEL

  * You can't transfer out/place order under current margin level.

### -3024 FEW_LIABILITY_LEFT

  * The unpaid debt is too small after this repayment.

### -3025 INVALID_EFFECTIVE_TIME

  * Your input date is invalid.

### -3026 VALIDATION_FAILED

  * Your input param is invalid.

### -3027 NOT_VALID_MARGIN_ASSET

  * Not a valid margin asset.

### -3028 NOT_VALID_MARGIN_PAIR

  * Not a valid margin pair.

### -3029 TRANSFER_FAILED

  * Transfer failed.

### -3036 ACCOUNT_BAN_REPAY

  * This account is not allowed to repay.

### -3037 PNL_CLEARING

  * PNL is clearing. Wait a second.

### -3038 LISTEN_KEY_NOT_FOUND

  * Listen key not found.

### -3041 BALANCE_NOT_CLEARED

  * Balance is not enough

### -3042 PRICE_INDEX_NOT_FOUND

  * PriceIndex not available for this margin pair.

### -3043 TRANSFER_IN_NOT_ALLOWED

  * Transferring in not allowed.

### -3044 SYSTEM_BUSY

  * System busy.

### -3045 SYSTEM

  * The system doesn't have enough asset now.

### -3999 NOT_WHITELIST_USER

  * This function is only available for invited users.

### -4001 CAPITAL_INVALID

  * Invalid operation.

### -4002 CAPITAL_IG

  * Invalid get.

### -4003 CAPITAL_IEV

  * Your input email is invalid.

### -4004 CAPITAL_UA

  * You don't login or auth.

### -4005 CAPAITAL_TOO_MANY_REQUEST

  * Too many new requests.

### -4006 CAPITAL_ONLY_SUPPORT_PRIMARY_ACCOUNT

  * Support main account only.

### -4007 CAPITAL_ADDRESS_VERIFICATION_NOT_PASS

  * Address validation is not passed.

### -4008 CAPITAL_ADDRESS_TAG_VERIFICATION_NOT_PASS

  * Address tag validation is not passed.

### -4010 CAPITAL_WHITELIST_EMAIL_CONFIRM

  * White list mail has been confirmed.

### -4011 CAPITAL_WHITELIST_EMAIL_EXPIRED

  * White list mail is invalid.

### -4012 CAPITAL_WHITELIST_CLOSE

  * White list is not opened.

### -4013 CAPITAL_WITHDRAW_2FA_VERIFY

  * 2FA is not opened.

### -4014 CAPITAL_WITHDRAW_LOGIN_DELAY

  * Withdraw is not allowed within 2 min login.

### -4015 CAPITAL_WITHDRAW_RESTRICTED_MINUTE

  * Withdraw is limited.

### -4016 CAPITAL_WITHDRAW_RESTRICTED_PASSWORD

  * Within 24 hours after password modification, withdrawal is prohibited.

### -4017 CAPITAL_WITHDRAW_RESTRICTED_UNBIND_2FA

  * Within 24 hours after the release of 2FA, withdrawal is prohibited.

### -4018 CAPITAL_WITHDRAW_ASSET_NOT_EXIST

  * We don't have this asset.

### -4019 CAPITAL_WITHDRAW_ASSET_PROHIBIT

  * Current asset is not open for withdrawal.

### -4021 CAPITAL_WITHDRAW_AMOUNT_MULTIPLE

  * Asset withdrawal must be an %s multiple of %s.

### -4022 CAPITAL_WITHDRAW_MIN_AMOUNT

  * Not less than the minimum pick-up quantity %s.

### -4023 CAPITAL_WITHDRAW_MAX_AMOUNT

  * Within 24 hours, the withdrawal exceeds the maximum amount.

### -4024 CAPITAL_WITHDRAW_USER_NO_ASSET

  * You don't have this asset.

### -4025 CAPITAL_WITHDRAW_USER_ASSET_LESS_THAN_ZERO

  * The number of hold asset is less than zero.

### -4026 CAPITAL_WITHDRAW_USER_ASSET_NOT_ENOUGH

  * You have insufficient balance.

### -4027 CAPITAL_WITHDRAW_GET_TRAN_ID_FAILURE

  * Failed to obtain tranId.

### -4028 CAPITAL_WITHDRAW_MORE_THAN_FEE

  * The amount of withdrawal must be greater than the Commission.

### -4029 CAPITAL_WITHDRAW_NOT_EXIST

  * The withdrawal record does not exist.

### -4030 CAPITAL_WITHDRAW_CONFIRM_SUCCESS

  * Confirmation of successful asset withdrawal.

### -4031 CAPITAL_WITHDRAW_CANCEL_FAILURE

  * Cancellation failed.

### -4032 CAPITAL_WITHDRAW_CHECKSUM_VERIFY_FAILURE

  * Withdraw verification exception.

### -4033 CAPITAL_WITHDRAW_ILLEGAL_ADDRESS

  * Illegal address.

### -4034 CAPITAL_WITHDRAW_ADDRESS_CHEAT

  * The address is suspected of fake.

### -4035 CAPITAL_WITHDRAW_NOT_WHITE_ADDRESS

  * This address is not on the whitelist. Please join and try again.

### -4036 CAPITAL_WITHDRAW_NEW_ADDRESS

  * The new address needs to be withdrawn in {0} hours.

### -4037 CAPITAL_WITHDRAW_RESEND_EMAIL_FAIL

  * Re-sending Mail failed.

### -4038 CAPITAL_WITHDRAW_RESEND_EMAIL_TIME_OUT

  * Please try again in 5 minutes.

### -4039 CAPITAL_USER_EMPTY

  * The user does not exist.

### -4040 CAPITAL_NO_CHARGE

  * This address not charged.

### -4041 CAPITAL_MINUTE_TOO_SMALL

  * Please try again in one minute.

### -4042 CAPITAL_CHARGE_NOT_RESET

  * This asset cannot get deposit address again.

### -4043 CAPITAL_ADDRESS_TOO_MUCH

  * More than 100 recharge addresses were used in 24 hours.

### -4044 CAPITAL_BLACKLIST_COUNTRY_GET_ADDRESS

  * This is a blacklist country.

### -4045 CAPITAL_GET_ASSET_ERROR

  * Failure to acquire assets.

### -4046 CAPITAL_AGREEMENT_NOT_CONFIRMED

  * Agreement not confirmed.

### -4047 CAPITAL_DATE_INTERVAL_LIMIT

  * Time interval must be within 0-90 days

### -4060 CAPITAL_WITHDRAW_USER_ASSET_LOCK_DEPOSIT

  * As your deposit has not reached the required block confirmations, we have temporarily locked {0} asset

### -5001 ASSET_DRIBBLET_CONVERT_SWITCH_OFF

  * Don't allow transfer to micro assets.

### -5002 ASSET_ASSET_NOT_ENOUGH

  * You have insufficient balance.

### -5003 ASSET_USER_HAVE_NO_ASSET

  * You don't have this asset.

### -5004 USER_OUT_OF_TRANSFER_FLOAT

  * The residual balances have exceeded 0.001BTC, Please re-choose.
  * The residual balances of %s have exceeded 0.001BTC, Please re-choose.

### -5005 USER_ASSET_AMOUNT_IS_TOO_LOW

  * The residual balances of the BTC is too low
  * The residual balances of %s is too low, Please re-choose.

### -5006 USER_CAN_NOT_REQUEST_IN_24_HOURS

  * Only transfer once in 24 hours.

### -5007 AMOUNT_OVER_ZERO

  * Quantity must be greater than zero.

### -5008 ASSET_WITHDRAW_WITHDRAWING_NOT_ENOUGH

  * Insufficient amount of returnable assets.

### -5009 PRODUCT_NOT_EXIST

  * Product does not exist.

### -5010 TRANSFER_FAIL

  * Asset transfer fail.

### -5011 FUTURE_ACCT_NOT_EXIST

  * future account not exists.

### -5012 TRANSFER_PENDING

  * Asset transfer is in pending.

### -5021 PARENT_SUB_HAVE_NO_RELATION

  * This parent sub have no relation

### -5012 FUTURE_ACCT_OR_SUBRELATION_NOT_EXIST

  * future account or sub relation not exists.

## 6XXX - Savings Issues

### -6001 DAILY_PRODUCT_NOT_EXIST

  * Daily product not exists.

### -6003 DAILY_PRODUCT_NOT_ACCESSIBLE

  * Product not exist or you don't have permission

### -6004 DAILY_PRODUCT_NOT_PURCHASABLE

  * Product not in purchase status

### -6005 DAILY_LOWER_THAN_MIN_PURCHASE_LIMIT

  * Smaller than min purchase limit

### -6006 DAILY_REDEEM_AMOUNT_ERROR

  * Redeem amount error

### -6007 DAILY_REDEEM_TIME_ERROR

  * Not in redeem time

### -6008 DAILY_PRODUCT_NOT_REDEEMABLE

  * Product not in redeem status

### -6009 REQUEST_FREQUENCY_TOO_HIGH

  * Request frequency too high

### -6011 EXCEEDED_USER_PURCHASE_LIMIT

  * Exceeding the maximum num allowed to purchase per user

### -6012 BALANCE_NOT_ENOUGH

  * Balance not enough

### -6013 PURCHASING_FAILED

  * Purchasing failed

### -6014 UPDATE_FAILED

  * Exceed up-limit allowed to purchased

### -6015 EMPTY_REQUEST_BODY

  * Empty request body

### -6016 PARAMS_ERR

  * Parameter err

### -6017 NOT_IN_WHITELIST

  * Not in whitelist

### -6018 ASSET_NOT_ENOUGH

  * Asset not enough

### -6019 PENDING

  * Need confirm

### -6020 PROJECT_NOT_EXISTS

  * Project not exists

## 70xx - Futures

### -7001 FUTURES_BAD_DATE_RANGE

  * Date range is not supported.

### -7002 FUTURES_BAD_TYPE

  * Data request type is not supported.

## 20xxx - Futures/Spot Algo

### -20121

  * Invalid symbol.

### -20124

  * Invalid algo id or it has been completed.

### -20130

  * Invalid data sent for a parameter.

### -20132

  * The client algo id is duplicated.

### -20194

  * Duration is too short to execute all required quantity.

### -20195

  * The total size is too small.

### -20196

  * The total size is too large.

### -20198

  * Reach the max open orders allowed.

### -20204

  * The notional of USD is less or more than the limit.

## Filter failures

Error message| Description  
---|---  
"Filter failure: PRICE_FILTER"| `price` is too high, too low, and/or not following the tick size rule for the symbol.  
"Filter failure: PERCENT_PRICE"| `price` is X% too high or X% too low from the average weighted price over the last Y minutes.  
"Filter failure: PERCENT_PRICE_BY_SIDE"| `price` is X% too high or Y% too low from the `lastPrice` on that side (i.e. BUY/SELL)  
"Filter failure: LOT_SIZE"| `quantity` is too high, too low, and/or not following the step size rule for the symbol.  
"Filter failure: MIN_NOTIONAL"| `price` * `quantity` is too low to be a valid order for the symbol.  
"Filter failure: ICEBERG_PARTS"| `ICEBERG` order would break into too many parts; icebergQty is too small.  
"Filter failure: MARKET_LOT_SIZE"| `MARKET` order's `quantity` is too high, too low, and/or not following the step size rule for the symbol.  
"Filter failure: MAX_POSITION"| The account's position has reached the maximum defined limit.   
  
This is composed of the sum of the balance of the base asset, and the sum of the quantity of all open `BUY`orders.  
"Filter failure: MAX_NUM_ORDERS"| Account has too many open orders on the symbol.  
"Filter failure: MAX_NUM_ALGO_ORDERS"| Account has too many open stop loss and/or take profit orders on the symbol.  
"Filter failure: MAX_NUM_ICEBERG_ORDERS"| Account has too many open iceberg orders on the symbol.  
"Filter failure: TRAILING_DELTA"| `trailingDelta` is not within the defined range of the filter for that order type.  
"Filter failure: EXCHANGE_MAX_NUM_ORDERS"| Account has too many open orders on the exchange.  
"Filter failure: EXCHANGE_MAX_NUM_ALGO_ORDERS"| Account has too many open stop loss and/or take profit orders on the exchange.  
  
## 10xxx - Crypto Loans

### -10001 SYSTEM_MAINTENANCE

  * The system is under maintenance, please try again later.

### -10002 INVALID_INPUT

  * Invalid input parameters.

### -10005 NO_RECORDS

  * No records found.

### -10007 COIN_NOT_LOANABLE

  * This coin is not loanable.

### -10008 COIN_NOT_LOANABLE

  * This coin is not loanable

### -10009 COIN_NOT_COLLATERAL

  * This coin can not be used as collateral.

### -10010 COIN_NOT_COLLATERAL

  * This coin can not be used as collateral.

### -10011 INSUFFICIENT_ASSET

  * Insufficient spot assets.

### -10012 INVALID_AMOUNT

  * Invalid repayment amount.

### -10013 INSUFFICIENT_AMOUNT

  * Insufficient collateral amount.

### -10015 DEDUCTION_FAILED

  * Collateral deduction failed.

### -10016 LOAN_FAILED

  * Failed to provide loan.

### -10017 REPAY_EXCEED_DEBT

  * Repayment amount exceeds debt.

### -10018 INVALID_AMOUNT

  * Invalid repayment amount.

### -10019 CONFIG_NOT_EXIST

  * Configuration does not exists.

### -10020 UID_NOT_EXIST

  * User ID does not exist.

### -10021 ORDER_NOT_EXIST

  * Order does not exist.

### -10022 INVALID_AMOUNT

  * Invalid adjustment amount.

### -10023 ADJUST_LTV_FAILED

  * Failed to adjust LTV.

### -10024 ADJUST_LTV_NOT_SUPPORTED

  * LTV adjustment not supported.

### -10025 REPAY_FAILED

  * Repayment failed.

### -10026 INVALID_PARAMETER

  * Invalid parameter.

### -10028 INVALID_PARAMETER

  * Invalid parameter.

### -10029 AMOUNT_TOO_SMALL

  * Loan amount is too small.

### -10030 AMOUNT_TOO_LARGE

  * Loan amount is too much.

### -10031 QUOTA_REACHED

  * Individual loan quota reached.

### -10032 REPAY_NOT_AVAILABLE

  * Repayment is temporarily unavailable.

### -10034 REPAY_NOT_AVAILABLE

  * Repay with collateral is not available currently, please try to repay with borrowed coin.

### -10039 AMOUNT_TOO_SMALL

  * Repayment amount is too small.

### -10040 AMOUNT_TOO_LARGE

  * Repayment amount is too large.

### -10041 INSUFFICIENT_AMOUNT

  * Due to high demand, there are currently insufficient loanable assets for {0}. Please adjust your borrow amount or try again tomorrow.

### -10042 ASSET_NOT_SUPPORTED

  * asset %s is not supported

### -10043 ASSET_NOT_SUPPORTED

  * {0} borrowing is currently not supported.

### -10044 QUOTA_REACHED

  * Collateral amount has reached the limit. Please reduce your collateral amount or try with other collaterals.

### -10045 COLLTERAL_REPAY_NOT_SUPPORTED

  * The loan coin does not support collateral repayment. Please try again later.

### -10046 EXCEED_MAX_ADJUSTMENT

  * Collateral Adjustment exceeds the maximum limit. Please try again.

### -10047 REGION_NOT_SUPPORTED

  * This coin is currently not supported in your location due to local regulations.

## 13xxx - BLVT

### -13000 BLVT_FORBID_REDEEM

  * Redeption of the token is forbiden now

### -13001 BLVT_EXCEED_DAILY_LIMIT

  * Exceeds individual 24h redemption limit of the token

### -13002 BLVT_EXCEED_TOKEN_DAILY_LIMIT

  * Exceeds total 24h redemption limit of the token

### -13003 BLVT_FORBID_PURCHASE

  * Subscription of the token is forbiden now

### -13004 BLVT_EXCEED_DAILY_PURCHASE_LIMIT

  * Exceeds individual 24h subscription limit of the token

### -13005 BLVT_EXCEED_TOKEN_DAILY_PURCHASE_LIMIT

  * Exceeds total 24h subscription limit of the token

### -13006 BLVT_PURCHASE_LESS_MIN_AMOUNT

  * Subscription amount is too small

### -13007 BLVT_PURCHASE_AGREEMENT_NOT_SIGN

  * The Agreement is not signed

## 12xxx - Liquid Swap

### -12014 TOO MANY REQUESTS

  * More than 1 request in 2 seconds

## 18xxx - Binance Code

### -18002

  * The total amount of codes you created has exceeded the 24-hour limit, please try again after UTC 0

### -18003

  * Too many codes created in 24 hours, please try again after UTC 0

### -18004

  * Too many invalid redeem attempts in 24 hours, please try again after UTC 0

### -18005

  * Too many invalid verify attempts, please try later

### -18006

  * The amount is too small, please re-enter

### -18007

  * This token is not currently supported, please re-enter

## 21xxx - Portfolio Margin Account

### -21001 USER_IS_NOT_UNIACCOUNT

  * Request ID is not a Portfolio Margin Account.

### -21002 UNI_ACCOUNT_CANT_TRANSFER_FUTURE

  * Portfolio Margin Account doesn't support transfer from margin to futures.

### -21003 NET_ASSET_MUST_LTE_RATIO

  * Fail to retrieve margin assets.

### -21004 USER_NO_LIABILITY

  * User doesn’t have portfolio margin bankruptcy loan

### -21005 NO_ENOUGH_ASSET

  * User’s spot wallet doesn’t have enough BUSD to repay portfolio margin bankruptcy loan

### -21006 HAD_IN_PROCESS_REPAY

  * User had portfolio margin bankruptcy loan repayment in process

### -21007 IN_FORCE_LIQUIDATION

  * User failed to repay portfolio margin bankruptcy loan since liquidation was in process

## Order Rejection Issues

Error messages like these are indicated when the error is coming specifically from the matching engine:

  * `-1010 ERROR_MSG_RECEIVED`
  * `-2010 NEW_ORDER_REJECTED`
  * `-2011 CANCEL_REJECTED`

The following messages which will indicate the specific error:

Error message| Description  
---|---  
"Unknown order sent."| The order (by either `orderId`, `clientOrderId`, `origClientOrderId`) could not be found.  
"Duplicate order sent."| The `clientOrderId` is already in use.  
"Market is closed."| The symbol is not trading.  
"Account has insufficient balance for requested action."| Not enough funds to complete the action.  
"Market orders are not supported for this symbol."| `MARKET` is not enabled on the symbol.  
"Iceberg orders are not supported for this symbol."| `icebergQty` is not enabled on the symbol  
"Stop loss orders are not supported for this symbol."| `STOP_LOSS` is not enabled on the symbol  
"Stop loss limit orders are not supported for this symbol."| `STOP_LOSS_LIMIT` is not enabled on the symbol  
"Take profit orders are not supported for this symbol."| `TAKE_PROFIT` is not enabled on the symbol  
"Take profit limit orders are not supported for this symbol."| `TAKE_PROFIT_LIMIT` is not enabled on the symbol  
"Price * QTY is zero or less."| `price` * `quantity` is too low  
"IcebergQty exceeds QTY."| `icebergQty` must be less than the order quantity  
"This action is disabled on this account."| Contact customer support; some actions have been disabled on the account.  
"This account may not place or cancel orders."| Contact customer support; the account has trading ability disabled.  
"Unsupported order combination"| The `orderType`, `timeInForce`, `stopPrice`, and/or `icebergQty` combination isn't allowed.  
"Order would trigger immediately."| The order's stop price is not valid when compared to the last traded price.  
"Cancel order is invalid. Check origClientOrderId and orderId."| No `origClientOrderId` or `orderId` was sent in.  
"Order would immediately match and take."| `LIMIT_MAKER` order type would immediately match and trade, and not be a pure maker order.  
"The relationship of the prices for the orders is not correct."| The prices set in the `OCO` is breaking the Price rules.   
  
The rules are:   
  
`SELL Orders`: Limit Price > Last Price > Stop Price   
  
`BUY Orders`: Limit Price < Last Price < Stop Price  
"OCO orders are not supported for this symbol"| `OCO` is not enabled on the symbol.  
"Quote order qty market orders are not support for this symbol."| `MARKET` orders using the parameter `quoteOrderQty` are not enabled on this symbol.  
"Trailing stop orders are not supported for this symbol."| Orders using `trailingDelta` are not enabled on the symbol.  
"Order cancel-replace is not supported for this symbol."| `POST /api/v3/order/cancelReplace` (REST API) or `order.cancelReplace` (WebSocket API) is on enabled the symbol.  
"This symbol is not permitted for this account."| Account and symbol do not have the same permissions. (e.g. `SPOT`, `MARGIN`, etc)  
"This symbol is restricted for this account."| Account is unable to trade on that symbol. (e.g. An `ISOLATED_MARGIN` account cannot place `SPOT` orders.)  
"Order was not canceled due to cancel restrictions."| Either `cancelRestrictions` was set to `ONLY_NEW` but the order status was not `NEW`   
or   
`cancelRestrictions` was set to `ONLY_PARTIALLY_FILLED` but the order status was not `PARTIALLY_FILLED`.  
  
## Errors regarding POST /api/v3/order/cancelReplace

### -2021 Order cancel-replace partially failed

This code is sent when either the cancellation of the order failed or the new order placement failed but not both.

### -2022 Order cancel-replace failed.

This code is sent when both the cancellation of the order failed and the new order placement failed.

  * [10xx - General Server or Network issues](</docs/derivatives/futures-data/error-code#10xx---general-server-or-network-issues>)
    * [-1000 UNKNOWN](</docs/derivatives/futures-data/error-code#-1000-unknown>)
    * [-1001 DISCONNECTED](</docs/derivatives/futures-data/error-code#-1001-disconnected>)
    * [-1002 UNAUTHORIZED](</docs/derivatives/futures-data/error-code#-1002-unauthorized>)
    * [-1003 TOO_MANY_REQUESTS](</docs/derivatives/futures-data/error-code#-1003-too_many_requests>)
    * [-1004 SERVER_BUSY](</docs/derivatives/futures-data/error-code#-1004-server_busy>)
    * [-1006 UNEXPECTED_RESP](</docs/derivatives/futures-data/error-code#-1006-unexpected_resp>)
    * [-1007 TIMEOUT](</docs/derivatives/futures-data/error-code#-1007-timeout>)
    * [-1008 SERVER_BUSY](</docs/derivatives/futures-data/error-code#-1008-server_busy>)
    * [-1014 UNKNOWN_ORDER_COMPOSITION](</docs/derivatives/futures-data/error-code#-1014-unknown_order_composition>)
    * [-1015 TOO_MANY_ORDERS](</docs/derivatives/futures-data/error-code#-1015-too_many_orders>)
    * [-1016 SERVICE_SHUTTING_DOWN](</docs/derivatives/futures-data/error-code#-1016-service_shutting_down>)
    * [-1020 UNSUPPORTED_OPERATION](</docs/derivatives/futures-data/error-code#-1020-unsupported_operation>)
    * [-1021 INVALID_TIMESTAMP](</docs/derivatives/futures-data/error-code#-1021-invalid_timestamp>)
    * [-1022 INVALID_SIGNATURE](</docs/derivatives/futures-data/error-code#-1022-invalid_signature>)
    * [-1099 Not found, authenticated, or authorized](</docs/derivatives/futures-data/error-code#-1099-not-found-authenticated-or-authorized>)
  * [11xx - 2xxx Request issues](</docs/derivatives/futures-data/error-code#11xx---2xxx-request-issues>)
    * [-1100 ILLEGAL_CHARS](</docs/derivatives/futures-data/error-code#-1100-illegal_chars>)
    * [-1101 TOO_MANY_PARAMETERS](</docs/derivatives/futures-data/error-code#-1101-too_many_parameters>)
    * [-1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED](</docs/derivatives/futures-data/error-code#-1102-mandatory_param_empty_or_malformed>)
    * [-1103 UNKNOWN_PARAM](</docs/derivatives/futures-data/error-code#-1103-unknown_param>)
    * [-1104 UNREAD_PARAMETERS](</docs/derivatives/futures-data/error-code#-1104-unread_parameters>)
    * [-1105 PARAM_EMPTY](</docs/derivatives/futures-data/error-code#-1105-param_empty>)
    * [-1106 PARAM_NOT_REQUIRED](</docs/derivatives/futures-data/error-code#-1106-param_not_required>)
    * [-1111 BAD_PRECISION](</docs/derivatives/futures-data/error-code#-1111-bad_precision>)
    * [-1112 NO_DEPTH](</docs/derivatives/futures-data/error-code#-1112-no_depth>)
    * [-1114 TIF_NOT_REQUIRED](</docs/derivatives/futures-data/error-code#-1114-tif_not_required>)
    * [-1115 INVALID_TIF](</docs/derivatives/futures-data/error-code#-1115-invalid_tif>)
    * [-1116 INVALID_ORDER_TYPE](</docs/derivatives/futures-data/error-code#-1116-invalid_order_type>)
    * [-1117 INVALID_SIDE](</docs/derivatives/futures-data/error-code#-1117-invalid_side>)
    * [-1118 EMPTY_NEW_CL_ORD_ID](</docs/derivatives/futures-data/error-code#-1118-empty_new_cl_ord_id>)
    * [-1119 EMPTY_ORG_CL_ORD_ID](</docs/derivatives/futures-data/error-code#-1119-empty_org_cl_ord_id>)
    * [-1120 BAD_INTERVAL](</docs/derivatives/futures-data/error-code#-1120-bad_interval>)
    * [-1121 BAD_SYMBOL](</docs/derivatives/futures-data/error-code#-1121-bad_symbol>)
    * [-1125 INVALID_LISTEN_KEY](</docs/derivatives/futures-data/error-code#-1125-invalid_listen_key>)
    * [-1127 MORE_THAN_XX_HOURS](</docs/derivatives/futures-data/error-code#-1127-more_than_xx_hours>)
    * [-1128 OPTIONAL_PARAMS_BAD_COMBO](</docs/derivatives/futures-data/error-code#-1128-optional_params_bad_combo>)
    * [-1130 INVALID_PARAMETER](</docs/derivatives/futures-data/error-code#-1130-invalid_parameter>)
    * [-1131 BAD_RECV_WINDOW](</docs/derivatives/futures-data/error-code#-1131-bad_recv_window>)
    * [-1134 BAD_STRATEGY_TYPE](</docs/derivatives/futures-data/error-code#-1134-bad_strategy_type>)
    * [-2010 NEW_ORDER_REJECTED](</docs/derivatives/futures-data/error-code#-2010-new_order_rejected>)
    * [-2011 CANCEL_REJECTED](</docs/derivatives/futures-data/error-code#-2011-cancel_rejected>)
    * [-2013 NO_SUCH_ORDER](</docs/derivatives/futures-data/error-code#-2013-no_such_order>)
    * [-2014 BAD_API_KEY_FMT](</docs/derivatives/futures-data/error-code#-2014-bad_api_key_fmt>)
    * [-2015 REJECTED_MBX_KEY](</docs/derivatives/futures-data/error-code#-2015-rejected_mbx_key>)
    * [-2016 NO_TRADING_WINDOW](</docs/derivatives/futures-data/error-code#-2016-no_trading_window>)
  * [3xxx-5xxx SAPI-specific issues](</docs/derivatives/futures-data/error-code#3xxx-5xxx-sapi-specific-issues>)
    * [-3000 INNER_FAILURE](</docs/derivatives/futures-data/error-code#-3000-inner_failure>)
    * [-3001 NEED_ENABLE_2FA](</docs/derivatives/futures-data/error-code#-3001-need_enable_2fa>)
    * [-3002 ASSET_DEFICIENCY](</docs/derivatives/futures-data/error-code#-3002-asset_deficiency>)
    * [-3003 NO_OPENED_MARGIN_ACCOUNT](</docs/derivatives/futures-data/error-code#-3003-no_opened_margin_account>)
    * [-3004 TRADE_NOT_ALLOWED](</docs/derivatives/futures-data/error-code#-3004-trade_not_allowed>)
    * [-3005 TRANSFER_OUT_NOT_ALLOWED](</docs/derivatives/futures-data/error-code#-3005-transfer_out_not_allowed>)
    * [-3006 EXCEED_MAX_BORROWABLE](</docs/derivatives/futures-data/error-code#-3006-exceed_max_borrowable>)
    * [-3007 HAS_PENDING_TRANSACTION](</docs/derivatives/futures-data/error-code#-3007-has_pending_transaction>)
    * [-3008 BORROW_NOT_ALLOWED](</docs/derivatives/futures-data/error-code#-3008-borrow_not_allowed>)
    * [-3009 ASSET_NOT_MORTGAGEABLE](</docs/derivatives/futures-data/error-code#-3009-asset_not_mortgageable>)
    * [-3010 REPAY_NOT_ALLOWED](</docs/derivatives/futures-data/error-code#-3010-repay_not_allowed>)
    * [-3011 BAD_DATE_RANGE](</docs/derivatives/futures-data/error-code#-3011-bad_date_range>)
    * [-3012 ASSET_ADMIN_BAN_BORROW](</docs/derivatives/futures-data/error-code#-3012-asset_admin_ban_borrow>)
    * [-3013 LT_MIN_BORROWABLE](</docs/derivatives/futures-data/error-code#-3013-lt_min_borrowable>)
    * [-3014 ACCOUNT_BAN_BORROW](</docs/derivatives/futures-data/error-code#-3014-account_ban_borrow>)
    * [-3015 REPAY_EXCEED_LIABILITY](</docs/derivatives/futures-data/error-code#-3015-repay_exceed_liability>)
    * [-3016 LT_MIN_REPAY](</docs/derivatives/futures-data/error-code#-3016-lt_min_repay>)
    * [-3017 ASSET_ADMIN_BAN_MORTGAGE](</docs/derivatives/futures-data/error-code#-3017-asset_admin_ban_mortgage>)
    * [-3018 ACCOUNT_BAN_MORTGAGE](</docs/derivatives/futures-data/error-code#-3018-account_ban_mortgage>)
    * [-3019 ACCOUNT_BAN_ROLLOUT](</docs/derivatives/futures-data/error-code#-3019-account_ban_rollout>)
    * [-3020 EXCEED_MAX_ROLLOUT](</docs/derivatives/futures-data/error-code#-3020-exceed_max_rollout>)
    * [-3021 PAIR_ADMIN_BAN_TRADE](</docs/derivatives/futures-data/error-code#-3021-pair_admin_ban_trade>)
    * [-3022 ACCOUNT_BAN_TRADE](</docs/derivatives/futures-data/error-code#-3022-account_ban_trade>)
    * [-3023 WARNING_MARGIN_LEVEL](</docs/derivatives/futures-data/error-code#-3023-warning_margin_level>)
    * [-3024 FEW_LIABILITY_LEFT](</docs/derivatives/futures-data/error-code#-3024-few_liability_left>)
    * [-3025 INVALID_EFFECTIVE_TIME](</docs/derivatives/futures-data/error-code#-3025-invalid_effective_time>)
    * [-3026 VALIDATION_FAILED](</docs/derivatives/futures-data/error-code#-3026-validation_failed>)
    * [-3027 NOT_VALID_MARGIN_ASSET](</docs/derivatives/futures-data/error-code#-3027-not_valid_margin_asset>)
    * [-3028 NOT_VALID_MARGIN_PAIR](</docs/derivatives/futures-data/error-code#-3028-not_valid_margin_pair>)
    * [-3029 TRANSFER_FAILED](</docs/derivatives/futures-data/error-code#-3029-transfer_failed>)
    * [-3036 ACCOUNT_BAN_REPAY](</docs/derivatives/futures-data/error-code#-3036-account_ban_repay>)
    * [-3037 PNL_CLEARING](</docs/derivatives/futures-data/error-code#-3037-pnl_clearing>)
    * [-3038 LISTEN_KEY_NOT_FOUND](</docs/derivatives/futures-data/error-code#-3038-listen_key_not_found>)
    * [-3041 BALANCE_NOT_CLEARED](</docs/derivatives/futures-data/error-code#-3041-balance_not_cleared>)
    * [-3042 PRICE_INDEX_NOT_FOUND](</docs/derivatives/futures-data/error-code#-3042-price_index_not_found>)
    * [-3043 TRANSFER_IN_NOT_ALLOWED](</docs/derivatives/futures-data/error-code#-3043-transfer_in_not_allowed>)
    * [-3044 SYSTEM_BUSY](</docs/derivatives/futures-data/error-code#-3044-system_busy>)
    * [-3045 SYSTEM](</docs/derivatives/futures-data/error-code#-3045-system>)
    * [-3999 NOT_WHITELIST_USER](</docs/derivatives/futures-data/error-code#-3999-not_whitelist_user>)
    * [-4001 CAPITAL_INVALID](</docs/derivatives/futures-data/error-code#-4001-capital_invalid>)
    * [-4002 CAPITAL_IG](</docs/derivatives/futures-data/error-code#-4002-capital_ig>)
    * [-4003 CAPITAL_IEV](</docs/derivatives/futures-data/error-code#-4003-capital_iev>)
    * [-4004 CAPITAL_UA](</docs/derivatives/futures-data/error-code#-4004-capital_ua>)
    * [-4005 CAPAITAL_TOO_MANY_REQUEST](</docs/derivatives/futures-data/error-code#-4005-capaital_too_many_request>)
    * [-4006 CAPITAL_ONLY_SUPPORT_PRIMARY_ACCOUNT](</docs/derivatives/futures-data/error-code#-4006-capital_only_support_primary_account>)
    * [-4007 CAPITAL_ADDRESS_VERIFICATION_NOT_PASS](</docs/derivatives/futures-data/error-code#-4007-capital_address_verification_not_pass>)
    * [-4008 CAPITAL_ADDRESS_TAG_VERIFICATION_NOT_PASS](</docs/derivatives/futures-data/error-code#-4008-capital_address_tag_verification_not_pass>)
    * [-4010 CAPITAL_WHITELIST_EMAIL_CONFIRM](</docs/derivatives/futures-data/error-code#-4010-capital_whitelist_email_confirm>)
    * [-4011 CAPITAL_WHITELIST_EMAIL_EXPIRED](</docs/derivatives/futures-data/error-code#-4011-capital_whitelist_email_expired>)
    * [-4012 CAPITAL_WHITELIST_CLOSE](</docs/derivatives/futures-data/error-code#-4012-capital_whitelist_close>)
    * [-4013 CAPITAL_WITHDRAW_2FA_VERIFY](</docs/derivatives/futures-data/error-code#-4013-capital_withdraw_2fa_verify>)
    * [-4014 CAPITAL_WITHDRAW_LOGIN_DELAY](</docs/derivatives/futures-data/error-code#-4014-capital_withdraw_login_delay>)
    * [-4015 CAPITAL_WITHDRAW_RESTRICTED_MINUTE](</docs/derivatives/futures-data/error-code#-4015-capital_withdraw_restricted_minute>)
    * [-4016 CAPITAL_WITHDRAW_RESTRICTED_PASSWORD](</docs/derivatives/futures-data/error-code#-4016-capital_withdraw_restricted_password>)
    * [-4017 CAPITAL_WITHDRAW_RESTRICTED_UNBIND_2FA](</docs/derivatives/futures-data/error-code#-4017-capital_withdraw_restricted_unbind_2fa>)
    * [-4018 CAPITAL_WITHDRAW_ASSET_NOT_EXIST](</docs/derivatives/futures-data/error-code#-4018-capital_withdraw_asset_not_exist>)
    * [-4019 CAPITAL_WITHDRAW_ASSET_PROHIBIT](</docs/derivatives/futures-data/error-code#-4019-capital_withdraw_asset_prohibit>)
    * [-4021 CAPITAL_WITHDRAW_AMOUNT_MULTIPLE](</docs/derivatives/futures-data/error-code#-4021-capital_withdraw_amount_multiple>)
    * [-4022 CAPITAL_WITHDRAW_MIN_AMOUNT](</docs/derivatives/futures-data/error-code#-4022-capital_withdraw_min_amount>)
    * [-4023 CAPITAL_WITHDRAW_MAX_AMOUNT](</docs/derivatives/futures-data/error-code#-4023-capital_withdraw_max_amount>)
    * [-4024 CAPITAL_WITHDRAW_USER_NO_ASSET](</docs/derivatives/futures-data/error-code#-4024-capital_withdraw_user_no_asset>)
    * [-4025 CAPITAL_WITHDRAW_USER_ASSET_LESS_THAN_ZERO](</docs/derivatives/futures-data/error-code#-4025-capital_withdraw_user_asset_less_than_zero>)
    * [-4026 CAPITAL_WITHDRAW_USER_ASSET_NOT_ENOUGH](</docs/derivatives/futures-data/error-code#-4026-capital_withdraw_user_asset_not_enough>)
    * [-4027 CAPITAL_WITHDRAW_GET_TRAN_ID_FAILURE](</docs/derivatives/futures-data/error-code#-4027-capital_withdraw_get_tran_id_failure>)
    * [-4028 CAPITAL_WITHDRAW_MORE_THAN_FEE](</docs/derivatives/futures-data/error-code#-4028-capital_withdraw_more_than_fee>)
    * [-4029 CAPITAL_WITHDRAW_NOT_EXIST](</docs/derivatives/futures-data/error-code#-4029-capital_withdraw_not_exist>)
    * [-4030 CAPITAL_WITHDRAW_CONFIRM_SUCCESS](</docs/derivatives/futures-data/error-code#-4030-capital_withdraw_confirm_success>)
    * [-4031 CAPITAL_WITHDRAW_CANCEL_FAILURE](</docs/derivatives/futures-data/error-code#-4031-capital_withdraw_cancel_failure>)
    * [-4032 CAPITAL_WITHDRAW_CHECKSUM_VERIFY_FAILURE](</docs/derivatives/futures-data/error-code#-4032-capital_withdraw_checksum_verify_failure>)
    * [-4033 CAPITAL_WITHDRAW_ILLEGAL_ADDRESS](</docs/derivatives/futures-data/error-code#-4033-capital_withdraw_illegal_address>)
    * [-4034 CAPITAL_WITHDRAW_ADDRESS_CHEAT](</docs/derivatives/futures-data/error-code#-4034-capital_withdraw_address_cheat>)
    * [-4035 CAPITAL_WITHDRAW_NOT_WHITE_ADDRESS](</docs/derivatives/futures-data/error-code#-4035-capital_withdraw_not_white_address>)
    * [-4036 CAPITAL_WITHDRAW_NEW_ADDRESS](</docs/derivatives/futures-data/error-code#-4036-capital_withdraw_new_address>)
    * [-4037 CAPITAL_WITHDRAW_RESEND_EMAIL_FAIL](</docs/derivatives/futures-data/error-code#-4037-capital_withdraw_resend_email_fail>)
    * [-4038 CAPITAL_WITHDRAW_RESEND_EMAIL_TIME_OUT](</docs/derivatives/futures-data/error-code#-4038-capital_withdraw_resend_email_time_out>)
    * [-4039 CAPITAL_USER_EMPTY](</docs/derivatives/futures-data/error-code#-4039-capital_user_empty>)
    * [-4040 CAPITAL_NO_CHARGE](</docs/derivatives/futures-data/error-code#-4040-capital_no_charge>)
    * [-4041 CAPITAL_MINUTE_TOO_SMALL](</docs/derivatives/futures-data/error-code#-4041-capital_minute_too_small>)
    * [-4042 CAPITAL_CHARGE_NOT_RESET](</docs/derivatives/futures-data/error-code#-4042-capital_charge_not_reset>)
    * [-4043 CAPITAL_ADDRESS_TOO_MUCH](</docs/derivatives/futures-data/error-code#-4043-capital_address_too_much>)
    * [-4044 CAPITAL_BLACKLIST_COUNTRY_GET_ADDRESS](</docs/derivatives/futures-data/error-code#-4044-capital_blacklist_country_get_address>)
    * [-4045 CAPITAL_GET_ASSET_ERROR](</docs/derivatives/futures-data/error-code#-4045-capital_get_asset_error>)
    * [-4046 CAPITAL_AGREEMENT_NOT_CONFIRMED](</docs/derivatives/futures-data/error-code#-4046-capital_agreement_not_confirmed>)
    * [-4047 CAPITAL_DATE_INTERVAL_LIMIT](</docs/derivatives/futures-data/error-code#-4047-capital_date_interval_limit>)
    * [-4060 CAPITAL_WITHDRAW_USER_ASSET_LOCK_DEPOSIT](</docs/derivatives/futures-data/error-code#-4060-capital_withdraw_user_asset_lock_deposit>)
    * [-5001 ASSET_DRIBBLET_CONVERT_SWITCH_OFF](</docs/derivatives/futures-data/error-code#-5001-asset_dribblet_convert_switch_off>)
    * [-5002 ASSET_ASSET_NOT_ENOUGH](</docs/derivatives/futures-data/error-code#-5002-asset_asset_not_enough>)
    * [-5003 ASSET_USER_HAVE_NO_ASSET](</docs/derivatives/futures-data/error-code#-5003-asset_user_have_no_asset>)
    * [-5004 USER_OUT_OF_TRANSFER_FLOAT](</docs/derivatives/futures-data/error-code#-5004-user_out_of_transfer_float>)
    * [-5005 USER_ASSET_AMOUNT_IS_TOO_LOW](</docs/derivatives/futures-data/error-code#-5005-user_asset_amount_is_too_low>)
    * [-5006 USER_CAN_NOT_REQUEST_IN_24_HOURS](</docs/derivatives/futures-data/error-code#-5006-user_can_not_request_in_24_hours>)
    * [-5007 AMOUNT_OVER_ZERO](</docs/derivatives/futures-data/error-code#-5007-amount_over_zero>)
    * [-5008 ASSET_WITHDRAW_WITHDRAWING_NOT_ENOUGH](</docs/derivatives/futures-data/error-code#-5008-asset_withdraw_withdrawing_not_enough>)
    * [-5009 PRODUCT_NOT_EXIST](</docs/derivatives/futures-data/error-code#-5009-product_not_exist>)
    * [-5010 TRANSFER_FAIL](</docs/derivatives/futures-data/error-code#-5010-transfer_fail>)
    * [-5011 FUTURE_ACCT_NOT_EXIST](</docs/derivatives/futures-data/error-code#-5011-future_acct_not_exist>)
    * [-5012 TRANSFER_PENDING](</docs/derivatives/futures-data/error-code#-5012-transfer_pending>)
    * [-5021 PARENT_SUB_HAVE_NO_RELATION](</docs/derivatives/futures-data/error-code#-5021-parent_sub_have_no_relation>)
    * [-5012 FUTURE_ACCT_OR_SUBRELATION_NOT_EXIST](</docs/derivatives/futures-data/error-code#-5012-future_acct_or_subrelation_not_exist>)
  * [6XXX - Savings Issues](</docs/derivatives/futures-data/error-code#6xxx---savings-issues>)
    * [-6001 DAILY_PRODUCT_NOT_EXIST](</docs/derivatives/futures-data/error-code#-6001-daily_product_not_exist>)
    * [-6003 DAILY_PRODUCT_NOT_ACCESSIBLE](</docs/derivatives/futures-data/error-code#-6003-daily_product_not_accessible>)
    * [-6004 DAILY_PRODUCT_NOT_PURCHASABLE](</docs/derivatives/futures-data/error-code#-6004-daily_product_not_purchasable>)
    * [-6005 DAILY_LOWER_THAN_MIN_PURCHASE_LIMIT](</docs/derivatives/futures-data/error-code#-6005-daily_lower_than_min_purchase_limit>)
    * [-6006 DAILY_REDEEM_AMOUNT_ERROR](</docs/derivatives/futures-data/error-code#-6006-daily_redeem_amount_error>)
    * [-6007 DAILY_REDEEM_TIME_ERROR](</docs/derivatives/futures-data/error-code#-6007-daily_redeem_time_error>)
    * [-6008 DAILY_PRODUCT_NOT_REDEEMABLE](</docs/derivatives/futures-data/error-code#-6008-daily_product_not_redeemable>)
    * [-6009 REQUEST_FREQUENCY_TOO_HIGH](</docs/derivatives/futures-data/error-code#-6009-request_frequency_too_high>)
    * [-6011 EXCEEDED_USER_PURCHASE_LIMIT](</docs/derivatives/futures-data/error-code#-6011-exceeded_user_purchase_limit>)
    * [-6012 BALANCE_NOT_ENOUGH](</docs/derivatives/futures-data/error-code#-6012-balance_not_enough>)
    * [-6013 PURCHASING_FAILED](</docs/derivatives/futures-data/error-code#-6013-purchasing_failed>)
    * [-6014 UPDATE_FAILED](</docs/derivatives/futures-data/error-code#-6014-update_failed>)
    * [-6015 EMPTY_REQUEST_BODY](</docs/derivatives/futures-data/error-code#-6015-empty_request_body>)
    * [-6016 PARAMS_ERR](</docs/derivatives/futures-data/error-code#-6016-params_err>)
    * [-6017 NOT_IN_WHITELIST](</docs/derivatives/futures-data/error-code#-6017-not_in_whitelist>)
    * [-6018 ASSET_NOT_ENOUGH](</docs/derivatives/futures-data/error-code#-6018-asset_not_enough>)
    * [-6019 PENDING](</docs/derivatives/futures-data/error-code#-6019-pending>)
    * [-6020 PROJECT_NOT_EXISTS](</docs/derivatives/futures-data/error-code#-6020-project_not_exists>)
  * [70xx - Futures](</docs/derivatives/futures-data/error-code#70xx---futures>)
    * [-7001 FUTURES_BAD_DATE_RANGE](</docs/derivatives/futures-data/error-code#-7001-futures_bad_date_range>)
    * [-7002 FUTURES_BAD_TYPE](</docs/derivatives/futures-data/error-code#-7002-futures_bad_type>)
  * [20xxx - Futures/Spot Algo](</docs/derivatives/futures-data/error-code#20xxx---futuresspot-algo>)
    * [-20121](</docs/derivatives/futures-data/error-code#-20121>)
    * [-20124](</docs/derivatives/futures-data/error-code#-20124>)
    * [-20130](</docs/derivatives/futures-data/error-code#-20130>)
    * [-20132](</docs/derivatives/futures-data/error-code#-20132>)
    * [-20194](</docs/derivatives/futures-data/error-code#-20194>)
    * [-20195](</docs/derivatives/futures-data/error-code#-20195>)
    * [-20196](</docs/derivatives/futures-data/error-code#-20196>)
    * [-20198](</docs/derivatives/futures-data/error-code#-20198>)
    * [-20204](</docs/derivatives/futures-data/error-code#-20204>)
  * [Filter failures](</docs/derivatives/futures-data/error-code#filter-failures>)
  * [10xxx - Crypto Loans](</docs/derivatives/futures-data/error-code#10xxx---crypto-loans>)
    * [-10001 SYSTEM_MAINTENANCE](</docs/derivatives/futures-data/error-code#-10001-system_maintenance>)
    * [-10002 INVALID_INPUT](</docs/derivatives/futures-data/error-code#-10002-invalid_input>)
    * [-10005 NO_RECORDS](</docs/derivatives/futures-data/error-code#-10005-no_records>)
    * [-10007 COIN_NOT_LOANABLE](</docs/derivatives/futures-data/error-code#-10007-coin_not_loanable>)
    * [-10008 COIN_NOT_LOANABLE](</docs/derivatives/futures-data/error-code#-10008-coin_not_loanable>)
    * [-10009 COIN_NOT_COLLATERAL](</docs/derivatives/futures-data/error-code#-10009-coin_not_collateral>)
    * [-10010 COIN_NOT_COLLATERAL](</docs/derivatives/futures-data/error-code#-10010-coin_not_collateral>)
    * [-10011 INSUFFICIENT_ASSET](</docs/derivatives/futures-data/error-code#-10011-insufficient_asset>)
    * [-10012 INVALID_AMOUNT](</docs/derivatives/futures-data/error-code#-10012-invalid_amount>)
    * [-10013 INSUFFICIENT_AMOUNT](</docs/derivatives/futures-data/error-code#-10013-insufficient_amount>)
    * [-10015 DEDUCTION_FAILED](</docs/derivatives/futures-data/error-code#-10015-deduction_failed>)
    * [-10016 LOAN_FAILED](</docs/derivatives/futures-data/error-code#-10016-loan_failed>)
    * [-10017 REPAY_EXCEED_DEBT](</docs/derivatives/futures-data/error-code#-10017-repay_exceed_debt>)
    * [-10018 INVALID_AMOUNT](</docs/derivatives/futures-data/error-code#-10018-invalid_amount>)
    * [-10019 CONFIG_NOT_EXIST](</docs/derivatives/futures-data/error-code#-10019-config_not_exist>)
    * [-10020 UID_NOT_EXIST](</docs/derivatives/futures-data/error-code#-10020-uid_not_exist>)
    * [-10021 ORDER_NOT_EXIST](</docs/derivatives/futures-data/error-code#-10021-order_not_exist>)
    * [-10022 INVALID_AMOUNT](</docs/derivatives/futures-data/error-code#-10022-invalid_amount>)
    * [-10023 ADJUST_LTV_FAILED](</docs/derivatives/futures-data/error-code#-10023-adjust_ltv_failed>)
    * [-10024 ADJUST_LTV_NOT_SUPPORTED](</docs/derivatives/futures-data/error-code#-10024-adjust_ltv_not_supported>)
    * [-10025 REPAY_FAILED](</docs/derivatives/futures-data/error-code#-10025-repay_failed>)
    * [-10026 INVALID_PARAMETER](</docs/derivatives/futures-data/error-code#-10026-invalid_parameter>)
    * [-10028 INVALID_PARAMETER](</docs/derivatives/futures-data/error-code#-10028-invalid_parameter>)
    * [-10029 AMOUNT_TOO_SMALL](</docs/derivatives/futures-data/error-code#-10029-amount_too_small>)
    * [-10030 AMOUNT_TOO_LARGE](</docs/derivatives/futures-data/error-code#-10030-amount_too_large>)
    * [-10031 QUOTA_REACHED](</docs/derivatives/futures-data/error-code#-10031-quota_reached>)
    * [-10032 REPAY_NOT_AVAILABLE](</docs/derivatives/futures-data/error-code#-10032-repay_not_available>)
    * [-10034 REPAY_NOT_AVAILABLE](</docs/derivatives/futures-data/error-code#-10034-repay_not_available>)
    * [-10039 AMOUNT_TOO_SMALL](</docs/derivatives/futures-data/error-code#-10039-amount_too_small>)
    * [-10040 AMOUNT_TOO_LARGE](</docs/derivatives/futures-data/error-code#-10040-amount_too_large>)
    * [-10041 INSUFFICIENT_AMOUNT](</docs/derivatives/futures-data/error-code#-10041-insufficient_amount>)
    * [-10042 ASSET_NOT_SUPPORTED](</docs/derivatives/futures-data/error-code#-10042-asset_not_supported>)
    * [-10043 ASSET_NOT_SUPPORTED](</docs/derivatives/futures-data/error-code#-10043-asset_not_supported>)
    * [-10044 QUOTA_REACHED](</docs/derivatives/futures-data/error-code#-10044-quota_reached>)
    * [-10045 COLLTERAL_REPAY_NOT_SUPPORTED](</docs/derivatives/futures-data/error-code#-10045-collteral_repay_not_supported>)
    * [-10046 EXCEED_MAX_ADJUSTMENT](</docs/derivatives/futures-data/error-code#-10046-exceed_max_adjustment>)
    * [-10047 REGION_NOT_SUPPORTED](</docs/derivatives/futures-data/error-code#-10047-region_not_supported>)
  * [13xxx - BLVT](</docs/derivatives/futures-data/error-code#13xxx---blvt>)
    * [-13000 BLVT_FORBID_REDEEM](</docs/derivatives/futures-data/error-code#-13000-blvt_forbid_redeem>)
    * [-13001 BLVT_EXCEED_DAILY_LIMIT](</docs/derivatives/futures-data/error-code#-13001-blvt_exceed_daily_limit>)
    * [-13002 BLVT_EXCEED_TOKEN_DAILY_LIMIT](</docs/derivatives/futures-data/error-code#-13002-blvt_exceed_token_daily_limit>)
    * [-13003 BLVT_FORBID_PURCHASE](</docs/derivatives/futures-data/error-code#-13003-blvt_forbid_purchase>)
    * [-13004 BLVT_EXCEED_DAILY_PURCHASE_LIMIT](</docs/derivatives/futures-data/error-code#-13004-blvt_exceed_daily_purchase_limit>)
    * [-13005 BLVT_EXCEED_TOKEN_DAILY_PURCHASE_LIMIT](</docs/derivatives/futures-data/error-code#-13005-blvt_exceed_token_daily_purchase_limit>)
    * [-13006 BLVT_PURCHASE_LESS_MIN_AMOUNT](</docs/derivatives/futures-data/error-code#-13006-blvt_purchase_less_min_amount>)
    * [-13007 BLVT_PURCHASE_AGREEMENT_NOT_SIGN](</docs/derivatives/futures-data/error-code#-13007-blvt_purchase_agreement_not_sign>)
  * [12xxx - Liquid Swap](</docs/derivatives/futures-data/error-code#12xxx---liquid-swap>)
    * [-12014 TOO MANY REQUESTS](</docs/derivatives/futures-data/error-code#-12014-too-many-requests>)
  * [18xxx - Binance Code](</docs/derivatives/futures-data/error-code#18xxx---binance-code>)
    * [-18002](</docs/derivatives/futures-data/error-code#-18002>)
    * [-18003](</docs/derivatives/futures-data/error-code#-18003>)
    * [-18004](</docs/derivatives/futures-data/error-code#-18004>)
    * [-18005](</docs/derivatives/futures-data/error-code#-18005>)
    * [-18006](</docs/derivatives/futures-data/error-code#-18006>)
    * [-18007](</docs/derivatives/futures-data/error-code#-18007>)
  * [21xxx - Portfolio Margin Account](</docs/derivatives/futures-data/error-code#21xxx---portfolio-margin-account>)
    * [-21001 USER_IS_NOT_UNIACCOUNT](</docs/derivatives/futures-data/error-code#-21001-user_is_not_uniaccount>)
    * [-21002 UNI_ACCOUNT_CANT_TRANSFER_FUTURE](</docs/derivatives/futures-data/error-code#-21002-uni_account_cant_transfer_future>)
    * [-21003 NET_ASSET_MUST_LTE_RATIO](</docs/derivatives/futures-data/error-code#-21003-net_asset_must_lte_ratio>)
    * [-21004 USER_NO_LIABILITY](</docs/derivatives/futures-data/error-code#-21004-user_no_liability>)
    * [-21005 NO_ENOUGH_ASSET](</docs/derivatives/futures-data/error-code#-21005-no_enough_asset>)
    * [-21006 HAD_IN_PROCESS_REPAY](</docs/derivatives/futures-data/error-code#-21006-had_in_process_repay>)
    * [-21007 IN_FORCE_LIQUIDATION](</docs/derivatives/futures-data/error-code#-21007-in_force_liquidation>)
  * [Order Rejection Issues](</docs/derivatives/futures-data/error-code#order-rejection-issues>)
  * [Errors regarding POST /api/v3/order/cancelReplace](</docs/derivatives/futures-data/error-code#errors-regarding-post-apiv3ordercancelreplace>)
    * [-2021 Order cancel-replace partially failed](</docs/derivatives/futures-data/error-code#-2021-order-cancel-replace-partially-failed>)
    * [-2022 Order cancel-replace failed.](</docs/derivatives/futures-data/error-code#-2022-order-cancel-replace-failed>)

