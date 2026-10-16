# 第225轮：BT P5A 组单 Cerebro 接线结果归档

日期：2026-10-16。对象是 Backtrader strategy-bound group submit/query 与 durable Monitor residual/recovery adapter。

**Root R9 验收通过，证据等级为 BT_ADAPTER_CEREBRO_REGISTRY_ONLY。这是 adapter 集成组件交付，不新增正式用例 PASS；正式计数保持 39/68。** BT01/BT02 的既有第204轮正式 PASS 不在本轮重裁或重跑范围内。

## BT 交付

Backtrader dev 提交 438b127fcff2c0619b13835248ee3ef210cc0728，父提交 38f764dbf2d51adcba4c6a0901734639096365a3，精确六路径。Root postcommit receipt 记录 scope_count=6、index_empty=true；六文件 checkout 与提交内容逐字节一致。六文件 SHA-256 与字节数列于同目录 manifest；receipt 原字节位于 raw ZIP 成员 accepted-source/postcommit.json。

## Root 固定源运行

R3、R8、R9 原始目录均逐字节收入 raw-evidence.zip。R3/R8 失败原件保留为失败；只沿用其中已通过且源语义未变的独立节点，没有把失败运行改写为全绿。

| Root run | 被选节点 | 原始结果 |
|---|---|---|
| R3 | 主 Cerebro 节点及两个 ownership 边界节点 | 1 failed / 2 passed，exit 1，8.93s。主节点在 runtime 建立前因测试配置含 validator 不接受的 tuple 报 INVALID_NODE；两个 ownership 节点通过。 |
| R8 | 主 Cerebro 节点及 boolean cursor 反例 | 1 failed / 1 passed，exit 1，9.79s。boolean cursor 节点通过；主节点 fixture 在 coordinator context_validated 阶段把 mutable JSON 容器与 WireEnvelope 冻结后的 MappingProxyType/tuple 作不等价比较，provider 尚未调用、outbox 尚未写入。 |
| R9 | 仅先前失败的 test_group_residual_stays_strategy_scoped_in_shared_account_cerebro | 1 passed / 0 failed，exit 0，7.312s；stdout 记录一条既有 Quandl deprecation warning，stderr 为空。 |

R9 仅运行主集成节点，没有重跑 R3 ownership 或 R8 boolean cursor。Root result 报告 857 source pins、sources 与六个仓库 HEAD 前后相同。SDK run-time HEAD 为 98648af2ea3a24394537c9fbd414fb897fb38f11；Backtrader 测试基线为 38f764dbf2d51adcba4c6a0901734639096365a3，随后由本轮提交固定。

合并通过的四个独立节点：R3 的 test_strategy_group_route_rejects_runtime_owned_by_another_session 和 test_strategy_group_route_allows_unowned_registered_runtime_borrow（AST SHA-256 分别为 12ec82aa2ef84d9647677b9a579801a424a1ddb483ba473f9404831bb6a5ca8d 与 e80711c2b0827c2163ad989dea1a37cfdb8026717ec973a8ae1bb2f5ffa6d581）；R8 的 test_group_monitor_page_rejects_boolean_requested_cursor（AST SHA-256 b17248a501ad0e29784efe22c568f7b25de80a253fcf2d1d89046a9fdb7a4ac5）；以及 R9 主 Cerebro 节点。R3/R8/R9 的 session source SHA-256 均为 c492095af903e475b9a9269f845f9ec30f190eb063fe029fefc2b45c83664af3；R8/R9 group adapter source SHA-256 均为 77556c807b7cb17fe3477851108848b4b01016272f9775801d2524aaed8ac30c。

## R9 覆盖与边界

R9 通过正常 Cerebro 建立两个共享同一 Broker/账户的 Strategy。Strategy A 绑定自己的组单和只读 Monitor route；Strategy B 没有该 route，绑定尝试返回 GROUP_ROUTE_UNAVAILABLE。合成 coordinator/provider 位于实际 SDK ServiceClientRuntime 与 ServiceRegistry 调用边界后；Monitor 使用 DurableOutbox/MonitorReadService SQLite 读路径。测试先确认 Registry groups.submit 成功且 outbox 已持久化，再由 synthetic transport 丢弃回复，BT 返回 REQUEST_OUTCOME_UNKNOWN，随后按原 group ID 查询并按持久 cursor 续读，不重新提交。

持久残差为 RECOVERY_REQUIRED：首腿成交 0.10、次腿成交 0.04、基础残差 0.06，含 Strategy A 身份、sequence、event ID 与 journal generation。重放同一事件可读回，cursor 续读为空页且游标不变。Strategy A、B 的 Broker 状态在组操作前后均相同；synthetic provider 仅一次组调用，普通 broker.orders 写调用为零。Session 与两个 Runtime 正常关闭，清理报告无错误，Store 已断开，Registry 关闭。

本轮 coordinator、provider 和 transport 是合成端；Runtime/Registry API 与 SQLite Monitor 读路径是被测 SDK 组件。**没有启动 SDK ServiceHost、没有经过真实 ZMQ/CURVE socket，也没有调用真实 provider、账户或凭据。**因此不宣称真实 Host/网络故障、跨进程恢复或交易所行为已验收。

## 正式计划位置与下一条最小验收

正式迭代计划 §10 DIM09（正式迭代计划.md:781）要求 C/P/F 三腿比例/乘数、零/部分成交、第二/第三腿拒绝或 UNKNOWN、策略退出及协调重启、残差向量和预算守恒；SCN02 要求九个 unified 目录走实际 SDK/Backtrader 正常路径。E6 和 §13.5（正式迭代计划.md:648,1034-1042）区分 P5A 两腿与 P5B 同账户受限三腿。当前 adapter 对 intent.version != 1 fail-closed，request 与 residual projection 仍是 P5A 两腿标量合同。正式计划没有独立 BT03 正式案例行；若 BT03 是内部执行标签，仍应映射 DIM09/SCN02，不另计 PASS。

下一最小完整链：保留 v1 行为，为 BT adapter 增加严格 typed v3 三腿 submit/query/snapshot 与 residual-vector 投影，维持 Strategy/portfolio/route 身份闸门；随后通过真实 SDK ServiceHost + ZMQ/CURVE transport 运行正常 Cerebro，provider 保持合成叶端。新链至少覆盖 C/P/F PAIRED、一个中途拒绝或 UNKNOWN、owner/coordinator 重启后按原 group ID 查询与 durable cursor 恢复、不重复派发及预算/残差向量。DIM09 R174/R183/R187 已接受的 SDK service-level 合同、wire 与 coordinator 分支可复用，不重跑；新证据需证明其经 BT 正常 adapter/Host 链。完整逐条闭合后再由 Root 裁定 DIM09/SCN02，本轮 formal count 不变。

独立 reviewer /root/review_bt_store_feed 对 R9 冻结源码/spec 的 857 pins 和六仓 HEAD 静态复核，结论 GO_STATIC_FOR_ONE_ROOT_RUN；意见通过协作消息收到，未作为独立原始 reviewer 文件归档。

## 原始证据定位

- R3：D:/temp/iteration10-bt-p5a-group-cerebro-root-20261016-r3/；配置 fixture INVALID_NODE 和两个 ownership 通过均保留。
- R8：D:/temp/iteration10-bt-p5a-group-cerebro-root-20261016-r8/；主节点失败和 boolean cursor 通过均保留。
- R9：D:/temp/iteration10-bt-p5a-group-cerebro-root-20261016-r9/；仅主节点通过。
- Root postcommit receipt：SDK D:\bt_api_py\.codex-tmp\iteration10-r225-bt-postcommit.json，归档 SHA-256 2964e456c07fdcd8538b94ababb3b44023213376029e27e96ce9885836337742。
- raw-evidence.zip：700771 bytes，SHA-256 c6888ad50ee97580d50d8a19e4cba160b2147e8b8afe9bed0fbbedbd9abf9de4，包含 40 个成员；manifest 列出逐成员字节数与 SHA-256。
