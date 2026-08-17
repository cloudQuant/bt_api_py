# 行情与交易转发架构方案

日期：2026-06-16

## 结论

建议把行情与交易转发的核心能力放在 `bt_api_py`，`backtrader` 只作为消费端和策略运行端，通过一个轻量的 Store/Feed/Broker 适配器接入转发服务。

原因是 `bt_api_py` 当前已经负责交易所 REST、WebSocket、CTP、账户、订单、成交、资金、持仓等交易所侧对接；转发层本质上也是交易所接入层的上游抽象，应该在这里完成连接复用、数据标准化、行情扇出、订单路由、账户权限、风控、幂等和状态持久化。`backtrader` 更适合做策略调度、事件消费、回测和实盘策略生命周期管理，不适合作为所有应用共享的账户与行情网关。

推荐方案是：

- 在 `bt_api_py` 增加独立运行的转发服务。
- 行情转发使用 ZeroMQ `PUB/SUB` 或 `XPUB/XSUB`。
- 交易指令使用 ZeroMQ `ROUTER/DEALER`，或 REST/gRPC 提交指令加 ZeroMQ 私有事件流。
- 快照查询、订阅管理、健康检查、管理接口使用 REST/gRPC。
- UDP 只能作为可选的低可靠行情广播，不能用于订单、成交、账户、持仓等关键路径。
- 不建议一开始自定义裸 TCP 协议；如果用 TCP，应优先通过 ZeroMQ、gRPC、NATS 等成熟协议栈承载。

## 当前代码观察

### bt_api_py

`bt_api_py` 的主要职责已经接近交易所接入网关：

- `bt_api_py/bt_api.py` 里的 `BtApi` 统一创建交易所 feed、调用 REST 方法、发起订阅，并把 WebSocket 数据写入本地 `queue.Queue`。
- `bt_api_base` 提供 `ExchangeRegistry`、`PluginLoader`、`Feed`、`BaseDataStream`、WebSocket 包装和事件总线，是交易所插件统一注册和运行的基础。
- 各交易所插件把行情、账户、订单、成交等数据转换成统一数据对象后放入队列。
- `bt_api_ctp` 已经把 CTP 行情流、交易流、订单和成交事件也纳入类似模型。
- `bt_api_py/brokers` 已有 `BrokerAdapter` 抽象，但目前还没有真正的跨进程行情/交易转发层。

目前的限制是：数据主要进入单进程内的本地队列，交易请求也是本进程直接调用交易所 feed。多个策略进程共享同一条行情连接、共享同一账户订单入口时，当前结构缺少中心化的订阅管理、扇出、权限、风控、幂等、重放和状态存储。

### backtrader

`~/Documents/new_projects/backtrader` 是策略引擎和事件消费侧：

- `backtrader/cerebro.py` 已扩展了 channel/event 模式，可以把 tick、orderbook、bar 等事件派发给 broker 和 strategy。
- `backtrader/channel.py`、`backtrader/events.py`、`backtrader/channels/live_queue.py` 提供了事件、优先级和实时队列模型。
- `backtrader/brokers/tickbroker.py`、`mixbroker.py` 适合做回测或模拟成交。
- `backtrader/stores/btapistore.py`、`feeds/btapifeed.py`、`brokers/btapibroker.py` 已经是对 `bt_api_py` 的实盘接入适配层。

这些能力说明 backtrader 很适合消费转发后的行情和账户事件，也适合把策略订单转成外部订单命令。但它的生命周期、数据线、broker、strategy callback 都是围绕策略运行设计的，不适合承担独立的行情网关和账户网关职责。

## 必须满足的需求

1. 一条交易所行情连接可以供多个策略、多个进程、多个框架消费。
2. 一个账户可以被多个策略或交易用户使用，但必须经过中心化权限、风控、限频和幂等控制。
3. 行情需要高吞吐、低延迟，可接受部分 topic 的最新值覆盖或短暂丢包，但必须有序列号和必要的快照补偿。
4. 订单、撤单、成交、资金、持仓、账户状态不能丢，必须可追踪、可确认、可恢复。
5. 转发层不能只服务 backtrader，后续应能服务其他策略框架、监控、风控、Web 后台、撮合模拟器或数据落库服务。

## 方案对比

| 方案 | 做法 | 优势 | 劣势 | 适用性 |
| --- | --- | --- | --- | --- |
| A. 在 `bt_api_py` 实现转发核心 | `bt_api_py` 连接交易所，统一标准化、扇出行情、路由交易指令，backtrader 作为客户端接入 | 复用交易所连接；不绑定单一策略框架；账户、订单、风控边界清晰；可支持多个进程和多种消费端；更符合当前代码职责 | 需要新增服务生命周期、配置、持久化、鉴权、监控和客户端适配器 | 推荐 |
| C. 混合方案 | `bt_api_py` 做中心网关，`backtrader` 增加 Store/Feed/Broker 客户端 | 保留 backtrader 的策略优势，同时让转发服务独立可复用；演进路径清晰 | 需要维护一套客户端协议和适配层 | 最推荐 |

## 推荐架构

```text
               +---------------------+
               |   Strategy Process  |
               |  backtrader / other |
               +----------+----------+
                          |
                          | subscribe / order command
                          v
+---------------------------------------------------+
|                  bt_api_py Forwarder              |
|                                                   |
|  +----------------+     +----------------------+  |
|  | MarketDataHub  |     | OrderRouter          |  |
|  | - 订阅去重      |     | - 账户权限            |  |
|  | - 数据标准化    |     | - 风控/限频           |  |
|  | - topic 扇出    |     | - 幂等 client_order_id |
|  | - 快照/重放     |     | - 订单状态机          |  |
|  +--------+-------+     +----------+-----------+  |
|           |                        |              |
|  +--------v------------------------v-----------+  |
|  | StateStore / EventLog / Metrics / Health    |  |
|  +---------------------------------------------+  |
+--------------------+------------------------------+
                     |
                     | REST / WebSocket / CTP / FIX-like adapters
                     v
          +----------------------------+
          | Exchange Plugins / Feeds   |
          +----------------------------+
```

### 核心模块

`MarketDataHub`：

- 维护交易所连接和订阅引用计数。
- 对相同 `exchange + market_type + symbol + topic` 去重，只保留一条上游订阅。
- 将不同交易所的 tick、depth、kline、trade、funding、mark price 等转成统一事件。
- 给每个事件增加 `sequence_id`、`event_time`、`receive_time`、`source`、`schema_version`。
- 提供最新快照缓存和最近 N 条 ring buffer，用于新消费者补快照和短暂断线恢复。

`OrderRouter`：

- 接收多个策略或用户的下单、撤单、改单、查询请求。
- 强制要求 `strategy_id`、`account_id`、`client_order_id` 或 `idempotency_key`。
- 做账户权限、品种权限、最大持仓、最大订单量、频率限制、价格偏离、资金检查和 kill switch。
- 把指令路由到对应交易所 feed、CTP gateway 或 broker adapter。
- 维护订单状态机，并把 ack、reject、order update、trade fill、position update 广播给有权限的消费者。

`StateStore`：

- 订单、成交、账户、持仓事件必须持久化。
- MVP 可以用 SQLite 或 Postgres；如果后续需要高并发和审计，优先 Postgres。
- 行情可以先用内存 ring buffer，重要行情再异步落库。

## 协议选型

| 协议 | 适合用途 | 优势 | 风险/限制 | 建议 |
| --- | --- | --- | --- | --- |
| ZeroMQ `PUB/SUB` | 行情扇出 | 轻量、低延迟、Python 生态成熟、适合一进多出 | 默认不保证历史消息；慢消费者需要 HWM、丢弃策略和快照补偿 | 推荐作为行情主通道 |
| ZeroMQ `XPUB/XSUB` | 可观测订阅代理 | 可以知道下游订阅情况，便于按需连接上游 | 比简单 PUB/SUB 多一层代理复杂度 | 中期推荐 |
| ZeroMQ `ROUTER/DEALER` | 多客户端异步订单命令 | 支持多客户端、异步 ack、比 REQ/REP 灵活 | 需要自己设计请求 id、重试、超时和状态机 | 推荐用于交易命令 |
| REST/gRPC | 下单、撤单、查询、管理、快照 | 语义清晰、易测试、易鉴权、易接入后台 | 高频事件流不适合只靠 HTTP 轮询 | 推荐作为控制面和查询面 |
| WebSocket | 浏览器、轻量客户端事件流 | 接入简单，适合 Web UI 和外部系统 | 内部高吞吐 fanout 不如 ZeroMQ/NATS 简洁 | 可作为外部接口 |
| UDP | 可丢弃行情广播 | 延迟低、广播方便 | 无可靠性、无顺序保证、穿透和监控复杂 | 只可选用于非关键行情，不用于交易 |
| 裸 TCP 自定义协议 | 极致定制 | 可控性强 | framing、重连、背压、鉴权、观测都要自己实现 | MVP 不建议 |
| NATS JetStream | 可靠 pub/sub 和持久事件 | 语义完整、扩展性好 | 引入独立服务和运维成本 | 规模扩大后可替代或补充 ZeroMQ |
| Kafka | 大规模日志和回放 | 持久化、回放、生态强 | 延迟和运维成本较高 | 适合数据平台，不适合低延迟订单入口 |

推荐初始组合：

```text
行情实时流：ZeroMQ PUB/SUB
行情快照/订阅管理：REST 或 gRPC
交易命令：ZeroMQ ROUTER/DEALER，或 REST/gRPC command endpoint
交易私有事件：ZeroMQ PUB/SUB 按 account/strategy topic 广播
订单/成交持久化：Postgres 或 SQLite MVP
序列化：JSON MVP，后续切换 MessagePack 或 Protobuf
```

## Topic 与消息建议

行情 topic 示例：

```text
md.BINANCE.SWAP.BTC-USDT.tick
md.BINANCE.SWAP.BTC-USDT.orderbook.L2
md.OKX.SPOT.ETH-USDT.kline.1m
md.CTP.FUTURE.rb2410.tick
```

账户与交易 topic 示例：

```text
acct.main.orders
acct.main.trades
acct.main.positions
acct.main.balances
strategy.mean_reversion_01.orders
strategy.mean_reversion_01.trades
```

行情事件最小字段：

```json
{
  "schema_version": "1.0",
  "event_type": "tick",
  "sequence_id": 123456,
  "exchange": "BINANCE",
  "market_type": "SWAP",
  "symbol": "BTC-USDT",
  "event_time": 1781620000000,
  "receive_time": 1781620000123,
  "payload": {}
}
```

订单命令最小字段：

```json
{
  "schema_version": "1.0",
  "command_id": "uuid",
  "idempotency_key": "strategy-order-unique-key",
  "strategy_id": "mean_reversion_01",
  "account_id": "main",
  "exchange": "BINANCE",
  "market_type": "SWAP",
  "symbol": "BTC-USDT",
  "side": "buy",
  "order_type": "limit",
  "price": "65000",
  "size": "0.01",
  "time_in_force": "GTC"
}
```

关键原则：

- 行情和交易事件必须分通道、分 topic、分可靠性等级。
- 订单命令必须支持幂等，不能靠客户端重试时“猜测是否下单成功”。
- 所有私有账户事件都必须按权限过滤，不能简单广播给所有消费者。
- 策略进程不应该直接共享交易所 API key；API key 应只由 `bt_api_py` 转发服务持有。

## backtrader 接入方式

backtrader 侧建议新增或改造现有 `BtApiStore`：

- `ForwardingStore`：连接 `bt_api_py` 转发服务，管理订阅、快照和私有事件流。
- `ForwardingFeed`：把行情事件转换成 backtrader 的 tick/orderbook/bar/channel event。
- `ForwardingBroker`：把 backtrader 的订单转换成 `OrderCommand`，并根据 ack、reject、order update、trade fill 更新本地订单状态。

如果希望减少改动，可以让现有 `BtApiStore` 支持两种 backend：

```text
direct backend：当前模式，直接调用 BtApi
forwarding backend：通过 bt_api_py forwarding client 调用
```

这样回归风险较低，原有直接接入方式保留，新转发方式逐步替换。

## 实施路线

### 第一阶段：MVP

1. 在 `bt_api_base` 增加统一转发事件和命令 schema。
2. 在 `bt_api_py` 增加 `forwarding` 模块，先支持一个进程内的 `MarketDataHub` 和 `OrderRouter`。
3. 实现 ZeroMQ 行情 `PUB/SUB`。
4. 实现 REST/gRPC 或 ZeroMQ `ROUTER/DEALER` 的下单、撤单、查询入口。
5. 实现订单命令幂等、基础风控、订单状态持久化。
6. backtrader 新增 forwarding backend，跑通一个行情多策略消费和一个账户多策略下单。

### 第二阶段：可靠性

1. 引入 topic 订阅引用计数和上游订阅去重。
2. 加入 market snapshot、ring buffer replay、sequence gap detection。
3. 增加账户权限、策略权限、品种白名单、限频和 kill switch。
4. 增加 Prometheus metrics、结构化日志和健康检查。
5. 增加断线重连、订单状态恢复和交易所未决订单对账。

### 第三阶段：扩展

1. 高吞吐行情切换到 MessagePack 或 Protobuf。
2. 私有事件和订单事件可迁移到 NATS JetStream 或 Postgres-backed event log。
3. 提供 WebSocket 外部接口给监控后台或人工交易页面。
4. 针对低延迟内网行情，可增加 UDP multicast 作为附加通道，但必须保留可靠快照和补偿路径。

## 风险与处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 慢消费者拖垮行情转发 | 延迟升高或内存增长 | ZeroMQ HWM、按 topic 丢弃策略、最新值缓存、消费者隔离 |
| 订单重复提交 | 重复下单 | 强制 `idempotency_key`，服务端持久化命令和结果 |
| 多策略共用账户互相影响 | 风险暴露不可控 | 策略级权限、额度、持仓预算、风控规则、kill switch |
| 行情断线后策略状态不一致 | 信号错误 | sequence 检测、快照补偿、断线事件通知策略 |
| 私有事件泄漏 | 账户风险 | topic ACL、认证、每个客户端身份绑定 account/strategy |
| 转发服务成为单点 | 全部策略受影响 | 进程守护、健康检查、状态持久化、后续主备或分 account 分片 |

## 最终建议

把转发能力定义为 `bt_api_py` 的一个独立网关服务，而不是 `backtrader` 的内部功能。`backtrader` 继续承担策略引擎职责，通过 forwarding store/feed/broker 接入即可。

这个边界更清晰：

- `bt_api_py` 负责连接交易所、统一协议、复用订阅、管理账户、订单路由和风控。
- `backtrader` 负责消费事件、运行策略、生成订单意图、维护策略侧订单视图。
- ZeroMQ 负责内部低延迟流转，REST/gRPC 负责查询和控制面，持久化存储负责订单与账户事件可靠性。

这样既能满足“一个行情供多个策略使用”，也能满足“一个账户供多个交易使用”，并且不会把核心交易基础设施绑定到单一策略框架。
