# Root adjudication — AFI approved-target cross-process durability & restart（第151轮）

## 裁定

- 接受 `D:/temp/afi_iter10_approved_target_http_root_20261016_r8`（authored spec `iteration10-approved-target-http-qa-root-r8.json`，sha256 `f054da8c2f8c91f1ba44d9850b92e5b9d0513d06fd5d96d41bfefe0359aaef86`；输出 spec 与 authored 字节等同；runner `iteration10-focused-qa.py` 冻结副本）作为本轮 AFI07 跨进程节点固定来源验收证据：exit 0、`2 passed, 2 warnings in 47.96s`（两条均为既有 warning：quandl 导入弃用、Starlette 422 常量弃用）、stdout sha256 `6338ff5e…`、stderr 为空、2147 个 source pin 前后不变、AFI head `b47a68c4` 运行前后不变。
- AFI 提交范围：仅 `src/backend/tests/test_service_read_authorized_http_integration.py` → AFI/dev `34f59117`（父 `b47a68c4`，`test(service-orders): verify approved-target durability across concurrent processes and restart`），提交 blob 与 spec pin `282d1307…` 字节等同（worktree == committed blob == QA 时读取字节）。README/scripts 6 项修改与 scripts/windows 3 项新增继续原样保留。
- SDK 父仓本轮为证据归档：无源码改动、无子包 gitlink 更新。

## R7→R8 增量与重建

- R8 = R7 + 五处字段改动：name（cross-process 命名）、nodes（新增第二节点）、output（`_r8`）、repositories pin（`a421354b`→`b47a68c4`，即第149轮提交后的 AFI head）、测试文件 pin（`a7c44613…`→`282d1307…`）。重建脚本 `build-afi-r8-reconstruction.py` 由 R7 复算并以 CRLF 字节序列化，输出 sha256 与 authored spec 完全相同，证明 R8 无未记录改动。
- 因测试文件字节已变更（新增跨进程节点），节点1在 R8 spec 内被重新执行属于新 pin 下的新验收，不构成对第149轮已接受 R7 QA 的重复；R7 输出目录与 R6 失败记录继续保留在 evidence/afi-approved-target-http-20261016/。

## 覆盖与语义（第二节点）

- `test_approved_target_operation_is_durable_across_concurrent_processes_and_restart`（26.91s）：策略审批 policy 发布 201、审批目标创建 201、状态 APPROVED。
- 仅同步两个真实 HTTP 请求的起点；AFI 预留、SQL 事务与 SDK 派发保持真实执行。两个独立 app 进程（`len(set(pids))==2` 且均 ≠ 测试进程 PID）对同一 intent 键并发提交：两侧均 200、intent_id 相同、均 ACKED。
- 恰好一行 operation（`approved_strategy_target`），携带 owner/principal/actor/delegate 身份引用、64 位 hex 的 `request_sha256`、selector/scope/account/strategy 引用，以及与该已批准目标相符的 approval_ref/target_sha256。一个被准入的已批准目标 intent 恰预留一个合成 Risk permit；所有重放的 HTTP/SDK 提交由持久记录服务，无第二次准入或 provider 调用。
- 同键异内容提交 → 409（`HTTP_409`）且 operation 行不变；重启代（新 app PID）同键提交 → 仍 200，operation 行不变；两进程与 Host 清理 exit code 均为 0。
- 第一节点 `test_approved_target_http_reaches_real_execution_host_and_replays_journal`（20.10s）在 R8 pin 下同样通过，语义与 R7 记录一致（本目录不重复叙述，见第149轮记录）。

## 失败史与不可复用性

- 本轮无新增失败运行；R8 输出目录仅此一份且已消费。历史 r1–r6 失败/诊断输出与 R7 单节点验收继续原样保留（`.codex-tmp/iteration10-approved-target-http-qa-root-r1..r7.json`、`D:/temp/afi_iter10_approved_target_http_root_20261016_r5..r7`、r6 失败归档见 evidence/afi-approved-target-http-20261016/），不覆盖、不删除、不原样重试。

## 边界

- 证据等级 `SYNTHETIC_HTTP_SDK_EXECUTION_HOST_APPROVED_TARGET_WITH_SYNTHETIC_RISK_AND_PROVIDER`：真实 AFI lifespan/HTTP、派生的 Execution ServiceHost 子进程、SQLite journal；合成 Risk 与 provider 叶，非真实账户、无外部 venue。
- AFI07 仍未完整：策略单、组撤销处置及“普通操作不改组腿”证据缺失；本节点不增加正式 PASS，正式累计维持 25/68。
