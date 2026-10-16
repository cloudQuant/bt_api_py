# Risk order exposure 接线轮（2026-10-16）

本轮把真实 `RiskOrderEntryService`、SQLite journal、Risk gate 和 BtApi normalized provider observation 接入同一条 account-owner 服务路径，覆盖首次 FILLED 与 ACK 后 open-order 敞口观察。证据是固定源码上的本地合成 provider 验收，不是完整 RISK02 或真实账户验收。

## 验收记录

- SDK dev checkout 在候选捕获时为 `577009e41da7b14c2bf4eb900a026509493e1194`。Host 使用固定历史候选：SDK 基线 `213f1114f69416bbad250f1a226e5fa1ccc2638e`、Base `b4aabd1e17b9c7252fa7b4d266932087d99b5517`、Execution `743a0b9feef5b625fbd98269e8ab374f4d80adc8` 原始源码导出、Risk `5f6ba0b2d7dd7a5589392e299ce2ecb90fef9d7b`、Gateway `59b164ab7f89c6428ae6546103aad6058dab4c16`、Monitor `536a4f04747182749fabdac5e28624f80465bc53`、Transport `29ab21e714292a363538491e4ff8461d336d1dfc`。没有加载 Backtrader 源。候选是 r8 固定快照加本轮三个文件的逐字节 overlay；不声称 368 个输入都已提交或等同于当前整个 dev checkout。
- 固定候选、source manifest 与完整 overlay 来源见 `D:\temp\iteration10-risk-exposure-host-sdk-adapter-sdk-20261016-r20-source`、`D:\temp\iteration10-risk-exposure-host-sdk-adapter-sdk-20261016-r20-root-spec.json` 和其 `candidate-overlay-provenance.json`。root spec SHA-256 为 `ae6cd135e2a228cf29048cc5b1deb883ce0b59e7fa800b80f484536fd98d725a`；368 项 manifest SHA-256 为 `c9eafeaea8918cf86fca8b73c6d4cc14d928e54992aed0aec0e592d9561cea45`；overlay 来源记录 SHA-256 为 `fab629289ed1054295c1ce7dcac1b1dde3c0e27e01c61ce6020e1eb3d583f189`。
- Root 在 `D:\temp\iteration10-risk-exposure-host-sdk-adapter-sdk-20261016-r20-host` 消费唯一 spawned Host 节点 `test_spawned_live_gross_terminal_and_post_ack_order_exposure_lifecycle`：launch `d0f029` / session `62438`，completion `4c8f9e`，pytest exit 0，1 passed、1 个既有 `asyncio_default_fixture_loop_scope` 配置警告，stderr 为空。368 个来源输入前后完全相同。结果文件 SHA-256 `5c29124170bd77553649f4ec52ebfcbc07737d5a41562ce8b0c11ef79b2af8eb`；stdout SHA-256 `a1d34a4d7bb3509e8bfd94f7927ee73dc634898e195da2eae28e07182ee3a35d`；stderr 是空文件；JUnit SHA-256 `9061bc148cde68e80202e13890c4111a79a197f56fbd888a2d85c63410ef7002`。
- Host 与 strategy 子进程都通过协议 close 返回、在时限内 join 且 exit code 为 0；测试的 fallback reaper 断言没有 terminate/kill 或清理失败。测试覆盖 FILLED、ACK、超限拒绝、部分成交及重复查询、信息不完整时 UNKNOWN 保留先前 Risk 敞口、完整 CANCELLED 释放 leaves、取消重复查询和释放额度后的新订单。
- 先前 R19 无 Host 节点在 `D:\temp\iteration10-risk-exposure-nohost-sdk-20261016-r19` 以 exit 0 通过 2 项（4.89 秒）：`test_in_process_live_gross_ack_partial_unknown_cancel_lifecycle` 与 `test_post_ack_unknown_requires_exact_persisted_event_shape`。命令使用 `C:\anaconda3\python.exe -B -m pytest --noconftest -q` 和隔离 `pytest.ini`；stdout SHA `06601cdef6823a8c63750e0f5e46837d07974377cce837b981bd6a218b6320e9`、stderr 为空、JUnit SHA `c88cda3e6123238b8270da0418b3ca6d64afda123a1990cf53cecfe5b1ed1b3e`。这项本地结果不替代上面的 spawned Host 证据。

## 最小交付范围

建议本轮只提交以下三个文件：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `bt_api_py/service_risk_order_entry.py` | 145,784 | `dea50da0492fd8dfe026defb0589ec87bb92a464d2210b9f8f6ad3813e198eff` |
| `bt_api_py/service_risk_order_journal.py` | 224,074 | `68389c09f427f183d19a47c5dcad2931e0ba61cf7535c6af7286d70e53c46187` |
| `tests/unit/test_service_risk_order_exposure_integration.py` | 66,853 | `56a7e228644bdadb25588f525dc5823e02f7788d18826238b0394d61822609f1` |

Risk-only integration test module remains a source/runtime fixture dependency in the candidate manifest, but no dirty helper hunk is required: all names used by the exposure test already exist at SDK HEAD. Its current dirty diff adds two typed-terminal tests, neither invoked by this Host node; do not include those pending tests in this round. The spawned-process helpers are already committed.

Other dirty paths are out of scope. `service_components.py` differs from HEAD only by Monitor formatting and the unused `TrustedRiskPolicyBinding` `__all__` export; the account-owner implementation is already in HEAD and the test does not import that export. `service_registry.py` has formatting-only changes. `test_service_order_entry_components.py` contains account-books work. The standalone `test_service_risk_order_journal_decimal_match.py` and group-runtime bundle test are outside the 368-entry candidate and were not run. Do not stage those paths or unrelated `.codex-tmp` / `%SystemDrive%` artifacts.

The fixed 368-entry candidate matched 365 current checkout source files. Three source inputs intentionally remain at the fixed historical snapshot pins:

| File | Candidate SHA-256 | Current checkout SHA-256 |
|---|---|---|
| `bt_api_py/service_execution.py` | `bb9af549000c4ac1f3d78c55c21a59faec768ea7bceee99ade2edf58957cf85e` | `ded42b52272286c9dd74ef95da15ea1927a9454b18c7562ef7187f4ed83fba1b` |
| `bt_api_py/service_execution_account_books.py` | `c4261c122968bd8bf47b4e4958bc295481cfe6f43e4441d596ef1a161f0a64af` | `22166da944fe4e9ddade5f41b16e43678fae382184b520bb1fcf8e18065a452f` |
| `bt_api_py/service_execution_group.py` | `8f718bfd4ceb8f4bfe1e59f645764990b5f31f5118926051042cb882592c1ff2` | `281d55b08b8011bcfc637cdebf5157902eb4d6d637edd54ba19d740efe22b5f0` |

The latter two contain newer account-books/group work in the current checkout. Keep Host evidence bound to the fixed candidate; do not copy those newer files into this delivery.

## Remaining RISK02 scope

The baseline and provider are synthetic, single-currency USD with multiplier 1. This round does not establish real account/provider facts, trusted production baseline ingestion, FX or non-unit multiplier valuation, TTL/stale-fact behavior, or restart recovery across the full exposure lifecycle. Root should track those as later RISK02 work; this round is not a RISK02 completion claim.
