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

  - An unknown error occurred while processing the request.

### -1001 DISCONNECTED

  - Internal error; unable to process your request. Please try again.

### -1002 UNAUTHORIZED

  - You are not authorized to execute this request.

### -1008 TOO_MANY_REQUESTS

  - Too many requests queued.
  - Too much request weight used; please use the websocket for live updates to avoid polling the API.
  - Too much request weight used; current limit is %s request weight per %s %s. Please use the websocket for live updates to avoid polling the API.
  - Way too much request weight used; IP banned until %s. Please use the websocket for live updates to avoid bans.

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

## 11xx - 2xxx Request issues

### -1100 ILLEGAL_CHARS

  - Illegal characters found in a parameter.
  - Illegal characters found in a parameter. %s
  - Illegal characters found in parameter `%s`; legal range is `%s`.

### -1101 TOO_MANY_PARAMETERS

  - Too many parameters sent for this endpoint.
  - Too many parameters; expected `%s` and received `%s`.
  - Duplicate values for a parameter detected.

### -1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED

  - A mandatory parameter was not sent, was empty/null, or malformed.
  - Mandatory parameter `%s` was not sent, was empty/null, or malformed.
  - Param `%s` or `%s` must be sent, but both were empty/null!

### -1103 UNKNOWN_PARAM

  - An unknown parameter was sent.

### -1104 UNREAD_PARAMETERS

  - Not all sent parameters were read.
  - Not all sent parameters were read; read `%s` parameter(s) but was sent `%s`.

### -1105 PARAM_EMPTY

  - A parameter was empty.
  - Parameter `%s` was empty.

### -1106 PARAM_NOT_REQUIRED

  - A parameter was sent when not required.
  - Parameter `%s` sent when not required.

### -1111 BAD_PRECISION

  - Precision is over the maximum defined for this asset.

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

  - This listenKey does not exist.

### -1127 MORE_THAN_XX_HOURS

  - Lookup interval is too big.
  - More than %s hours between startTime and endTime.

### -1128 BAD_CONTRACT

  - Invalid underlying

### -1129 BAD_CURRENCY

  - Invalid asset。

### -1130 INVALID_PARAMETER

  - Invalid data sent for a parameter.
  - Data sent for paramter `%s` is not valid.

### -1131 BAD_RECV_WINDOW

  - recvWindow must be less than 60000

### -2010 NEW_ORDER_REJECTED

  - NEW_ORDER_REJECTED

### -2013 NO_SUCH_ORDER

  - Order does not exist.

### -2014 BAD_API_KEY_FMT

  - API-key format invalid.

### -2015 INVALID_API_KEY

  - Invalid API-key, IP, or permissions for action.

### -2018 BALANCE_NOT_SUFFICIENT

  - Balance is insufficient.

### -2027 OPTION_MARGIN_NOT_SUFFICIENT

  - Option margin is insufficient.

## 3xxx-5xxx Filters and other issues

### -3029 TRANSFER_FAILED

  - Asset transfer fail.

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

### -4013 PRICE_LESS_THAN_MIN_PRICE

  - Price less than min price.

### -4029 INVALID_TICK_SIZE_PRECISION

  - Tick size precision is invalid.

### -4030 INVALID_QTY_PRECISION

  - Step size precision is invalid.

### -4055 AMOUNT_MUST_BE_POSITIVE

  - Amount must be positive.

  - [10xx - General Server or Network issues](</docs/derivatives/option/error-code#10xx---general-server-or-network-issues>)
    - [-1000 UNKNOWN](</docs/derivatives/option/error-code#-1000-unknown>)
    - [-1001 DISCONNECTED](</docs/derivatives/option/error-code#-1001-disconnected>)
    - [-1002 UNAUTHORIZED](</docs/derivatives/option/error-code#-1002-unauthorized>)
    - [-1008 TOO_MANY_REQUESTS](</docs/derivatives/option/error-code#-1008-too_many_requests>)
    - [-1014 UNKNOWN_ORDER_COMPOSITION](</docs/derivatives/option/error-code#-1014-unknown_order_composition>)
    - [-1015 TOO_MANY_ORDERS](</docs/derivatives/option/error-code#-1015-too_many_orders>)
    - [-1016 SERVICE_SHUTTING_DOWN](</docs/derivatives/option/error-code#-1016-service_shutting_down>)
    - [-1020 UNSUPPORTED_OPERATION](</docs/derivatives/option/error-code#-1020-unsupported_operation>)
    - [-1021 INVALID_TIMESTAMP](</docs/derivatives/option/error-code#-1021-invalid_timestamp>)
    - [-1022 INVALID_SIGNATURE](</docs/derivatives/option/error-code#-1022-invalid_signature>)
  - [11xx - 2xxx Request issues](</docs/derivatives/option/error-code#11xx---2xxx-request-issues>)
    - [-1100 ILLEGAL_CHARS](</docs/derivatives/option/error-code#-1100-illegal_chars>)
    - [-1101 TOO_MANY_PARAMETERS](</docs/derivatives/option/error-code#-1101-too_many_parameters>)
    - [-1102 MANDATORY_PARAM_EMPTY_OR_MALFORMED](</docs/derivatives/option/error-code#-1102-mandatory_param_empty_or_malformed>)
    - [-1103 UNKNOWN_PARAM](</docs/derivatives/option/error-code#-1103-unknown_param>)
    - [-1104 UNREAD_PARAMETERS](</docs/derivatives/option/error-code#-1104-unread_parameters>)
    - [-1105 PARAM_EMPTY](</docs/derivatives/option/error-code#-1105-param_empty>)
    - [-1106 PARAM_NOT_REQUIRED](</docs/derivatives/option/error-code#-1106-param_not_required>)
    - [-1111 BAD_PRECISION](</docs/derivatives/option/error-code#-1111-bad_precision>)
    - [-1115 INVALID_TIF](</docs/derivatives/option/error-code#-1115-invalid_tif>)
    - [-1116 INVALID_ORDER_TYPE](</docs/derivatives/option/error-code#-1116-invalid_order_type>)
    - [-1117 INVALID_SIDE](</docs/derivatives/option/error-code#-1117-invalid_side>)
    - [-1118 EMPTY_NEW_CL_ORD_ID](</docs/derivatives/option/error-code#-1118-empty_new_cl_ord_id>)
    - [-1119 EMPTY_ORG_CL_ORD_ID](</docs/derivatives/option/error-code#-1119-empty_org_cl_ord_id>)
    - [-1120 BAD_INTERVAL](</docs/derivatives/option/error-code#-1120-bad_interval>)
    - [-1121 BAD_SYMBOL](</docs/derivatives/option/error-code#-1121-bad_symbol>)
    - [-1125 INVALID_LISTEN_KEY](</docs/derivatives/option/error-code#-1125-invalid_listen_key>)
    - [-1127 MORE_THAN_XX_HOURS](</docs/derivatives/option/error-code#-1127-more_than_xx_hours>)
    - [-1128 BAD_CONTRACT](</docs/derivatives/option/error-code#-1128-bad_contract>)
    - [-1129 BAD_CURRENCY](</docs/derivatives/option/error-code#-1129-bad_currency>)
    - [-1130 INVALID_PARAMETER](</docs/derivatives/option/error-code#-1130-invalid_parameter>)
    - [-1131 BAD_RECV_WINDOW](</docs/derivatives/option/error-code#-1131-bad_recv_window>)
    - [-2010 NEW_ORDER_REJECTED](</docs/derivatives/option/error-code#-2010-new_order_rejected>)
    - [-2013 NO_SUCH_ORDER](</docs/derivatives/option/error-code#-2013-no_such_order>)
    - [-2014 BAD_API_KEY_FMT](</docs/derivatives/option/error-code#-2014-bad_api_key_fmt>)
    - [-2015 INVALID_API_KEY](</docs/derivatives/option/error-code#-2015-invalid_api_key>)
    - [-2018 BALANCE_NOT_SUFFICIENT](</docs/derivatives/option/error-code#-2018-balance_not_sufficient>)
    - [-2027 OPTION_MARGIN_NOT_SUFFICIENT](</docs/derivatives/option/error-code#-2027-option_margin_not_sufficient>)
  - [3xxx-5xxx Filters and other issues](</docs/derivatives/option/error-code#3xxx-5xxx-filters-and-other-issues>)
    - [-3029 TRANSFER_FAILED](</docs/derivatives/option/error-code#-3029-transfer_failed>)
    - [-4001 PRICE_LESS_THAN_ZERO](</docs/derivatives/option/error-code#-4001-price_less_than_zero>)
    - [-4002 PRICE_GREATER_THAN_MAX_PRICE](</docs/derivatives/option/error-code#-4002-price_greater_than_max_price>)
    - [-4003 QTY_LESS_THAN_ZERO](</docs/derivatives/option/error-code#-4003-qty_less_than_zero>)
    - [-4004 QTY_LESS_THAN_MIN_QTY](</docs/derivatives/option/error-code#-4004-qty_less_than_min_qty>)
    - [-4005 QTY_GREATER_THAN_MAX_QTY](</docs/derivatives/option/error-code#-4005-qty_greater_than_max_qty>)
    - [-4013 PRICE_LESS_THAN_MIN_PRICE](</docs/derivatives/option/error-code#-4013-price_less_than_min_price>)
    - [-4029 INVALID_TICK_SIZE_PRECISION](</docs/derivatives/option/error-code#-4029-invalid_tick_size_precision>)
    - [-4030 INVALID_QTY_PRECISION](</docs/derivatives/option/error-code#-4030-invalid_qty_precision>)
    - [-4055 AMOUNT_MUST_BE_POSITIVE](</docs/derivatives/option/error-code#-4055-amount_must_be_positive>)
