# 第155轮裁定：Backtrader managed-paper V2 恢复日记账与回调投影交付、CFG06 提交组合复跑归档（2026-10-16）

## 范围与提交
- Backtrader 子仓（D:/source_code/backtrader，dev）提交 `b002afa2da66ca6d14f1623280109be8e653a1b6`（父 `48bde5d76bd666ce876763a613f27b92e9c62074`，"Add restart-safe recovery quote journal and session callback projection for managed paper V2"，10 文件 +4115/−42）。内容：恢复报价绑定（feeds/service_recovery_quotes.py，bar 失配 fail-closed、修订守卫）、recovery journal schema v3 与单消费租约（stores/service_execution_recovery.py）、会话接线（stores/service_execution_session.py）、Broker/Strategy 回调投影与 ACK/checkpoint 顺序（brokers/btapibroker.py、strategy.py、trade.py）、账户簿可选键（stores/service_strategy_account_book.py）、adapter 事件版本（stores/service_execution_adapter.py）、新单元测试（tests/unit/stores/test_service_execution_recovery_v2.py）。
- 父仓本提交仅归档文档与证据（Backtrader 非本仓子模块，其 dev 提交独立记录；无 gitlink 变化）。CFG06 既有证据目录新增 combo-verification.md。

## 验收证据（作者固定来源）
- r5 12passed/exit0；r6 238项 7fail/1err（修复前保留）；r7 `--noconftest` 602passed/18deselected/exit0、stderr 空；r8 conftest 4passed/exit0；父 48bde5d76 基线 14fail+4err 与 r7 18 个 deselect 一一对应（既有失败，非本次回归）。
- r7 十文件 source pins 与提交 blob 逐字节一致（独立复核 `git rev-parse` 核对）；排除项（ctp 三文件、`0`、docs/_internal）未入提交且工作区保持。

## 独立静态复核
- GO（.codex-tmp/btv2-review-status.md）：blob 10/10 一致、无额外路径；fail-closed 设计核对（未知 schema/事件版本拒绝、单消费租约、ACK 前持久 checkpoint、崩溃后水合不重执行、V2 仅 MARKET/FOK）；deselect 与基线 1:1；诚实性核对（LOCAL_SQLITE_BROKER_COMPONENT_ONLY，无 fresh-process/Host 声明）。无授权/安全洞。
- 非阻塞观察：回调半程（session/broker/strategy 投影）无专测；3 个 deselect 节点涉改但均已父级失败；quote mixin 为内存态（持久性在 intent-context/fill-projection 行）；SQLite 无剪枝。

## CFG06 提交组合独立复跑
- 候选树（父 99bec079 + 20 子模块 pins，risk a1485ee）与已接受 QA 相同命令与 pytest 配置：exit 0、6passed、junit 节点一致；170 pins 中 134 精确、30 仅 eol（export CRLF 伪差，blob 字节一致）、4 为 DIM01 WIP 邻居（替换为提交字节后仍 6/6）；子进程边界节点未加载主工作区 bt_api_py 字节（transitive 检查）。归档见 evidence/cfg06-policy-catalog-control-plane-20261016/combo-verification.md。

## 已关闭 / 剩余
- 关闭：第154轮裁定保留项"提交组合的独立复跑另行安排"（复跑通过）。
- 剩余：BT 真实 fresh-process 杀进程/重启恢复链与 SDK 侧 V2 经 Host 提升（BT02 仍部分）；SCN02 零 I/O 回放增量与 MIG01-R1 退役切面另行轮次记录。正式 PASS 仍 25/68。
- 证据等级：COMMITTED_SOURCE_BACKTRADER_PINNED_LOCAL_SQLITE_JOURNAL_AND_CALLBACK_PROJECTION_COMPONENT_ONLY_WITH_INDEPENDENT_STATIC_REVIEW。
