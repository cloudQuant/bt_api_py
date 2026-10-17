# ARB06：平仓授权的持久计划修订号锚点

本轮在现有 `D:/bt_api_py` 的 `dev` 修改。ARB06 完整验收尚未通过，正式进度仍为 60/68。

## 已观察到的失败

Root 消费了一次新的 R5 完整本机链路验收：`D:/temp/arb06-full-close-committed-r5-20261017/host-output-r5`。结果为 1 failed、2 warnings、29.52 秒；900 个源码文件和 8 个仓库 HEAD 在前后检查中保持一致，标准错误文件为空。Risk Host 回执记录 `cleanup_verified=true`、线程已正常 join、资源正常关闭。

首次 Risk 提交恰好调用一次。`startup-diagnostic.json` 记录内部异常 `GroupRollExitAuthorizationSourceError / GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH`，位置为 `service_execution_group_roll_exit_source.py:432`；RPC 返回 `GROUP_ROLL_EXIT_UNCERTAIN / UNSTARTED`。原始结果保留，没有把此次失败改写成通过。

只读检查本次合成数据库表明：授权固定于组修订号 18；其准确的 PLAN 记录位于修订号 19，SUBMIT_STARTED 位于 20，后续两个状态记录位于 21、22。Risk 动作仍为 PREPARED，observation 为空。原代码却将授权的不可变修订号与最新快照的 22 比较，因正常记录计划和提交事件而拒绝了该授权。

## 修复边界

修复将修订号增长后的授权绑定到已持久化的准确 PLAN，并复用现有 `_owner_roll_exit_actions` 事件历史校验。计划前缀修订号、action、授权完整 payload 和 digest 必须一致，推进后的事件后缀只允许现有平仓生命周期事件；其余当前来源、身份、作用域、订单和风险凭证字段继续严格比较。

准备和只读查询允许在记录计划前校验同一修订号。提交则必须有准确的 PLAN 和 START 记录；幂等的后续状态仍由现有持久化 CAS 和动作状态机约束。不会用简单的“小于等于当前修订号”代替校验，也不会在查询中增加提交或恢复。

## 定向验收

Root 在新的固定源码导出中执行一次定向 QA，结果为 **6 passed，2.22 秒，exit 0，无 warning，stderr 为空**；549 个文件 pin 和 7 个仓库 HEAD 前后保持一致。输出位于 `D:/temp/arb06-source-anchor-root-r1-20261017`，spec 为 `.codex-tmp/arb06-source-anchor-root-r1-spec-20261017.json`，SHA-256 `1aae62c901a66ff05bfe7626c90ecf05704e3e7b9b72f7e54acf25602d98583b`。

测试读取 R5 合成 group journal 的逐字节副本，以真实 `SqliteOrderGroupStore`、typed authorization、`assert_authorization` 和动作历史 fold 检查本次修复。registration validation 和 source rebuild 是替代的测试输入：该回归不证明完整 registration/source reconstruction 组合。六项覆盖已记录 PLAN/START 后的查询及提交、计划前准备及查询、计划前提交拒绝、不同授权修订号/action 拒绝，以及事件链摘要不符拒绝。

此轮证据为 `LOCAL_SQLITE_SOURCE_REVISION_ANCHOR_COMPONENT_ONLY_NO_HOST_OR_PROVIDER`，不是 ARB06 完整 Host 或真实账户验收。完整链路需要后续独立的新运行。

## 可恢复记录

- R5 spec：`.codex-tmp/arb06-full-close-committed-root-r5-spec-20261017.json`，SHA-256 `b7091e04c3de79f24c0ab8a1cf95da5b4efaa71e3771057b370837810125a748`。
- R5 stdout SHA-256：`cdcabf5ad35d040199b91b03841d446e358de5a993316f7b3a75305db556212b`。
- 本轮开始时 SDK HEAD：`3a69b7b0401f17504c2fba77cf22ecccae999f49`。
- 接受的源码：40,490 bytes / `2c923fa23a7e3b56a2e5dc74dd4fed6427654f569c0ae260482d0dcb6ce21c34`。
- 接受的测试：13,136 bytes / `68768c3e58e41172f7ab8be9e02cd2d3f017ddad82f245101c7c5c062262ecf6`。
- 合成 fixture：94,208 bytes / `ff9da1159237372ecb4d2f7fc5cbfb0678ffdb78ef9fa9e1a7b0bf5b78b9a546`；只含 R5 本机 synthetic group 数据，没有真实账户或凭据。
- 提交范围仅上述源码、测试、fixture 和本记录；提交哈希由 `.codex-tmp/arb06-source-anchor-postcommit-20261017.json` 记录。
- Backtrader 的两份完整链路候选测试仍未验收、未提交；其他已有修改保留。
