# SBE Market Data Streams

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/sbe-market-data-streams.md>

## General Information

- Base Endpoint: **stream-sbe.binance.com**或**stream-sbe.binance.com:9443**
- SBE schema: <https://github.com/binance/binance-spot-api-docs/blob/master/sbe/schemas/stream_1_0.xml>
- 所有交易对名称为**小写**
- 单个流: `/ws/<streamName>`
- 组合流: `/stream?streams=<streamName1>/<streamName2>/<streamName3>`
- 单个连接最长有效 **24 小时**
- 所有时间字段为**微秒**
- **需要 API Key**（仅 Ed25519 密钥）
  - 在 `X-MBX-APIKEY` header 中提供 API Key
  - 不需要额外的 API 权限来访问公开行情数据
  - 如使用 IP 白名单，仅允许指定 IP 使用
- 服务器每 20 秒发送 `ping frame`，1 分钟内未收到 `pong` 将断开连接
- 订阅/取消订阅请求以 JSON 发送，订阅响应也是 JSON
- 可通过 WebSocket frame 类型区分：订阅响应为 text frame (JSON)，事件为 binary frame (SBE)

## WebSocket 限制

- 每秒最多 **5**条入站消息
- 单个连接最多监听**1024**个流
- 每 IP 每 5 分钟最多**300** 个连接

## Available Streams

### Trades Streams (逐笔成交)

- *SBE Message Name:** `TradesStreamEvent`

- *Stream Name:** `<symbol>@trade`

- *Update Speed:** 实时

### Best Bid/Ask Streams (最优买卖)

- *SBE Message Name:** `BestBidAskStreamEvent`

- *Stream Name:** `<symbol>@bestBidAsk`

- *Update Speed:** 实时

> 注: SBE 的 bestBidAsk 流等同于 JSON 的 bookTicker 流，但支持 auto-culling 并包含 `eventTime` 字段。

- *Auto-culling:** 系统高负载时，可能会丢弃过时事件而只推送最新事件（按交易对维度）。

### Diff. Depth Streams (增量深度)

- *SBE Message Name:** `DepthDiffStreamEvent`

- *Stream Name:** `<symbol>@depth`

- *Update Speed:** 50ms

### Partial Book Depth Streams (部分深度快照)

- *SBE Message Name:** `DepthSnapshotStreamEvent`

- *Stream Name:** `<symbol>@depth20`

- *Update Speed:** 50ms

---

## SBE vs JSON 数据流对比

| 特性 | SBE | JSON |

|------|-----|------|

| 时间精度 | 微秒 | 毫秒(可选微秒) |

| 数据格式 | Binary (SBE) | Text (JSON) |

| 认证要求 | 需要 Ed25519 API Key | 不需要 |

| 深度更新速度 | 50ms | 100ms/1000ms |

| Best Bid/Ask | 支持 auto-culling | 不支持 |

| 可用流 | trade, bestBidAsk, depth, depth20 | 更多流类型 |
