# Error Code

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/error-code)

## 10xx - General Server or Network issues

| Code | Name | Description |
|------|------|-------------|
| -1000 | UNKNOWN | An unknown error occurred while processing the request. |
| -1001 | DISCONNECTED | Internal error; unable to process your request. Please try again. |
| -1002 | UNAUTHORIZED | You are not authorized to execute this request. |
| -1003 | TOO_MANY_REQUESTS | Too many requests queued. Please use the websocket for live updates to avoid polling the API. We recommend using the websocket for getting data as much as possible. |
| -1006 | UNEXPECTED_RESP | An unexpected response was received from the message bus. Execution status unknown. |
| -1007 | TIMEOUT | Timeout waiting for response from backend server. |
| -1014 | UNKNOWN_ORDER_COMPOSITION | Unsupported order combination. |
| -1015 | TOO_MANY_ORDERS | Too many new orders. Please reduce the request rate. |
| -1016 | SERVICE_SHUTTING_DOWN | This service is no longer available. |
| -1020 | UNSUPPORTED_OPERATION | This operation is not supported. |
| -1021 | INVALID_TIMESTAMP | Timestamp for this request is outside of the recvWindow, or timestamp for this request was 1000ms ahead of the server's time. |
| -1022 | INVALID_SIGNATURE | Signature for this request is not valid. |

## 11xx - Request issues

| Code | Name | Description |
|------|------|-------------|
| -1100 | ILLEGAL_CHARS | Illegal characters found in a parameter. |
| -1101 | TOO_MANY_PARAMETERS | Too many parameters sent for this endpoint. |
| -1102 | MANDATORY_PARAM_EMPTY_OR_MALFORMED | A mandatory parameter was not sent, was empty/null, or malformed. |
| -1103 | UNKNOWN_PARAM | An unknown parameter was sent. |
| -1104 | UNREAD_PARAMETERS | Not all sent parameters were read. |
| -1105 | PARAM_EMPTY | A parameter was empty. |
| -1106 | PARAM_NOT_REQUIRED | A parameter was sent when not required. |
| -1108 | PARAM_OVERFLOW | Parameter overflow. |
| -1111 | BAD_PRECISION | Precision is over the maximum defined for this asset. |
| -1112 | NO_DEPTH | No orders on book for symbol. |
| -1114 | TIF_NOT_REQUIRED | TimeInForce parameter sent when not required. |
| -1115 | INVALID_TIF | Invalid timeInForce. |
| -1116 | INVALID_ORDER_TYPE | Invalid orderType. |
| -1117 | INVALID_SIDE | Invalid side. |
| -1118 | EMPTY_NEW_CL_ORD_ID | New client order ID was empty. |
| -1119 | EMPTY_ORG_CL_ORD_ID | Original client order ID was empty. |
| -1120 | BAD_INTERVAL | Invalid interval. |
| -1121 | BAD_SYMBOL | Invalid symbol. |
| -1125 | INVALID_LISTEN_KEY | This listenKey does not exist. |
| -1127 | MORE_THAN_XX_HOURS | Lookup interval is too big. |
| -1128 | OPTIONAL_PARAMS_BAD_COMBO | Combination of optional parameters invalid. |
| -1130 | INVALID_PARAMETER | Invalid data sent for a parameter. |
| -1134 | BAD_STRATEGY_TYPE | `strategyType` was less than 1000000. |
| -1135 | INVALID_JSON | Invalid JSON Request |
| -1139 | INVALID_TICKER_TYPE | Invalid ticker type. |
| -1145 | INVALID_CANCEL_RESTRICTIONS | `cancelRestrictions` has to be either `ONLY_NEW` or `ONLY_PARTIALLY_FILLED`. |
| -1151 | DUPLICATE_SYMBOLS | Duplicate symbol. |
| -1152 | INVALID_SBE_HEADER | Invalid `X-MBX-SBE` header; expected `<SCHEMA_ID>:<VERSION>`. |
| -1153 | UNSUPPORTED_SBE_SCHEMA_ID | Unsupported SBE schema ID or version specified in the `X-MBX-SBE` header. |
| -1155 | SBE_DISABLED | SBE is not enabled. |

## 20xx - Processing Issues

| Code | Name | Description |
|------|------|-------------|
| -2010 | NEW_ORDER_REJECTED | NEW_ORDER_REJECTED |
| -2011 | CANCEL_REJECTED | CANCEL_REJECTED |
| -2013 | NO_SUCH_ORDER | Order does not exist. |
| -2014 | BAD_API_KEY_FMT | API-key format invalid. |
| -2015 | REJECTED_MBX_KEY | Invalid API-key, IP, or permissions for action. |
| -2016 | NO_TRADING_WINDOW | No trading window could be found for the symbol. Try ticker/24hrs instead. |
| -2026 | ORDER_ARCHIVED | Order was canceled or expired with no executed qty over 90 days ago and has been archived. |

## Algo Order Specific Errors

| Code | Description |
|------|-------------|
| -20121 | Invalid Algo order parameter |
| -20124 | Too many open Algo orders |
| -20130 | Invalid Algo order quantity |
| -20132 | Invalid Algo order price |
| -20194 | Duration is too short to execute this order |
| -20195 | You don't have permission to use algo trading |

## Other Error Categories

### 100xx - Loan related

| Code | Name | Description |
|------|------|-------------|
| -10003 | NOT_FOUND | No records found |
| -10005 | NO_RECORDS | No records found |
| -10007 | COIN_NOT_LOANABLE | This coin is not loanable |
| -10011 | INSUFFICIENT_ASSET | Insufficient spot assets |
| -10012 | INVALID_AMOUNT | Invalid repayment amount |
| -10021 | ORDER_NOT_EXIST | Order does not exist |

### 13xxx - BLVT

| Code | Name | Description |
|------|------|-------------|
| -13000 | BLVT_FORBID_REDEEM | Redemption of the token is forbidden now |
| -13001 | BLVT_EXCEED_DAILY_LIMIT | Exceeds individual 24h redemption limit of the token |
| -13003 | BLVT_FORBID_PURCHASE | Subscription of the token is forbidden now |

### 21xxx - Portfolio Margin Account

| Code | Name | Description |
|------|------|-------------|
| -21001 | USER_IS_NOT_UNIACCOUNT | Request ID is not a Portfolio Margin Account |
| -21002 | UNI_ACCOUNT_CANT_TRANSFER_FUTURE | Portfolio Margin Account doesn't support transfer from margin to futures |

## Order Rejection Issues

Error messages like these are indicated when the error is coming specifically from the matching engine:

- `-1010 ERROR_MSG_RECEIVED`
- `-2010 NEW_ORDER_REJECTED`
- `-2011 CANCEL_REJECTED`

| Error message | Description |
|---------------|-------------|
| "Unknown order sent." | The order could not be found. |
| "Duplicate order sent." | The clientOrderId is already in use. |
| "Market is closed." | The symbol is not trading. |
| "Account has insufficient balance for requested action." | Not enough funds to complete the action. |
| "Market orders are not supported for this symbol." | MARKET is not enabled on the symbol. |
| "Iceberg orders are not supported for this symbol." | icebergQty is not enabled on the symbol |
| "Stop loss orders are not supported for this symbol." | STOP_LOSS is not enabled on the symbol |
| "Take profit orders are not supported for this symbol." | TAKE_PROFIT is not enabled on the symbol |
| "Price * QTY is zero or less." | price * quantity is too low |
| "This action is disabled on this account." | Contact customer support; some actions have been disabled on the account. |
| "Unsupported order combination" | The orderType, timeInForce, stopPrice, and/or icebergQty combination isn't allowed. |
| "Order would trigger immediately." | The order's stop price is not valid when compared to the last traded price. |
| "Order would immediately match and take." | LIMIT_MAKER order type would immediately match and trade, and not be a pure maker order. |
