# ENUM Definitions

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/enums.md>

适用于 REST API 和 WebSocket API。

## Symbol status (status)

- `TRADING`
- `END_OF_DAY`
- `HALT`
- `BREAK`

## Account and Symbol Permissions (permissions)

- `SPOT`
- `MARGIN`
- `LEVERAGED`
- `TRD_GRP_002` ~ `TRD_GRP_025`

## Order status (status)

| Status | Description |

|--------|-------------|

| `NEW` | 订单已被引擎接受 |

| `PENDING_NEW` | 订单处于挂起阶段，直到订单列表的工作订单完全成交 |

| `PARTIALLY_FILLED` | 部分成交 |

| `FILLED` | 完全成交 |

| `CANCELED` | 被用户取消 |

| `PENDING_CANCEL` | 当前未使用 |

| `REJECTED` | 订单被引擎拒绝 |

| `EXPIRED` | 订单根据订单类型规则被取消（如 LIMIT FOK 无成交、LIMIT IOC 或 MARKET 部分成交）或被交易所取消 |

| `EXPIRED_IN_MATCH` | 订单因 STP 过期 |

## Order List Status (listStatusType)

| Status | Description |

|--------|-------------|

| `RESPONSE` | 列表状态响应失败操作 |

| `EXEC_STARTED` | 订单列表已下单或状态更新 |

| `UPDATED` | 订单列表中某订单的 clientOrderId 已更改 |

| `ALL_DONE` | 订单列表执行完毕 |

## Order List Order Status (listOrderStatus)

| Status | Description |

|--------|-------------|

| `EXECUTING` | 订单列表已下单或状态更新中 |

| `ALL_DONE` | 订单列表执行完毕 |

| `REJECT` | 列表状态响应失败操作 |

## ContingencyType

- `OCO`
- `OTO`

## AllocationType

- `SOR`

## Order types (orderTypes, type)

- `LIMIT`
- `MARKET`
- `STOP_LOSS`
- `STOP_LOSS_LIMIT`
- `TAKE_PROFIT`
- `TAKE_PROFIT_LIMIT`
- `LIMIT_MAKER`

## Order Response Type (newOrderRespType)

- `ACK`
- `RESULT`
- `FULL`

## Working Floor

- `EXCHANGE`
- `SOR`

## Order side (side)

- `BUY`
- `SELL`

## Time in force (timeInForce)

| Status | Description |

|--------|-------------|

| `GTC` | Good Til Canceled - 订单一直有效直到被取消 |

| `IOC` | Immediate Or Cancel - 尽可能成交，剩余取消 |

| `FOK` | Fill or Kill - 全部成交或全部取消 |

## Rate limiters (rateLimitType)

- `REQUEST_WEIGHT` - 请求权重限制
- `ORDERS` - 下单频率限制
- `RAW_REQUESTS` - 原始请求限制

## Rate limit intervals (interval)

- `SECOND`
- `MINUTE`
- `DAY`

## STP Modes

- `NONE`
- `EXPIRE_MAKER`
- `EXPIRE_TAKER`
- `EXPIRE_BOTH`
- `DECREMENT`
- `TRANSFER`
