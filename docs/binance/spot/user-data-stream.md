# Binance Spot 用户数据流 (User Data Stream)

> 来源: https://github.com/binance/binance-spot-api-docs/blob/master/user-data-stream.md

## General Information

* 通过 WebSocket API 使用 API Key 订阅
* 支持 SBE 和 JSON 输出
* 账户事件**实时**推送
* JSON 中所有时间戳默认为**毫秒**

## User Data Stream 事件

### Account Update (账户更新)

`outboundAccountPosition` - 账户余额变动时推送，包含可能变动的资产。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "outboundAccountPosition",
    "E": 1564034571105,      // Event Time
    "u": 1564034571073,      // Time of last account update
    "B": [
      {
        "a": "ETH",          // Asset
        "f": "10000.000000", // Free
        "l": "0.000000"      // Locked
      }
    ]
  }
}
```

### Balance Update (余额更新)

充提或账户间资金划转时触发。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "balanceUpdate",
    "E": 1573200697110,      // Event Time
    "a": "BTC",              // Asset
    "d": "100.00000000",     // Balance Delta
    "T": 1573200697068       // Clear Time
  }
}
```

### Order Update (订单更新)

通过 `executionReport` 事件推送订单更新。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "executionReport",
    "E": 1499405658658,             // Event time
    "s": "ETHBTC",                  // Symbol
    "c": "mUvoqJxFIILMdfAW5iGSOW", // Client order ID
    "S": "BUY",                     // Side
    "o": "LIMIT",                   // Order type
    "f": "GTC",                     // Time in force
    "q": "1.00000000",              // Order quantity
    "p": "0.10264410",              // Order price
    "P": "0.00000000",              // Stop price
    "F": "0.00000000",              // Iceberg quantity
    "g": -1,                        // OrderListId
    "C": "",                        // Original client order ID (被取消的订单ID)
    "x": "NEW",                     // Current execution type
    "X": "NEW",                     // Current order status
    "r": "NONE",                    // Order reject reason
    "i": 4293153,                   // Order ID
    "l": "0.00000000",              // Last executed quantity
    "z": "0.00000000",              // Cumulative filled quantity
    "L": "0.00000000",              // Last executed price
    "n": "0",                       // Commission amount
    "N": null,                      // Commission asset
    "T": 1499405658657,             // Transaction time
    "t": -1,                        // Trade ID
    "v": 3,                         // Prevented Match Id (仅STP过期时可见)
    "I": 8641984,                   // Execution Id
    "w": true,                      // Is the order on the book?
    "m": false,                     // Is this trade the maker side?
    "O": 1499405658657,             // Order creation time
    "Z": "0.00000000",              // Cumulative quote asset transacted quantity
    "Y": "0.00000000",              // Last quote asset transacted quantity
    "Q": "0.00000000",              // Quote Order Quantity
    "W": 1499405658657,             // Working Time
    "V": "NONE"                     // SelfTradePreventionMode
  }
}
```

**Note:** 平均价 = `Z / z`

#### Conditional Fields in Execution Report

| 字段 | 名称 | 说明 |
|------|------|------|
| `d` | Trailing Delta | 仅追踪止损订单 |
| `D` | Trailing Time | 仅追踪止损订单 |
| `j` | Strategy Id | 仅在下单时提供 strategyId |
| `J` | Strategy Type | 仅在下单时提供 strategyType |
| `v` | Prevented Match Id | 仅 STP 过期的订单 |
| `A` | Prevented Quantity | 仅 STP 过期的订单 |
| `B` | Last Prevented Quantity | 仅 STP 过期的订单 |
| `u` | Trade Group Id | 仅 STP 过期的订单 |
| `U` | Counter Order Id | 仅 STP 过期的订单 |
| `Cs` | Counter Symbol | 仅 STP 过期的订单 |
| `W` | Working Time | 订单在 book 上时出现 |
| `b` | Match Type | 有分配的订单 |
| `a` | Allocation ID | 有分配的订单 |
| `k` | Working Floor | 可能有分配的订单 |
| `uS` | UsedSor | 使用 SOR 的订单 |
| `gP` | Pegged Price Type | 仅挂钩订单 |
| `gOT` | Pegged Offset Type | 仅挂钩订单 |
| `gOV` | Pegged Offset Value | 仅挂钩订单 |
| `gp` | Pegged Price | 仅挂钩订单 |

#### Order Reject Reason

| Rejection Reason (`r`) | Error Message |
|-------------------------|---------------|
| `NONE` | N/A (订单未被拒绝) |
| `INSUFFICIENT_BALANCES` | 账户余额不足 |
| `STOP_PRICE_WOULD_TRIGGER_IMMEDIATELY` | 订单会立即触发 |
| `WOULD_MATCH_IMMEDIATELY` | 订单会立即匹配 |
| `OCO_BAD_PRICES` | OCO 订单价格关系不正确 |

### List Status (订单列表状态)

当订单属于订单列表时，除 `executionReport` 外还会推送 `listStatus`。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "listStatus",
    "E": 1564035303637,
    "s": "ETHBTC",
    "g": 2,                            // OrderListId
    "c": "OCO",                        // Contingency Type
    "l": "EXEC_STARTED",               // List Status Type
    "L": "EXECUTING",                  // List Order Status
    "r": "NONE",                       // List Reject Reason
    "C": "F4QN4G8DlFATFlIUQ0cjdD",    // List Client Order ID
    "T": 1564035303625,                // Transaction Time
    "O": [
      { "s": "ETHBTC", "i": 17, "c": "AJYsMjErWJesZvqlJCTUgL" },
      { "s": "ETHBTC", "i": 18, "c": "bfYPSQdLoqAJeNrOr9adzq" }
    ]
  }
}
```

### Execution Types

| Type | Description |
|------|-------------|
| `NEW` | 订单已被引擎接受 |
| `CANCELED` | 被用户取消 |
| `REPLACED` | 订单已被修改 |
| `REJECTED` | 订单被拒绝（如 CancelReplace 中新单被拒但取消成功） |
| `TRADE` | 部分或全部成交 |
| `EXPIRED` | 订单按规则过期或被交易所取消 |
| `TRADE_PREVENTION` | 因 STP 过期 |

### Event Stream Terminated

用户数据流终止时推送。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "eventStreamTerminated",
    "E": 1728973001334
  }
}
```

### External Lock Update

Spot 钱包余额被外部系统锁定/解锁时推送（如用作保证金抵押）。

```json
{
  "subscriptionId": 0,
  "event": {
    "e": "externalLockUpdate",
    "E": 1581557507324,
    "a": "NEO",              // Asset
    "d": "10.00000000",      // Delta
    "T": 1581557507268       // Transaction Time
  }
}
```
