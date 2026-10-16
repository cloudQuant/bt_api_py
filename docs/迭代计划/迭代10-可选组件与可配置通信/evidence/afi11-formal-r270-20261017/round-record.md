# AFI11 R270：故障隔离证据汇总（供 Root 正式裁定）

**建议状态：** `PROPOSED_GO_FOR_ROOT_FORMAL_DECISION`。Root 已表示 AFI11 formal GO；本包把支撑材料固定到可提交的 SDK 证据目录，供 Root 最终审阅。本包不改正式计划、进度表或矩阵，也不自行登记正式通过。

**证据级别：** 各轮均是固定源码导出上的本机合成集成/worker 证据。R240、R263 R6、R11 是作者本机节点结果；R218 是已接受的离线 Risk worker 生命周期与 SDK Host 集成证据。它们没有使用真实交易账户或生产 provider，不能表述为真实账户验收。R11 测试源码已在 AFI `dev` 的 `50f6a6826ce30fb108aefead33af1cc2633f266f` 提交，唯一源码路径为 `src/backend/tests/test_afi11_monitor_process_failure.py`；该提交只增加测试。

## 四轮证据与 AFI11 条款

- **R240 — AFI 应用进程退出与恢复。** SDK 节点 `test_afi11_app_process_failure_recovery_keeps_strategy_on_same_owner` 在 fixed-export、synthetic-provider fixture 上 1/1 通过。测试确实启动并终止其自有的 AFI FastAPI/Uvicorn app A，再启动不同 PID 的 app B；Strategy 会话、owner、policy 与 provider session 保持不变。必要 instrument risk facts 缺失的下单在 dispatch 前被拒绝；app 停机时一笔获准的合成意图仍经同一 owner ACK。合成 provider 构造/session-open 计数保持 1。详见 `../afi11-app-failure-r240-20261016/round-record.md` 与 `raw-r5.zip`（SHA-256 `65ffe000a949d93fd60026d92c419a544eb23328193a3ec5ecdd0492ad7bd704`）。这是作者本机单节点证据，含一条既有 Quandl deprecation warning。

- **R263 — analytics worker 启动超时隔离。** AFI R263 测试 `test_http_analytics_startup_timeout_reaps_only_worker_and_preserves_owner` 在本机合成流程 1/1 通过。HTTP analytics 首次任务返回 `TASK_WORKER_START_TIMEOUT`；测试断言只终止并回收该 task worker，Host、已授权 Strategy session/policy 保持可用，第二个任务可成功。缺 instrument facts 的请求在 provider 前拒绝，之后的有效订单仍被 owner ACK，合成 provider construction count 为 1。author-result 记录该次 worker PID `62536`、parent/Host PID `46440`；这两个数值仅是所归档 R6 author-result 中的本机观察。R270 的 `r263-r6-reference/` 保存了 result、JUnit、stdout/stderr 和 source/artifact pin JSON。固定候选源是 AFI commit `d1719aa980c22910c43b556a1c1e5ed10900c8e5` 下的测试 `src/backend/tests/test_afi11_analytics_worker_failure.py`（53,949 B，SHA-256 `306959bea86723a148d855d4818005e93112df274919e27db1f27b06fd358669`）；所依赖的 R236 Risk analytics worker 导出为 80,802 B，SHA-256 `1b148a4a923081302e507829d540aa9e45fc8ae3aac37eeb8f7f8dd3f1bda9c8`。R263 R6 也是作者本机单节点证据。

- **R218 — worker 有界生命周期基础。** 已接受的 Risk commit `ae9075ebd6f2bda2802edcb41a2dc66c859478fc` 固定 `service_analytics.py`（78,824 B，SHA-256 `3310eb1bd5def260f2e9e40624525b972594f79e8c7ca567b6e74e6a93148470`）与生命周期测试（SHA-256 `bc3dffe6fbdd7352b52d638fdf6a55a002a97baf395ba847073c37c0685f4aa7`）。Root 接受的结果包含 unit 6/6 和 SDK client → Risk Host → spawned worker integration 1/1；覆盖启动/执行 deadline、取消、终止回收、终态 CAS/晚到结果及清理不确定时保留占位。它支撑 R263 对 worker 隔离与回收的解释，不替代 AFI11 故障链或账户验收。详见 `../afi04-worker-lifecycle-20261016/round-record.md` 和 `manifest.json`。

- **R11 — Monitor 独立进程失联与恢复。** AFI 已提交测试 `src/backend/tests/test_afi11_monitor_process_failure.py`（45,365 B，SHA-256 `d82126a5d1d3e7327a600d4a695111fa68075c17978cd3aaf0c147c62b24d4e5`）在固定 SDK `e6cd356c2c281543202ccf5f5f2433aeebfc6d65` 与 Monitor `7c407634c092ba59383bd870a0b0b19c588ae451` 导出上，作者本机 1/1 通过。它终止并回收测试自有的 Monitor ServiceHost 进程，同时保持 Strategy owner/session/transport/policy；真实 HTTP control/status 边界在 Monitor 故障时返回不可用，持久化的 FREEZE 命令在同 endpoint/store 恢复后取得 applied receipt 和 Risk freeze snapshot。按既有 owner 政策，一笔合成 reduce-only CLOSE 获 ACK/只 dispatch 一次；缺事实请求被拒绝，恢复后的 OPEN 仍被拒绝且没有新增 dispatch。R11 stdout/JUnit 没有持久化 numeric PID，因此本包只引用测试中的精确 PID 断言，不填写数值。完整冻结材料和依赖 source pins 在 `r11/`。

## 综合判断与边界

四轮共同覆盖 AFI 进程、analytics worker、Monitor 的不同故障边界；R218 提供 worker 生命周期基础，R240/R263/R11 分别提供应用进程、analytics worker、Monitor 进程的 AFI/HTTP/owner 本机链路。必要事实缺失会阻止新增 dispatch，应用或 Monitor 故障不会创建第二个 provider session 或绕过 owner；允许的动作仍依同一 owner/risk policy 处理。根证据为真实本机进程/HTTP/SDK Host 配合合成 provider，而非 fake-only unit 测试。

R240、R263、R11 的 author-result 都保留各自 `formal_afi11_pass=false` 或等价的非正式声明；R270 只提出把这些证据按计划 AFI11 条款合并裁定。R11 的数值 PID 未记录在输出；不得由测试运行环境或其他轮次推算。未在本轮重跑任何 QA，也未修改共享 progress、正式矩阵、源码或 Git index。