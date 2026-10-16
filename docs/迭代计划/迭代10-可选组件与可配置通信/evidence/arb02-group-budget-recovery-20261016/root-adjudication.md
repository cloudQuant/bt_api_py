# Root adjudication — ARB02 expired-unclaimed release & post-crash UNKNOWN hold（第152轮）

## 裁定

- 接受 `D:/temp/iteration10-arb02-group-budget-fixed-source-r11-qa-output`（authored spec `.codex-tmp/iteration10-arb02-group-budget-fixed-source-r11/focused-qa.json`，sha256 `a749673a2cc4c1f9…`；输出 spec 与 authored 字节等同；overlay manifest sha256 `fa6e9bc1…`）作为本轮 ARB02 固定来源验收证据：唯一节点 exit 0、`1 passed in 40.32s`、stdout sha256 `b6f1a594…`、stderr 为空、1808 个 source pin 前后不变、五个 live 仓库 head 不变（SDK `833b101c`、base `b4aabd1e`、execution `12372d82`、risk `756ffbd4`、transport `a397fdb4`）。
- 提交范围：Execution/dev 两路径 `dc8bd7a`（父 `12372d82`）；SDK/dev 五路径 + 本证据 + 该子仓 gitlink；父提交不含其它 WIP。

## 提交映射与字节一致性

- 7 个 overlay 路径在 R11 spec 中同时以候选路径与 live 绝对路径双列 pin；提交前复核 live 字节 == 候选字节 == overlay sha，提交后复核 Execution 提交 blob（`git show HEAD:<path>`）与 pin 字节等同。SDK 五路径在父提交中以工作区字节直接纳入，并由交付脚本对 live pin 逐路径断言。
- 候选基线为归档 SDK 导出 + 7 overlay（R7–R11 固定来源方法论），非 live HEAD 组合；验收语义限定在该固定来源与合成场景内。

## 诊断与修复（R10 → R11）

- R10（`77ad8fea…` pin）：exit 1、`1 failed in 82.31s`；R10-r2（`c57bc8bc…` pin）：exit 1、`1 failed in 42.21s`，失败点 `assert expired_started[1:3] == ("submit", _GROUP_EXPIRED_UNCLAIMED)`。两次原结果目录原样保留、随证据归档（qa/r10-failure、qa/r10-r2-failure），不覆盖、不删除。
- 根因判定：fixture 终态预期与现场语义冲突——deadline-crossed 已派发腿在 fill 观测阶段前失去 5s writer lease，生产 proof guard 正确把该尝试记为 UNKNOWN（GROUP_OWNER_FENCE_UNAVAILABLE）且不消耗已布防的 incomplete-fills fault（fault 保持 pending、provider 0 写 0 查，持久记录保持 DISPATCHING、预算 HELD/RECOVERY_REQUIRED、只读同 ID 重放零新增）。
- 修复为纯测试侧断言对齐（`c57bc8bc…`→`b24e4f99…`，生产代码零改动）；R11 在相同 7 路径 pin 下通过。

## 被排除节点

- `test_two_strategy_clients_compete_for_one_portfolio_hold_across_reopen` 在 R7–R11 各 spec 中均为 excluded_accepted_node：其函数体与已提交版本 `50a393e7…`（第144轮 `1ecee85e` 提交）逐字相同（文件字节变化仅来自其后新增函数的装饰器与空行），因此保持第144轮 R5 正式验收结论；本轮不重复运行已接受 QA。

## 覆盖与语义

- 过期未认领预留：完整账户快照在无任何子 intent 持久化前即过期，coordinator 在派发前拒绝（RESERVATION_NOT_ACTIVE），并在证明精确未认领向量后安全释放；Risk 读取 API 不做 TTL 清理时不搁浅组合容量。
- 崩溃后 UNKNOWN：已派发腿在协调器崩溃/租约失效后保持 UNKNOWN 与预算 HELD（RECOVERY_REQUIRED），无二次派发或 provider 查询；只读同 ID 重放零新增写入。

## 边界

- 证据等级 `FIXED_SOURCE_THREE_SERVICEHOST_CURVE_SYNTHETIC_PROVIDER_TTL_CRASH_UNKNOWN_SCENARIO_ONLY`：本机三 ServiceHost/CURVE 子进程与合成 provider，非真实账户、无外部 venue；未证明同时 SQLite 竞争与完整 ARB02 矩阵。
- ARB02 仍部分，不增加正式 PASS，正式累计维持 25/68。
