# CTP Gateway open_orders 身份迁移设计（未完成）

**状态：未完成。** 旧 Gateway 子进程没有可信的 SDK issuer/route 跨进程绑定。当前检测到活动 CTP 订单时，account provider 返回有限错误 `ORDER_OBSERVATION_IDENTITY_UNAVAILABLE`，不发布完整订单快照；这不是无损迁移完成，也不代表订单为空。

## 已核实的调用路径

- `bt_api/bt_api_ctp/src/bt_api_ctp/account_provider.py` 的 `_build_observation` 调用 `_project_open_orders`。旧映射把 `ExchangeID:OrderSysID`（缺少系统号时使用 TradingDay、FrontID、SessionID、OrderRef）写入 `AccountOpenOrder.order_id`，并把原生 OrderRef 写入 `client_order_id`。
- `bt_api_py/ctp_account_process.py` 的固定 CTP profile 在 child 中创建该 provider；`CtpSimNowSet2AccountProcessOwner` 将其装入 Gateway process provider。
- Gateway child 将 `AccountObservation.to_dict()` 经 Pipe 发送；`account_session_worker.py` 把 observation 持久化为基线；`service_component.py` 的 `account.snapshot` 返回该快照，`account_view_projection.py` 也直接序列化 open_orders 字段。因此旧 profile 被装配时，原生标识能够到达 Gateway 公共快照。
- 这不是 BtApi 当前默认的 CTP account-session factory。`BtApi.account_session` 使用 `store_account_session.py`；`BtApi.query_facade` 另行构造带 `order_observation_issuer` 的中立 query route。

## 缺失的跨进程接口

Gateway 当前 bootstrap 在 `bt_api/bt_api_gateway/src/bt_api_gateway/account_process.py` 只接受 `profile_ref` 与 `read_only`。child factory 只接收 profile_ref；`GatewayAccountSessionProcessBinding` 只含 account_key 和 provider。CTP Gateway owner 没有接收 SDK ledger scope、canonical account_ref、selected route/session binding 或 order-observation issuer 的入口。

`DurableOrderCorrelationIssuer` 在 `bt_api_py/order_correlation_issuer.py` 持有 SQLite connection，当前没有可供 Gateway child 安全重建同一 issuer 身份的 spawn-safe factory/opaque handle。不能把原 Python issuer 对象直接当作 child capability，也不能从 AccountKey、环境变量或可变账户别名推导 SDK account_ref/scope_ref。

## 接通合同

1. SDK trusted composition 必须基于精确的 selected CTP route 和当前 session，绑定 provider、environment、canonical account_ref、execution ledger scope_ref 与 session generation；route/session 变化或缺项时拒绝签发。
2. Gateway parent 必须把该绑定作为版本化、spawn-safe 的 opaque capability 传给 child-local issuer factory。能力只允许打开/调用同一个 SDK durable issuer；不得携带给 BT 使用的 secret、native identity 或任意路径。该 factory/resolver 是新增接口，当前不存在。
3. child provider 仅用 CTP 私有 provider identity 调用现有 base 合同 `ManagedOrderObservationIssuer.issue_order_observation_id`。managed 关联还需同一 trusted runtime 提供的 resolver，返回 exact OrderCorrelation 与 issuer reservation，并按现有 `bind_order_observation` / `resolve_order_observation` 校验；未验证时 client_order_id 必须为空。
4. AccountObservation 与 account.snapshot 只能包含稳定 opaque order_id 和已验证的中立 client_order_id。缺 issuer、scope、resolver、session currentness 或证据冲突时，整个 open_orders completeness 必须 unavailable，不能回退原生 ID 或伪装空集合。opaque ID 不构成撤单授权。

## 完成前的验证

完成该接线前需独立审查 Gateway bootstrap schema、parent owner 到 child factory 的精确 capability 绑定及 child session currentness。验收应覆盖稳定 ID、scope 隔离、managed/unmanaged 订单、AccountObservation/Pipe/journal/account.snapshot 序列化无原生标识，以及 issuer 缺失/过期时无完整快照。当前没有声称这些接口或验收已经完成；本轮未修改 Gateway bootstrap，也未运行测试或账户操作。
## 本轮边界复核

已对照当前运行时证明，现有 sealed context 不能直接搬进 Gateway child：`CtpExecutionRuntime.bind_selected_request_feed` 只绑定 runtime 自己的一只精确 feed；`build_selected_feed_order_provider_context` 要求该 feed 已连接；`validate_selected_feed_order_provider_context` 与 CTP `_CtpRuntimeOwnedRequestFeedBinding.validate_provider_context` 再校验同一 feed、同一 TraderClient、runtime/store/owner/session generation 和 owner thread 的对象身份。Gateway child 的 profile factory 会新建 feed/client，因此父 runtime 的对象身份 seal 在 child 中不会成立；把 Python context 或 issuer 对象 pickle/传 Pipe 也不能保留这份身份保证。

Gateway `serve_account_session_child` 的 provider factory 仍是 `Callable[[str], GatewayAccountSessionProcessBinding]`，child bootstrap 只接收 `{profile_ref, read_only}`，支持的 Pipe 操作是 bootstrap/connect/read_account/close。没有 child→parent 的 observation issuer RPC。`CtpReadOnlyAccountSessionProvider` 也没有 issuer、执行 scope 或 observation resolver 注入参数。已有 `DurableOrderCorrelationIssuer` 持有进程内 SQLite connection、lock 和 issuer secret；仅传数据库路径、`AccountKey` 或 profile 名不能证明它与当前 SDK ledger account/scope/session 是同一个受信 issuer。

因此当前 fail-closed 错误是必要的。接通需新增经过审查的版本化 Gateway bootstrap capability 和 child-local SDK issuer factory，或 child→parent 的有界 issuer RPC；两者必须把 authenticated child session、selected route/account_ref、ledger scope 与当前 session generation 作可验证绑定，并让 managed observation resolver 使用同一绑定。opaque `order_id` 仅作观察标识；撤单仍须由现有 managed cancellation owner 从 durable intent/target 证据独立授权。上述跨进程合同当前不存在，本轮没有扩大 Gateway 协议或 WIP。
## Child-owned session 可行性核对

现有 Gateway child 可以按固定 `profile_ref` 在 child 内创建自己的 CTP feed/client/session，但 parent 的 `_build_account_resources` 只调用 `owner.create_provider(account_session_ref)`；`GatewayAccountSessionBinding.account_scope` 是 Gateway 本地作用域，不是已经验证并传到 child 的 `ExecutionScope`。正常 `CtpExecutionRuntime` 确实持有可信 `execution_scope`，但它只为自己拥有的 selected feed/client/session 签发并验证 context；Gateway child 的新 feed/client不属于该 runtime。当前没有 SDK factory 能在 child 启动后，以已经验证的 child session 返回同一个 ledger `account_ref`/`scope_ref` 并绑定 issuer。

因此“child 自己登录后再本地生成 opaque ID”仍缺最关键的同账户/同 ledger 证明；child 自己创建新的 issuer 也不能保证跨刷新稳定，更不能关联 managed correlation。最小接通条件是 SDK trusted composition 提供版本化 child capability/factory（由可信执行 owner签发，引用同一个持久 issuer 和精确 ledger scope；child 在认证后向该 factory 验证当前 session/account），或实现带相同 route/session 校验的 parent-side issuer RPC。当前 Gateway bootstrap、CTP account provider 和 CTP runtime 都没有这个入口；在它落地前，Gateway active-order snapshot 必须保持 unavailable。
## 2026-11-01：spawn-safe authority 设计与真实 composition 盘点

### 当前可接线边界

源码盘点确认，A（base 中立合同与 parent authority）和 B（Gateway spawn/IPC 合同）现在都不能独立作为可用能力落到真实调用链；单独加入 DTO、registry 或 child RPC 会成为没有可信 issuer 调用者的脚手架。

- 当前真实 `BtApi.query_facade(exchange_name)` 会将 exact selected feed、execution context 和同一个 `DurableOrderCorrelationIssuer` 传入 `SelectedQueryRoute`。这条路径只服务进程内 SDK QueryFacade；旧 Gateway account-session owner 不使用它。
- `build_ctp_simnow_set2_account_operator_profile(...)` 只接收 Gateway config、authority、journal、Gateway account scope、principals、guard 和 owner 标识。它在内部创建 `CtpSimNowSet2AccountProcessOwner`，没有 execution runtime、`ExecutionScope`、execution store、canonical account_ref 或 issuer 注入参数；源码中没有另一个调用点给它补入这些值。
- `GatewayOperatorDeployment._build_account_resources()` 通过 `owner.create_provider(account_session_ref)` 创建子进程 provider。当前 Gateway account-process bootstrap 仅传 profile/read-only，且 child factory 只接 profile_ref。给协议加一个 capability 字段而没有上游 route issuer，不能证明该字段来源。
- CTP execution runtime 的 sealed context 绑定它自己拥有的 feed/client/store/owner/session generation。Gateway child 重新创建 feed/client；跨进程传输父 context 或 issuer 对象不会保留 exact object identity。
- `_ExecutionSession.execution_identity()` 对 CTP 使用账户 fingerprint 作为 ledger `account_id`，并明确 `physical_account_id_verified=False`。CTP TraderClient 的 query-session fingerprint 从当前登录回报的 BrokerID/InvestorID 计算。两者可用于比对 SDK 定义的 CTP 登录主体，但不证明物理账户身份；Gateway 当前既没有 trusted ledger route，也没有把 child 的 authenticated query-session scope 绑定到该 route。
- `DurableOrderCorrelationIssuer` 的 observation API 接收 caller-provided provider/environment/account/scope；Issuer 本身没有为 Gateway child 固定 scope 的 factory。把数据库路径、AccountKey、profile_ref 或可变 alias 传入 child 都不能代替 scope-bound authority。

因此本轮不增加 A/B 源码合同或 Gateway IPC 字段。真实 composition 先要有一个由 SDK managed CTP runtime 持有的 route/authority，能够把既有 Gateway operator profile 绑定到同一个 active CTP execution ledger 与 owner lease；目前这一入口不存在。Gateway 包当前未提交 WIP 位于 market-hub 文件，CTP account provider 也有其他 WIP；本轮仅追加本决策记录，未修改这些源码。

### 可落地的优先结构：父侧 scoped issuer service

未来优先实现 parent-side issuer RPC，而不是让 child 重开 SQLite issuer。child 只拿到一个由 `multiprocessing` spawn 安全传递的专用 `Connection` 端点；parent 保留 issuer、execution store、route、scope 和 owner lease。Connection 是可转移的 OS IPC handle，不是被 pickle 的 parent Python object。该接口仍属于 SDK 内部 trusted deployment boundary，不向 BT 暴露 provider identity 或 ledger scope。

每个 Gateway child generation 在 parent 建立一次性 authority registry entry，至少绑定：

- Gateway process PID、Gateway generation、IPC endpoint 与固定 child target/profile；
- 当前选中的 CTP execution route、exact `ExecutionScope`、canonical `account_ref`、scope key、execution store、durable correlation issuer；
- CTP runtime/source currentness validator、owner-thread invoker、ledger writer/fencing lease 与短期有效期。

上述对象只留在 parent 的 registry。child bootstrap 仅接收独立 IPC endpoint 和协议版本/一次性 challenge；wire request 不得携带 `account_ref`、`scope_ref`、SQLite path、issuer secret 或 caller-selected provider/environment。parent 每次处理请求都重验 child generation、active CTP route、current session、lease/fence 与 capability 状态。端点关闭、child restart、route 改变或 lease 失效时撤销 entry。独立 IPC channel 可避免在现有单 pending-request 的 account Pipe 上引入双向嵌套消息和响应串线。

在 CTP child 完成 `connect()` 后，固定的 CTP provider 必须从当前 exact `_QuerySessionScope` 产生一次性的 authenticated-session receipt。receipt 至少绑定 parent challenge、固定 route/profile/environment、child generation、CTP connection generation、trading day、read-only/login-ready 状态及登录回报的 account fingerprint。当前 `_QuerySessionScope` 的 issuer/client object identity 不能跨进程传递；SDK 尚无可供 parent 验证的跨进程 receipt issuer/validator，这是实现前置合同。不能让任意 mapping 或仅含 AccountKey 的消息充当 receipt。

parent 仅接受 trusted CTP source adapter 签发、与已登记 child endpoint 和 challenge 匹配的 receipt，并将其中 authenticated account fingerprint 与 active execution ledger 的 expected CTP fingerprint 精确比较。登录 principal 不匹配、receipt 过期、route/environment profile 未按固定映射绑定，或 parent 没有 active ledger route 时，authority 不建立；CTP active-order 快照保持 unavailable。`AccountKey.environment=simnow`、CTP native profile `set2_7x24_4000x` 与 execution ledger `demo` 是不同命名域，只能由固定 SDK route 明确映射，不能用字符串别名推导相等。

child 侧 authority client 只提供观察操作，不提供 scope 参数或取消操作。每条 open-order row 必须先由 CTP provider 按 child 当前 query-session/source 证据校验，再把 provider-native identity 作为仅在专用 IPC 中瞬时传递的私有输入。parent scoped service 以自身固定的 provider/environment/account_ref 调用 durable issuer 产生稳定 opaque `order_id`，不持久化 native identity。若需要关联 managed order，CTP 专属 resolver 还须用 parent 已绑定的 exact execution scope 通过 execution store 反查 OrderRef reservation，再用同一 issuer 的 provider-reference 索引解析 exact `OrderCorrelation` 和 reservation handle；只有双方 exact 匹配时才将中立 `client_order_id` 返回，并持久绑定 observation token。未匹配的外部订单仅可表现为 opaque token 与 `client_order_id=None`，不得转成取消权。当前 active-order projection 即使有了 issuer，逐行账户身份校验与 query receipt/currentness 也必须成立；此前列出的 row-level BrokerID/InvestorID HOLD 仍需独立解决。

`AccountObservation`、Pipe journal、持久快照和 `account.snapshot` 只能见中立 opaque order id/已验证 client id；任何原生 OrderSysID、OrderRef、FrontID、SessionID 都不得进入这些字段或诊断。没有有效 session receipt/issuer、行关联冲突、currentness 失败或 coverage 不完整时，不能投影完整 open_orders，也不能用空列表代替 unavailable。

### 分阶段依赖

1. **S0：接入真实执行 owner。** 为 CTP managed runtime 增加 SDK 内部 route resolver，使 Gateway operator profile 的 parent composition 能取得同一 active route、exact `ExecutionScope`、account_ref、execution store、issuer、owner-thread invoker 和有效 fencing lease。若 Gateway 部署没有这个 active runtime，应显式保持 unavailable；不能从 Gateway `account_scope`、账户配置或环境变量补造。
2. **S1：定义 scope-bound authority。** 在 `bt_api_py` 中由 active runtime 创建 per-child parent authority registry/facade，scope 只在 parent closure 中固定；Base 只需定义 provider-neutral IPC port/request/receipt DTO 和有限错误，不公开 raw scope/identity。Factory 必须从真实 route resolver 取得 authority；缺 route/fence 时不注册。
3. **S2：扩展 Gateway spawn contract。** 以版本化独立 IPC endpoint 将窄 client 交给固定 module-level child target；规定一次性 child binding、request id、有限操作、超时、关闭和 revoke。保留原 AccountSession Pipe 的请求/响应形状，直到新路径整体可用。
4. **S3：签发和校验 CTP child session receipt。** 由 CTP trusted source 模块在 exact live `_QuerySessionScope` 下生成 receipt；parent verifier 将 route、authenticated account fingerprint、generation、profile 与固定 child capability 关联，并在每次 observation 请求重验 currentness。
5. **S4：接 issuer 与 managed join。** 注入 scoped IPC client 到 `CtpReadOnlyAccountSessionProvider`；父侧复用同一个 durable issuer 与 exact execution store；native identity 仅用于 HMAC/OrderRef lookup，未绑定 correlation 留为 unresolved，任何 mismatch 都 fail closed。
6. **S5：闭合公开投影。** 为 AccountObservation/Pipe/journal/snapshot 加 exact opaque ID 与中立 correlation 验证，证明所有 serializing path 不含 CTP native identity，缺能力时明确 unavailable。随后再做独立源码复核和获授权的 source/fake 行为验证；本设计本身不代表实现或账户验收。

只有 S0 有真实调用方后，S1/S2 才能一起作为接线改动落到实际 Gateway profile。当前仅交付设计，没有能无损接入的现有 capability/factory/IPC；本轮未运行测试、未调用账户、未修改 Gateway/CTP 源码、未暂存或提交。