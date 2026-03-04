On this page

# Error Codes

> Here is the error JSON payload:


    {
      "code":-1121,
      "msg":"Invalid symbol."
    }


Errors consist of two parts: an error code and a message.
Codes are universal,but messages can vary.

## 10xx - General Server or Network issues

### -1000 UNKNOWN

  - An unknown error occured while processing the request.

### -1001 DISCONNECTED

  - Internal error; unable to process your request. Please try again.

### -1002 UNAUTHORIZED

  - You are not authorized to execute this request.

### -1003 TOO_MANY_REQUESTS

  - Too many requests; current limit is %s requests per minute. Please use the websocket for live updates to avoid polling the API.
  - Way too many requests; IP banned until %s. Please use the websocket for live updates to avoid bans.

### -1004 DUPLICATE_IP

  - This IP is already on the white list

### -1005 NO_SUCH_IP

  - No such IP has been white listed

### -1006 UNEXPECTED_RESP

  - An unexpected response was received from the message bus. Execution status unknown.

### -1007 TIMEOUT

  - Timeout waiting for response from backend server. Send status unknown; execution status unknown.

### -1010 ERROR_MSG_RECEIVED

  - ERROR_MSG_RECEIVED.

### -1011 NON_WHITE_LIST

  - This IP cannot access this route.

### -1013 INVALID_MESSAGE

  - INVALID_MESSAGE.

### -1014 UNKNOWN_ORDER_COMPOSITION

  - Unsupported order combination.

### -1015 TOO_MANY_ORDERS

  - Too many new orders.
  - Too many new orders; current limit is %s orders per %s.

### -1016 SERVICE_SHUTTING_DOWN

  - This service is no longer available.

### -1020 UNSUPPORTED_OPERATION

  - This operation is not supported.

### -1021 INVALID_TIMESTAMP

  - Timestamp for this request is outside of the recvWindow.
  - Timestamp for this request was 1000ms ahead of the server's time.

### -1022 INVALID_SIGNATURE

  - Signature for this request is not valid.

### -1023 START_TIME_GREATER_THAN_END_TIME

  - Start time is greater than end time.

## 11xx - Request issues

### -1100 ILLEGAL_CHARS

  - Illegal characters found in a parameter.
  - Illegal characters found in parameter '%s'; legal range is '%s'.

### -1101 TOO_MANY_PARAMETERS

  - Too many parameters sent for this endpoint.
  - Too many parameters; expected '%s' and received '%s'.
  - Duplicate values for a parameter detected.

### -1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED

  - A mandatory parameter was not sent, was empty/null, or malformed.
  - Mandatory parameter '%s' was not sent, was empty/null, or malformed.
  - Param '%s' or '%s' must be sent, but both were empty/null!

### -1103 UNKNOWN_PARAM

  - An unknown parameter was sent.

### -1104 UNREAD_PARAMETERS

  - Not all sent parameters were read.
  - Not all sent parameters were read; read '%s' parameter(s) but was sent '%s'.

### -1105 PARAM_EMPTY

  - A parameter was empty.
  - Parameter '%s' was empty.

### -1106 PARAM_NOT_REQUIRED

  - A parameter was sent when not required.
  - Parameter '%s' sent when not required.

### -1108 BAD_ASSET

  - Invalid asset.

### -1109 BAD_ACCOUNT

  - Invalid account.

### -1110 BAD_INSTRUMENT_TYPE

  - Invalid symbolType.

### -1111 BAD_PRECISION

  - Precision is over the maximum defined for this asset.

### -1112 NO_DEPTH

  - No orders on book for symbol.

### -1113 WITHDRAW_NOT_NEGATIVE

  - Withdrawal amount must be negative.

### -1114 TIF_NOT_REQUIRED

  - TimeInForce parameter sent when not required.

### -1115 INVALID_TIF

  - Invalid timeInForce.

### -1116 INVALID_ORDER_TYPE

  - Invalid orderType.

### -1117 INVALID_SIDE

  - Invalid side.

### -1118 EMPTY_NEW_CL_ORD_ID

  - New client order ID was empty.

### -1119 EMPTY_ORG_CL_ORD_ID

  - Original client order ID was empty.

### -1120 BAD_INTERVAL

  - Invalid interval.

### -1121 BAD_SYMBOL

  - Invalid symbol.

### -1125 INVALID_LISTEN_KEY

  - This listenKey does not exist. Please use `POST /fapi/v1/listenKey` to recreate `listenKey`

### -1127 MORE_THAN_XX_HOURS

  - Lookup interval is too big.
  - More than %s hours between startTime and endTime.

### -1128 OPTIONAL_PARAMS_BAD_COMBO

  - Combination of optional parameters invalid.

### -1130 INVALID_PARAMETER

  - Invalid data sent for a parameter.
  - Data sent for parameter '%s' is not valid.

### -1136 INVALID_NEW_ORDER_RESP_TYPE

  - Invalid newOrderRespType.

## 20xx - Processing Issues

### -2010 NEW_ORDER_REJECTED

  - NEW_ORDER_REJECTED

### -2011 CANCEL_REJECTED

  - CANCEL_REJECTED

### -2013 NO_SUCH_ORDER

  - Order does not exist.

### -2014 BAD_API_KEY_FMT

  - API-key format invalid.

### -2015 REJECTED_MBX_KEY

  - Invalid API-key, IP, or permissions for action.

### -2016 NO_TRADING_WINDOW

  - No trading window could be found for the symbol. Try ticker/24hrs instead.

### -2018 BALANCE_NOT_SUFFICIENT

  - Balance is insufficient.

### -2019 MARGIN_NOT_SUFFICIEN

  - Margin is insufficient.

### -2020 UNABLE_TO_FILL

  - Unable to fill.

### -2021 ORDER_WOULD_IMMEDIATELY_TRIGGER

  - Order would immediately trigger.

### -2022 REDUCE_ONLY_REJECT

  - ReduceOnly Order is rejected.

### -2023 USER_IN_LIQUIDATION

  - User in liquidation mode now.

### -2024 POSITION_NOT_SUFFICIENT

  - Position is not sufficient.

### -2025 MAX_OPEN_ORDER_EXCEEDED

  - Reach max open order limit.

### -2026 REDUCE_ONLY_ORDER_TYPE_NOT_SUPPORTED

  - This OrderType is not supported when reduceOnly.

### -2027 MAX_LEVERAGE_RATIO

  - Exceeded the maximum allowable position at current leverage.

### -2028 MIN_LEVERAGE_RATIO

  - Leverage is smaller than permitted: insufficient margin balance.

## 40xx - Filters and other Issues

### -4000 INVALID_ORDER_STATUS

  - Invalid order status.

### -4001 PRICE_LESS_THAN_ZERO

  - Price less than 0.

### -4002 PRICE_GREATER_THAN_MAX_PRICE

  - Price greater than max price.

### -4003 QTY_LESS_THAN_ZERO

  - Quantity less than zero.

### -4004 QTY_LESS_THAN_MIN_QTY

  - Quantity less than min quantity.

### -4005 QTY_GREATER_THAN_MAX_QTY

  - Quantity greater than max quantity.

### -4006 STOP_PRICE_LESS_THAN_ZERO

  - Stop price less than zero.

### -4007 STOP_PRICE_GREATER_THAN_MAX_PRICE

  - Stop price greater than max price.

### -4008 TICK_SIZE_LESS_THAN_ZERO

  - Tick size less than zero.

### -4009 MAX_PRICE_LESS_THAN_MIN_PRICE

  - Max price less than min price.

### -4010 MAX_QTY_LESS_THAN_MIN_QTY

  - Max qty less than min qty.

### -4011 STEP_SIZE_LESS_THAN_ZERO

  - Step size less than zero.

### -4012 MAX_NUM_ORDERS_LESS_THAN_ZERO

  - Max mum orders less than zero.

### -4013 PRICE_LESS_THAN_MIN_PRICE

  - Price less than min price.

### -4014 PRICE_NOT_INCREASED_BY_TICK_SIZE

  - Price not increased by tick size.

### -4015 INVALID_CL_ORD_ID_LEN

  - Client order id is not valid.
  - Client order id length should not be more than 36 chars

### -4016 PRICE_HIGHTER_THAN_MULTIPLIER_UP

  - Price is higher than mark price multiplier cap.

### -4017 MULTIPLIER_UP_LESS_THAN_ZERO

  - Multiplier up less than zero.

### -4018 MULTIPLIER_DOWN_LESS_THAN_ZERO

  - Multiplier down less than zero.

### -4019 COMPOSITE_SCALE_OVERFLOW

  - Composite scale too large.

### -4020 TARGET_STRATEGY_INVALID

  - Target strategy invalid for orderType '%s',reduceOnly '%b'.

### -4021 INVALID_DEPTH_LIMIT

  - Invalid depth limit.
  - '%s' is not valid depth limit.

### -4022 WRONG_MARKET_STATUS

  - market status sent is not valid.

### -4023 QTY_NOT_INCREASED_BY_STEP_SIZE

  - Qty not increased by step size.

### -4024 PRICE_LOWER_THAN_MULTIPLIER_DOWN

  - Price is lower than mark price multiplier floor.

### -4025 MULTIPLIER_DECIMAL_LESS_THAN_ZERO

  - Multiplier decimal less than zero.

### -4026 COMMISSION_INVALID

  - Commission invalid.
  - `%s` less than zero.
  - `%s` absolute value greater than `%s`

### -4027 INVALID_ACCOUNT_TYPE

  - Invalid account type.

### -4028 INVALID_LEVERAGE

  - Invalid leverage
  - Leverage `%s` is not valid
  - Leverage `%s` already exist with `%s`

### -4029 INVALID_TICK_SIZE_PRECISION

  - Tick size precision is invalid.

### -4030 INVALID_STEP_SIZE_PRECISION

  - Step size precision is invalid.

### -4031 INVALID_WORKING_TYPE

  - Invalid parameter working type
  - Invalid parameter working type: `%s`

### -4032 EXCEED_MAX_CANCEL_ORDER_SIZE

  - Exceed maximum cancel order size.
  - Invalid parameter working type: `%s`

### -4033 INSURANCE_ACCOUNT_NOT_FOUND

  - Insurance account not found.

### -4044 INVALID_BALANCE_TYPE

  - Balance Type is invalid.

### -4045 MAX_STOP_ORDER_EXCEEDED

  - Reach max stop order limit.

### -4046 NO_NEED_TO_CHANGE_MARGIN_TYPE

  - No need to change margin type.

### -4047 THERE_EXISTS_OPEN_ORDERS

  - Margin type cannot be changed if there exists open orders.

### -4048 THERE_EXISTS_QUANTITY

  - Margin type cannot be changed if there exists position.

### -4049 ADD_ISOLATED_MARGIN_REJECT

  - Add margin only support for isolated position.

### -4050 CROSS_BALANCE_INSUFFICIENT

  - Cross balance insufficient.

### -4051 ISOLATED_BALANCE_INSUFFICIENT

  - Isolated balance insufficient.

### -4052 NO_NEED_TO_CHANGE_AUTO_ADD_MARGIN

  - No need to change auto add margin.

### -4053 AUTO_ADD_CROSSED_MARGIN_REJECT

  - Auto add margin only support for isolated position.

### -4054 ADD_ISOLATED_MARGIN_NO_POSITION_REJECT

  - Cannot add position margin: position is 0.

### -4055 AMOUNT_MUST_BE_POSITIVE

  - Amount must be positive.

### -4056 INVALID_API_KEY_TYPE

  - Invalid api key type.

### -4057 INVALID_RSA_PUBLIC_KEY

  - Invalid api public key

### -4058 MAX_PRICE_TOO_LARGE

  - maxPrice and priceDecimal too large,please check.

### -4059 NO_NEED_TO_CHANGE_POSITION_SIDE

  - No need to change position side.

### -4060 INVALID_POSITION_SIDE

  - Invalid position side.

### -4061 POSITION_SIDE_NOT_MATCH

  - Order's position side does not match user's setting.

### -4062 REDUCE_ONLY_CONFLICT

  - Invalid or improper reduceOnly value.

### -4067 POSITION_SIDE_CHANGE_EXISTS_OPEN_ORDERS

  - Position side cannot be changed if there exists open orders.

### -4068 POSITION_SIDE_CHANGE_EXISTS_QUANTITY

  - Position side cannot be changed if there exists position.

### -4082 INVALID_BATCH_PLACE_ORDER_SIZE

  - Invalid number of batch place orders.
  - Invalid number of batch place orders: %s

### -4083 PLACE_BATCH_ORDERS_FAIL

  - Fail to place batch orders.

### -4084 UPCOMING_METHOD

  - Method is not allowed currently. Upcoming soon.

### -4086 INVALID_PRICE_SPREAD_THRESHOLD

  - Invalid price spread threshold.

### -4087 INVALID_PAIR

  - Invalid pair.

### -4088 INVALID_TIME_INTERVAL

  - Invalid time interval.
  - Maximum time interval is %s days.

### -4089 REDUCE_ONLY_ORDER_PERMISSION

  - User can only place reduce only order.

### -4090 NO_PLACE_ORDER_PERMISSION

  - User can not place order currently.

### -4104 INVALID_CONTRACT_TYPE

  - Invalid contract type.

### -4110 INVALID_CLIENT_TRAN_ID_LEN

  - clientTranId is not valid.
  - Client tran id length should be less than 64 chars.

### -4111 DUPLICATED_CLIENT_TRAN_ID

  - clientTranId is duplicated.
  - Client tran id should be unique within 7 days.

### -4112 REDUCE_ONLY_MARGIN_CHECK_FAILED

  - ReduceOnly Order Failed. Please check your existing position and open orders.

### -4113 MARKET_ORDER_REJECT

  - The counterparty's best price does not meet the PERCENT_PRICE filter limit.

### -4135 INVALID_ACTIVATION_PRICE

  - Invalid activation price.

### -4137 QUANTITY_EXISTS_WITH_CLOSE_POSITION

  - Quantity must be zero with closePosition equals true.

### -4138 REDUCE_ONLY_MUST_BE_TRUE

  - Reduce only must be true with closePosition equals true.

### -4139 ORDER_TYPE_CANNOT_BE_MKT

  - Order type can not be market if it's unable to cancel.

### -4142 STRATEGY_INVALID_TRIGGER_PRICE

  - REJECT: take profit or stop order will be triggered immediately.

### -4150 ISOLATED_LEVERAGE_REJECT_WITH_POSITION

  - Leverage reduction is not supported in Isolated Margin Mode with open positions.

### -4151 PRICE_HIGHTER_THAN_STOP_MULTIPLIER_UP

  - Price is higher than stop price multiplier cap.
  - Limit price can't be higher than %s.

### -4152 PRICE_LOWER_THAN_STOP_MULTIPLIER_DOWN

  - Price is lower than stop price multiplier floor.
  - Limit price can't be lower than %s.

### -4154 STOP_PRICE_HIGHER_THAN_PRICE_MULTIPLIER_LIMIT

  - Stop price is higher than price multiplier cap.
  - Stop price can't be higher than %s

### -4155 STOP_PRICE_LOWER_THAN_PRICE_MULTIPLIER_LIMIT

  - PStop price is lower than price multiplier floor.
  - Stop price can't be lower than %s

### -4178 MIN_NOTIONAL

  - Order's notional must be no smaller than one (unless you choose reduce only)
  - Order's notional must be no smaller than %s (unless you choose reduce only)

### -4192 COOLING_OFF_PERIOD

  - Trade forbidden due to Cooling-off Period.

### -4194 ADJUST_LEVERAGE_KYC_FAILED

  - Intermediate Personal Verification is required for adjusting leverage over 20x.

### -4195 ADJUST_LEVERAGE_ONE_MONTH_FAILED

  - More than 20x leverage is available one month after account registration.

### -4196 LIMIT_ORDER_ONLY

  - Only limit order is supported.

### -4197 SAME_ORDER

  - No need to modify the order.

### -4198 EXCEED_MAX_MODIFY_ORDER_LIMIT

  - Exceed maximum modify order limit.

### -4199 MOVE_ORDER_NOT_ALLOWED_SYMBOL_REASON

  - Symbol is not in trading status. Order amendment is not permitted.

### -4200 ADJUST_LEVERAGE_X_DAYS_FAILED

  - More than 20x leverage is available 30 days after Futures account registration.
  - More than 20x leverage is available %s days after Futures account registration.

### -4201 ADJUST_LEVERAGE_KYC_LIMIT

  - Users in this country has limited adjust leverage.
  - Users in your location/country can only access a maximum leverage of %s

### -4202 ADJUST_LEVERAGE_ACCOUNT_SYMBOL_FAILED

  - Current symbol leverage cannot exceed 20 when using position limit adjustment service.

### -4188 ME_INVALID_TIMESTAMP

  - Timestamp for this request is outside of the ME recvWindow.

  - [10xx - General Server or Network issues](</docs/derivatives/coin-margined-futures/error-code#10xx---general-server-or-network-issues>)
    - [-1000 UNKNOWN](</docs/derivatives/coin-margined-futures/error-code#-1000-unknown>)
    - [-1001 DISCONNECTED](</docs/derivatives/coin-margined-futures/error-code#-1001-disconnected>)
    - [-1002 UNAUTHORIZED](</docs/derivatives/coin-margined-futures/error-code#-1002-unauthorized>)
    - [-1003 TOO_MANY_REQUESTS](</docs/derivatives/coin-margined-futures/error-code#-1003-too_many_requests>)
    - [-1004 DUPLICATE_IP](</docs/derivatives/coin-margined-futures/error-code#-1004-duplicate_ip>)
    - [-1005 NO_SUCH_IP](</docs/derivatives/coin-margined-futures/error-code#-1005-no_such_ip>)
    - [-1006 UNEXPECTED_RESP](</docs/derivatives/coin-margined-futures/error-code#-1006-unexpected_resp>)
    - [-1007 TIMEOUT](</docs/derivatives/coin-margined-futures/error-code#-1007-timeout>)
    - [-1010 ERROR_MSG_RECEIVED](</docs/derivatives/coin-margined-futures/error-code#-1010-error_msg_received>)
    - [-1011 NON_WHITE_LIST](</docs/derivatives/coin-margined-futures/error-code#-1011-non_white_list>)
    - [-1013 INVALID_MESSAGE](</docs/derivatives/coin-margined-futures/error-code#-1013-invalid_message>)
    - [-1014 UNKNOWN_ORDER_COMPOSITION](</docs/derivatives/coin-margined-futures/error-code#-1014-unknown_order_composition>)
    - [-1015 TOO_MANY_ORDERS](</docs/derivatives/coin-margined-futures/error-code#-1015-too_many_orders>)
    - [-1016 SERVICE_SHUTTING_DOWN](</docs/derivatives/coin-margined-futures/error-code#-1016-service_shutting_down>)
    - [-1020 UNSUPPORTED_OPERATION](</docs/derivatives/coin-margined-futures/error-code#-1020-unsupported_operation>)
    - [-1021 INVALID_TIMESTAMP](</docs/derivatives/coin-margined-futures/error-code#-1021-invalid_timestamp>)
    - [-1022 INVALID_SIGNATURE](</docs/derivatives/coin-margined-futures/error-code#-1022-invalid_signature>)
    - [-1023 START_TIME_GREATER_THAN_END_TIME](</docs/derivatives/coin-margined-futures/error-code#-1023-start_time_greater_than_end_time>)
  - [11xx - Request issues](</docs/derivatives/coin-margined-futures/error-code#11xx---request-issues>)
    - [-1100 ILLEGAL_CHARS](</docs/derivatives/coin-margined-futures/error-code#-1100-illegal_chars>)
    - [-1101 TOO_MANY_PARAMETERS](</docs/derivatives/coin-margined-futures/error-code#-1101-too_many_parameters>)
    - [-1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED](</docs/derivatives/coin-margined-futures/error-code#-1102-mandatory_param_empty_or_malformed>)
    - [-1103 UNKNOWN_PARAM](</docs/derivatives/coin-margined-futures/error-code#-1103-unknown_param>)
    - [-1104 UNREAD_PARAMETERS](</docs/derivatives/coin-margined-futures/error-code#-1104-unread_parameters>)
    - [-1105 PARAM_EMPTY](</docs/derivatives/coin-margined-futures/error-code#-1105-param_empty>)
    - [-1106 PARAM_NOT_REQUIRED](</docs/derivatives/coin-margined-futures/error-code#-1106-param_not_required>)
    - [-1108 BAD_ASSET](</docs/derivatives/coin-margined-futures/error-code#-1108-bad_asset>)
    - [-1109 BAD_ACCOUNT](</docs/derivatives/coin-margined-futures/error-code#-1109-bad_account>)
    - [-1110 BAD_INSTRUMENT_TYPE](</docs/derivatives/coin-margined-futures/error-code#-1110-bad_instrument_type>)
    - [-1111 BAD_PRECISION](</docs/derivatives/coin-margined-futures/error-code#-1111-bad_precision>)
    - [-1112 NO_DEPTH](</docs/derivatives/coin-margined-futures/error-code#-1112-no_depth>)
    - [-1113 WITHDRAW_NOT_NEGATIVE](</docs/derivatives/coin-margined-futures/error-code#-1113-withdraw_not_negative>)
    - [-1114 TIF_NOT_REQUIRED](</docs/derivatives/coin-margined-futures/error-code#-1114-tif_not_required>)
    - [-1115 INVALID_TIF](</docs/derivatives/coin-margined-futures/error-code#-1115-invalid_tif>)
    - [-1116 INVALID_ORDER_TYPE](</docs/derivatives/coin-margined-futures/error-code#-1116-invalid_order_type>)
    - [-1117 INVALID_SIDE](</docs/derivatives/coin-margined-futures/error-code#-1117-invalid_side>)
    - [-1118 EMPTY_NEW_CL_ORD_ID](</docs/derivatives/coin-margined-futures/error-code#-1118-empty_new_cl_ord_id>)
    - [-1119 EMPTY_ORG_CL_ORD_ID](</docs/derivatives/coin-margined-futures/error-code#-1119-empty_org_cl_ord_id>)
    - [-1120 BAD_INTERVAL](</docs/derivatives/coin-margined-futures/error-code#-1120-bad_interval>)
    - [-1121 BAD_SYMBOL](</docs/derivatives/coin-margined-futures/error-code#-1121-bad_symbol>)
    - [-1125 INVALID_LISTEN_KEY](</docs/derivatives/coin-margined-futures/error-code#-1125-invalid_listen_key>)
    - [-1127 MORE_THAN_XX_HOURS](</docs/derivatives/coin-margined-futures/error-code#-1127-more_than_xx_hours>)
    - [-1128 OPTIONAL_PARAMS_BAD_COMBO](</docs/derivatives/coin-margined-futures/error-code#-1128-optional_params_bad_combo>)
    - [-1130 INVALID_PARAMETER](</docs/derivatives/coin-margined-futures/error-code#-1130-invalid_parameter>)
    - [-1136 INVALID_NEW_ORDER_RESP_TYPE](</docs/derivatives/coin-margined-futures/error-code#-1136-invalid_new_order_resp_type>)
  - [20xx - Processing Issues](</docs/derivatives/coin-margined-futures/error-code#20xx---processing-issues>)
    - [-2010 NEW_ORDER_REJECTED](</docs/derivatives/coin-margined-futures/error-code#-2010-new_order_rejected>)
    - [-2011 CANCEL_REJECTED](</docs/derivatives/coin-margined-futures/error-code#-2011-cancel_rejected>)
    - [-2013 NO_SUCH_ORDER](</docs/derivatives/coin-margined-futures/error-code#-2013-no_such_order>)
    - [-2014 BAD_API_KEY_FMT](</docs/derivatives/coin-margined-futures/error-code#-2014-bad_api_key_fmt>)
    - [-2015 REJECTED_MBX_KEY](</docs/derivatives/coin-margined-futures/error-code#-2015-rejected_mbx_key>)
    - [-2016 NO_TRADING_WINDOW](</docs/derivatives/coin-margined-futures/error-code#-2016-no_trading_window>)
    - [-2018 BALANCE_NOT_SUFFICIENT](</docs/derivatives/coin-margined-futures/error-code#-2018-balance_not_sufficient>)
    - [-2019 MARGIN_NOT_SUFFICIEN](</docs/derivatives/coin-margined-futures/error-code#-2019-margin_not_sufficien>)
    - [-2020 UNABLE_TO_FILL](</docs/derivatives/coin-margined-futures/error-code#-2020-unable_to_fill>)
    - [-2021 ORDER_WOULD_IMMEDIATELY_TRIGGER](</docs/derivatives/coin-margined-futures/error-code#-2021-order_would_immediately_trigger>)
    - [-2022 REDUCE_ONLY_REJECT](</docs/derivatives/coin-margined-futures/error-code#-2022-reduce_only_reject>)
    - [-2023 USER_IN_LIQUIDATION](</docs/derivatives/coin-margined-futures/error-code#-2023-user_in_liquidation>)
    - [-2024 POSITION_NOT_SUFFICIENT](</docs/derivatives/coin-margined-futures/error-code#-2024-position_not_sufficient>)
    - [-2025 MAX_OPEN_ORDER_EXCEEDED](</docs/derivatives/coin-margined-futures/error-code#-2025-max_open_order_exceeded>)
    - [-2026 REDUCE_ONLY_ORDER_TYPE_NOT_SUPPORTED](</docs/derivatives/coin-margined-futures/error-code#-2026-reduce_only_order_type_not_supported>)
    - [-2027 MAX_LEVERAGE_RATIO](</docs/derivatives/coin-margined-futures/error-code#-2027-max_leverage_ratio>)
    - [-2028 MIN_LEVERAGE_RATIO](</docs/derivatives/coin-margined-futures/error-code#-2028-min_leverage_ratio>)
  - [40xx - Filters and other Issues](</docs/derivatives/coin-margined-futures/error-code#40xx---filters-and-other-issues>)
    - [-4000 INVALID_ORDER_STATUS](</docs/derivatives/coin-margined-futures/error-code#-4000-invalid_order_status>)
    - [-4001 PRICE_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4001-price_less_than_zero>)
    - [-4002 PRICE_GREATER_THAN_MAX_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4002-price_greater_than_max_price>)
    - [-4003 QTY_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4003-qty_less_than_zero>)
    - [-4004 QTY_LESS_THAN_MIN_QTY](</docs/derivatives/coin-margined-futures/error-code#-4004-qty_less_than_min_qty>)
    - [-4005 QTY_GREATER_THAN_MAX_QTY](</docs/derivatives/coin-margined-futures/error-code#-4005-qty_greater_than_max_qty>)
    - [-4006 STOP_PRICE_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4006-stop_price_less_than_zero>)
    - [-4007 STOP_PRICE_GREATER_THAN_MAX_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4007-stop_price_greater_than_max_price>)
    - [-4008 TICK_SIZE_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4008-tick_size_less_than_zero>)
    - [-4009 MAX_PRICE_LESS_THAN_MIN_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4009-max_price_less_than_min_price>)
    - [-4010 MAX_QTY_LESS_THAN_MIN_QTY](</docs/derivatives/coin-margined-futures/error-code#-4010-max_qty_less_than_min_qty>)
    - [-4011 STEP_SIZE_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4011-step_size_less_than_zero>)
    - [-4012 MAX_NUM_ORDERS_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4012-max_num_orders_less_than_zero>)
    - [-4013 PRICE_LESS_THAN_MIN_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4013-price_less_than_min_price>)
    - [-4014 PRICE_NOT_INCREASED_BY_TICK_SIZE](</docs/derivatives/coin-margined-futures/error-code#-4014-price_not_increased_by_tick_size>)
    - [-4015 INVALID_CL_ORD_ID_LEN](</docs/derivatives/coin-margined-futures/error-code#-4015-invalid_cl_ord_id_len>)
    - [-4016 PRICE_HIGHTER_THAN_MULTIPLIER_UP](</docs/derivatives/coin-margined-futures/error-code#-4016-price_highter_than_multiplier_up>)
    - [-4017 MULTIPLIER_UP_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4017-multiplier_up_less_than_zero>)
    - [-4018 MULTIPLIER_DOWN_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4018-multiplier_down_less_than_zero>)
    - [-4019 COMPOSITE_SCALE_OVERFLOW](</docs/derivatives/coin-margined-futures/error-code#-4019-composite_scale_overflow>)
    - [-4020 TARGET_STRATEGY_INVALID](</docs/derivatives/coin-margined-futures/error-code#-4020-target_strategy_invalid>)
    - [-4021 INVALID_DEPTH_LIMIT](</docs/derivatives/coin-margined-futures/error-code#-4021-invalid_depth_limit>)
    - [-4022 WRONG_MARKET_STATUS](</docs/derivatives/coin-margined-futures/error-code#-4022-wrong_market_status>)
    - [-4023 QTY_NOT_INCREASED_BY_STEP_SIZE](</docs/derivatives/coin-margined-futures/error-code#-4023-qty_not_increased_by_step_size>)
    - [-4024 PRICE_LOWER_THAN_MULTIPLIER_DOWN](</docs/derivatives/coin-margined-futures/error-code#-4024-price_lower_than_multiplier_down>)
    - [-4025 MULTIPLIER_DECIMAL_LESS_THAN_ZERO](</docs/derivatives/coin-margined-futures/error-code#-4025-multiplier_decimal_less_than_zero>)
    - [-4026 COMMISSION_INVALID](</docs/derivatives/coin-margined-futures/error-code#-4026-commission_invalid>)
    - [-4027 INVALID_ACCOUNT_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4027-invalid_account_type>)
    - [-4028 INVALID_LEVERAGE](</docs/derivatives/coin-margined-futures/error-code#-4028-invalid_leverage>)
    - [-4029 INVALID_TICK_SIZE_PRECISION](</docs/derivatives/coin-margined-futures/error-code#-4029-invalid_tick_size_precision>)
    - [-4030 INVALID_STEP_SIZE_PRECISION](</docs/derivatives/coin-margined-futures/error-code#-4030-invalid_step_size_precision>)
    - [-4031 INVALID_WORKING_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4031-invalid_working_type>)
    - [-4032 EXCEED_MAX_CANCEL_ORDER_SIZE](</docs/derivatives/coin-margined-futures/error-code#-4032-exceed_max_cancel_order_size>)
    - [-4033 INSURANCE_ACCOUNT_NOT_FOUND](</docs/derivatives/coin-margined-futures/error-code#-4033-insurance_account_not_found>)
    - [-4044 INVALID_BALANCE_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4044-invalid_balance_type>)
    - [-4045 MAX_STOP_ORDER_EXCEEDED](</docs/derivatives/coin-margined-futures/error-code#-4045-max_stop_order_exceeded>)
    - [-4046 NO_NEED_TO_CHANGE_MARGIN_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4046-no_need_to_change_margin_type>)
    - [-4047 THERE_EXISTS_OPEN_ORDERS](</docs/derivatives/coin-margined-futures/error-code#-4047-there_exists_open_orders>)
    - [-4048 THERE_EXISTS_QUANTITY](</docs/derivatives/coin-margined-futures/error-code#-4048-there_exists_quantity>)
    - [-4049 ADD_ISOLATED_MARGIN_REJECT](</docs/derivatives/coin-margined-futures/error-code#-4049-add_isolated_margin_reject>)
    - [-4050 CROSS_BALANCE_INSUFFICIENT](</docs/derivatives/coin-margined-futures/error-code#-4050-cross_balance_insufficient>)
    - [-4051 ISOLATED_BALANCE_INSUFFICIENT](</docs/derivatives/coin-margined-futures/error-code#-4051-isolated_balance_insufficient>)
    - [-4052 NO_NEED_TO_CHANGE_AUTO_ADD_MARGIN](</docs/derivatives/coin-margined-futures/error-code#-4052-no_need_to_change_auto_add_margin>)
    - [-4053 AUTO_ADD_CROSSED_MARGIN_REJECT](</docs/derivatives/coin-margined-futures/error-code#-4053-auto_add_crossed_margin_reject>)
    - [-4054 ADD_ISOLATED_MARGIN_NO_POSITION_REJECT](</docs/derivatives/coin-margined-futures/error-code#-4054-add_isolated_margin_no_position_reject>)
    - [-4055 AMOUNT_MUST_BE_POSITIVE](</docs/derivatives/coin-margined-futures/error-code#-4055-amount_must_be_positive>)
    - [-4056 INVALID_API_KEY_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4056-invalid_api_key_type>)
    - [-4057 INVALID_RSA_PUBLIC_KEY](</docs/derivatives/coin-margined-futures/error-code#-4057-invalid_rsa_public_key>)
    - [-4058 MAX_PRICE_TOO_LARGE](</docs/derivatives/coin-margined-futures/error-code#-4058-max_price_too_large>)
    - [-4059 NO_NEED_TO_CHANGE_POSITION_SIDE](</docs/derivatives/coin-margined-futures/error-code#-4059-no_need_to_change_position_side>)
    - [-4060 INVALID_POSITION_SIDE](</docs/derivatives/coin-margined-futures/error-code#-4060-invalid_position_side>)
    - [-4061 POSITION_SIDE_NOT_MATCH](</docs/derivatives/coin-margined-futures/error-code#-4061-position_side_not_match>)
    - [-4062 REDUCE_ONLY_CONFLICT](</docs/derivatives/coin-margined-futures/error-code#-4062-reduce_only_conflict>)
    - [-4067 POSITION_SIDE_CHANGE_EXISTS_OPEN_ORDERS](</docs/derivatives/coin-margined-futures/error-code#-4067-position_side_change_exists_open_orders>)
    - [-4068 POSITION_SIDE_CHANGE_EXISTS_QUANTITY](</docs/derivatives/coin-margined-futures/error-code#-4068-position_side_change_exists_quantity>)
    - [-4082 INVALID_BATCH_PLACE_ORDER_SIZE](</docs/derivatives/coin-margined-futures/error-code#-4082-invalid_batch_place_order_size>)
    - [-4083 PLACE_BATCH_ORDERS_FAIL](</docs/derivatives/coin-margined-futures/error-code#-4083-place_batch_orders_fail>)
    - [-4084 UPCOMING_METHOD](</docs/derivatives/coin-margined-futures/error-code#-4084-upcoming_method>)
    - [-4086 INVALID_PRICE_SPREAD_THRESHOLD](</docs/derivatives/coin-margined-futures/error-code#-4086-invalid_price_spread_threshold>)
    - [-4087 INVALID_PAIR](</docs/derivatives/coin-margined-futures/error-code#-4087-invalid_pair>)
    - [-4088 INVALID_TIME_INTERVAL](</docs/derivatives/coin-margined-futures/error-code#-4088-invalid_time_interval>)
    - [-4089 REDUCE_ONLY_ORDER_PERMISSION](</docs/derivatives/coin-margined-futures/error-code#-4089-reduce_only_order_permission>)
    - [-4090 NO_PLACE_ORDER_PERMISSION](</docs/derivatives/coin-margined-futures/error-code#-4090-no_place_order_permission>)
    - [-4104 INVALID_CONTRACT_TYPE](</docs/derivatives/coin-margined-futures/error-code#-4104-invalid_contract_type>)
    - [-4110 INVALID_CLIENT_TRAN_ID_LEN](</docs/derivatives/coin-margined-futures/error-code#-4110-invalid_client_tran_id_len>)
    - [-4111 DUPLICATED_CLIENT_TRAN_ID](</docs/derivatives/coin-margined-futures/error-code#-4111-duplicated_client_tran_id>)
    - [-4112 REDUCE_ONLY_MARGIN_CHECK_FAILED](</docs/derivatives/coin-margined-futures/error-code#-4112-reduce_only_margin_check_failed>)
    - [-4113 MARKET_ORDER_REJECT](</docs/derivatives/coin-margined-futures/error-code#-4113-market_order_reject>)
    - [-4135 INVALID_ACTIVATION_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4135-invalid_activation_price>)
    - [-4137 QUANTITY_EXISTS_WITH_CLOSE_POSITION](</docs/derivatives/coin-margined-futures/error-code#-4137-quantity_exists_with_close_position>)
    - [-4138 REDUCE_ONLY_MUST_BE_TRUE](</docs/derivatives/coin-margined-futures/error-code#-4138-reduce_only_must_be_true>)
    - [-4139 ORDER_TYPE_CANNOT_BE_MKT](</docs/derivatives/coin-margined-futures/error-code#-4139-order_type_cannot_be_mkt>)
    - [-4142 STRATEGY_INVALID_TRIGGER_PRICE](</docs/derivatives/coin-margined-futures/error-code#-4142-strategy_invalid_trigger_price>)
    - [-4150 ISOLATED_LEVERAGE_REJECT_WITH_POSITION](</docs/derivatives/coin-margined-futures/error-code#-4150-isolated_leverage_reject_with_position>)
    - [-4151 PRICE_HIGHTER_THAN_STOP_MULTIPLIER_UP](</docs/derivatives/coin-margined-futures/error-code#-4151-price_highter_than_stop_multiplier_up>)
    - [-4152 PRICE_LOWER_THAN_STOP_MULTIPLIER_DOWN](</docs/derivatives/coin-margined-futures/error-code#-4152-price_lower_than_stop_multiplier_down>)
    - [-4154 STOP_PRICE_HIGHER_THAN_PRICE_MULTIPLIER_LIMIT](</docs/derivatives/coin-margined-futures/error-code#-4154-stop_price_higher_than_price_multiplier_limit>)
    - [-4155 STOP_PRICE_LOWER_THAN_PRICE_MULTIPLIER_LIMIT](</docs/derivatives/coin-margined-futures/error-code#-4155-stop_price_lower_than_price_multiplier_limit>)
    - [-4178 MIN_NOTIONAL](</docs/derivatives/coin-margined-futures/error-code#-4178-min_notional>)
    - [-4192 COOLING_OFF_PERIOD](</docs/derivatives/coin-margined-futures/error-code#-4192-cooling_off_period>)
    - [-4194 ADJUST_LEVERAGE_KYC_FAILED](</docs/derivatives/coin-margined-futures/error-code#-4194-adjust_leverage_kyc_failed>)
    - [-4195 ADJUST_LEVERAGE_ONE_MONTH_FAILED](</docs/derivatives/coin-margined-futures/error-code#-4195-adjust_leverage_one_month_failed>)
    - [-4196 LIMIT_ORDER_ONLY](</docs/derivatives/coin-margined-futures/error-code#-4196-limit_order_only>)
    - [-4197 SAME_ORDER](</docs/derivatives/coin-margined-futures/error-code#-4197-same_order>)
    - [-4198 EXCEED_MAX_MODIFY_ORDER_LIMIT](</docs/derivatives/coin-margined-futures/error-code#-4198-exceed_max_modify_order_limit>)
    - [-4199 MOVE_ORDER_NOT_ALLOWED_SYMBOL_REASON](</docs/derivatives/coin-margined-futures/error-code#-4199-move_order_not_allowed_symbol_reason>)
    - [-4200 ADJUST_LEVERAGE_X_DAYS_FAILED](</docs/derivatives/coin-margined-futures/error-code#-4200-adjust_leverage_x_days_failed>)
    - [-4201 ADJUST_LEVERAGE_KYC_LIMIT](</docs/derivatives/coin-margined-futures/error-code#-4201-adjust_leverage_kyc_limit>)
    - [-4202 ADJUST_LEVERAGE_ACCOUNT_SYMBOL_FAILED](</docs/derivatives/coin-margined-futures/error-code#-4202-adjust_leverage_account_symbol_failed>)
    - [-4188 ME_INVALID_TIMESTAMP](</docs/derivatives/coin-margined-futures/error-code#-4188-me_invalid_timestamp>)
