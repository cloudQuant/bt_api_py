# Binance Spot API 错误码

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/errors.md>

错误由错误码和消息组成。错误码是通用的，但消息可能会有所不同。

```json
{
  "code": -1121,
  "msg": "Invalid symbol."
}

```bash

## 10xx - 通用服务器或网络问题

| 错误码 | 名称 | 说明 |

|--------|------|------|

| -1000 | UNKNOWN | 处理请求时发生未知错误 |

| -1001 | DISCONNECTED | 内部错误；无法处理请求，请重试 |

| -1002 | UNAUTHORIZED | 无权执行此请求 |

| -1003 | TOO_MANY_REQUESTS | 请求过多，建议使用 WebSocket Streams |

| -1006 | UNEXPECTED_RESP | 从消息总线收到意外响应 |

| -1007 | TIMEOUT | 等待后端服务器响应超时 |

| -1008 | SERVER_BUSY | 服务器当前负载过重 |

| -1013 | INVALID_MESSAGE | 请求被 API 拒绝（未到达匹配引擎） |

| -1014 | UNKNOWN_ORDER_COMPOSITION | 不支持的订单组合 |

| -1015 | TOO_MANY_ORDERS | 新订单过多 |

| -1016 | SERVICE_SHUTTING_DOWN | 服务不再可用 |

| -1020 | UNSUPPORTED_OPERATION | 不支持的操作 |

| -1021 | INVALID_TIMESTAMP | 请求的时间戳超出 recvWindow |

| -1022 | INVALID_SIGNATURE | 请求的签名无效 |

| -1033 | COMP_ID_IN_USE | SenderCompId 正在使用中 |

| -1034 | TOO_MANY_CONNECTIONS | 并发连接过多 |

| -1035 | LOGGED_OUT | 请发送 Logout 消息关闭会话 |

## 11xx - 请求问题

| 错误码 | 名称 | 说明 |

|--------|------|------|

| -1100 | ILLEGAL_CHARS | 参数中包含非法字符 |

| -1101 | TOO_MANY_PARAMETERS | 发送了过多参数 |

| -1102 | MANDATORY_PARAM_EMPTY_OR_MALFORMED | 必需参数未发送、为空或格式错误 |

| -1103 | UNKNOWN_PARAM | 发送了未知参数 |

| -1104 | UNREAD_PARAMETERS | 并非所有发送的参数都被读取 |

| -1105 | PARAM_EMPTY | 参数为空 |

| -1106 | PARAM_NOT_REQUIRED | 发送了不需要的参数 |

| -1108 | PARAM_OVERFLOW | 参数溢出 |

| -1111 | BAD_PRECISION | 参数精度过高 |

| -1112 | NO_DEPTH | 该交易对没有挂单 |

| -1114 | TIF_NOT_REQUIRED | 不需要 TimeInForce 参数 |

| -1115 | INVALID_TIF | 无效的 timeInForce |

| -1116 | INVALID_ORDER_TYPE | 无效的 orderType |

| -1117 | INVALID_SIDE | 无效的 side |

| -1118 | EMPTY_NEW_CL_ORD_ID | newClientOrderId 为空 |

| -1119 | EMPTY_ORG_CL_ORD_ID | origClientOrderId 为空 |

| -1120 | BAD_INTERVAL | 无效的 interval |

| -1121 | BAD_SYMBOL | 无效的 symbol |

| -1122 | INVALID_SYMBOLSTATUS | 无效的 symbolStatus |

| -1125 | INVALID_LISTEN_KEY | listenKey 不存在 |

| -1127 | MORE_THAN_XX_HOURS | startTime 和 endTime 间隔过长 |

| -1128 | OPTIONAL_PARAMS_BAD_COMBO | 可选参数组合无效 |

| -1130 | INVALID_PARAMETER | 参数数据无效 |

| -1134 | BAD_STRATEGY_TYPE | strategyType 小于 1000000 |

| -1135 | INVALID_JSON | 无效的 JSON 请求 |

| -1139 | INVALID_TICKER_TYPE | 无效的 ticker 类型 |

| -1145 | INVALID_CANCEL_RESTRICTIONS | cancelRestrictions 必须是 ONLY_NEW 或 ONLY_PARTIALLY_FILLED |

| -1151 | DUPLICATE_SYMBOLS | 列表中交易对重复 |

| -1152 | INVALID_SBE_HEADER | 无效的 X-MBX-SBE header |

| -1153 | UNSUPPORTED_SCHEMA_ID | 不支持的 SBE schema ID 或版本 |

| -1155 | SBE_DISABLED | SBE 未启用 |

| -1158 | OCO_ORDER_TYPE_REJECTED | OCO 不支持该订单类型 |

| -1160 | OCO_ICEBERGQTY_TIMEINFORCE | icebergQty 使用时 timeInForce 须为 GTC |

| -1165 | BUY_OCO_LIMIT_MUST_BE_BELOW | 买入 OCO 限价单必须低于市价 |

| -1166 | SELL_OCO_LIMIT_MUST_BE_ABOVE | 卖出 OCO 限价单必须高于市价 |

| -1168 | BOTH_OCO_ORDERS_CANNOT_BE_LIMIT | 至少一个 OCO 订单必须是条件单 |

| -1194 | INVALID_TIME_UNIT | 无效的时间单位 |

| -1210 | INVALID_PEG_PRICE_TYPE | 无效的 pegPriceType |

| -1211 | INVALID_PEG_OFFSET_TYPE | 无效的 pegOffsetType |

## 20xx - 交易问题

| 错误码 | 名称 | 说明 |

|--------|------|------|

| -2010 | NEW_ORDER_REJECTED | 新订单被拒绝 |

| -2011 | CANCEL_REJECTED | 取消被拒绝 |

| -2013 | NO_SUCH_ORDER | 订单不存在 |

| -2014 | BAD_API_KEY_FMT | API-key 格式无效 |

| -2015 | REJECTED_MBX_KEY | 无效的 API-key、IP 或权限 |

| -2016 | NO_TRADING_WINDOW | 找不到该交易对的交易窗口 |

| -2021 | ORDER_CANCEL_REPLACE_PARTIALLY_FAILED | 取消替换部分失败 |

| -2022 | ORDER_CANCEL_REPLACE_FAILED | 取消替换全部失败 |

| -2026 | ORDER_ARCHIVED | 订单已归档（超过 90 天） |

| -2035 | SUBSCRIPTION_ACTIVE | 用户数据流订阅已激活 |

| -2036 | SUBSCRIPTION_INACTIVE | 用户数据流订阅未激活 |

| -2039 | CLIENT_ORDER_ID_INVALID | clientOrderId 与 orderId 不匹配 |

## Filter 失败消息

| 错误消息 | 说明 |

|----------|------|

| Filter failure: PRICE_FILTER | price 过高、过低或不符合 tickSize 规则 |

| Filter failure: PERCENT_PRICE | price 偏离加权平均价过大 |

| Filter failure: LOT_SIZE | quantity 不符合数量规则 |

| Filter failure: MIN_NOTIONAL | price *quantity 过低 |

| Filter failure: NOTIONAL | price* quantity 不在范围内 |

| Filter failure: ICEBERG_PARTS | 冰山订单拆分部分过多 |

| Filter failure: MARKET_LOT_SIZE | MARKET 订单数量不符合规则 |

| Filter failure: MAX_POSITION | 账户持仓达到上限 |

| Filter failure: MAX_NUM_ORDERS | 该交易对挂单数量过多 |

| Filter failure: MAX_NUM_ALGO_ORDERS | 止损/止盈挂单过多 |

| Filter failure: MAX_NUM_ICEBERG_ORDERS | 冰山订单过多 |

| Filter failure: TRAILING_DELTA | trailingDelta 不在允许范围内 |

| Filter failure: EXCHANGE_MAX_NUM_ORDERS | 交易所级别挂单过多 |

| Filter failure: EXCHANGE_MAX_NUM_ALGO_ORDERS | 交易所级别止损/止盈过多 |
