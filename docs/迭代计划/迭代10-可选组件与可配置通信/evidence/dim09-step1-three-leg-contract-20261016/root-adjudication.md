# 第174轮根裁定：DIM09 Step-1 — v3 三腿组合同与分段协调器链

（2026-10-16，根裁定·归档件；本目录为其证据归档）

## 交付物

- **execution 子仓**（branch dev）：提交 `6e89d306a943b251670efacc9eddd88cb13ee343`
  "feat(execution): add v3 three-leg group contract and staged coordinator chain"，
  4 文件，`+1430/-47`：
  - `src/bt_api_execution/group_order_contracts.py` — sha256 `67b50a4801d9f508cd9415103dd4eaab55780239fb62669877412d24318ab287`（1584 行）
  - `src/bt_api_execution/group_coordinator.py` — sha256 `81e79337f550c06a00a161787cfd9e39c0ac80c6fce278a179f45d52bdf20e1e`（1720 行）
  - `src/bt_api_execution/group_store.py` — sha256 `efba3cd16d7547072552b966210dfd2a9daa3cbf21e35aa98d8b524f0c5c8932`（929 行；仅 3 hunks：`create_or_get` version {2,3}、`update()` `residual_vector` 透传；无 schema/DDL 变更）
  - `tests/test_group_coordinator_three_leg.py` — NEW，sha256 `4c48f71ce5657e8d1e3456f724295c0bb4b19dc744ebb08171d09fc38dffb6a0`（1033 行，13 节点）
- **父仓**：提交 `c4148e8c`（仅 gitlink：execution `dc8bd7a3` → `6e89d30`）。

## 语义要点（钉在代码+测试中）

- v3 `residual_base_units` 标量 = 残差向量各分量绝对值之和（精确 Fraction → Decimal）；向量存于新快照字段 `residual_vector: tuple[Decimal, ...] | None`；v2 保持标量 `first_base - second_base` 逐字节不变且 `residual_vector is None`。
- v3 中途零成交（leg2/3 终态 `filled_quantity == 0`）fail-closed：后续腿不再提交，码 `CHAIN_INTERRUPTED_ZERO_FILL` → 存储 `chain_interrupted_zero_fill`，残差向量记录。
- v3 同侧不变量 `"v3 group legs cannot all use one side"`；v1/v2 保持原 `"P5A legs must have opposing sides"`。
- 分段下腿 + 整组归约（迭代收敛 ≤64 轮至公共 B*，实测归约至 B*=15 含向量 (0,2.5,2.5)）；v3 复用 `HEDGE_LEG_*` 存储事件类型；v3 reject 前零派发防御分支。
- 与 recon 的偏差（已如实记录于 author-status）：`CHAIN_STEP_SHORTFALL` "reject before dispatch" 在 v3 架构上不可达（合同可达性 + 分段下腿保证先有成交），保留防御分支并由测试覆盖合同级拒绝与可达运行分支（`chain_quantity_rounds_to_zero`）。

## 验收记录（作者 QA，`runs/` 全表）

10 个 run（`cmd.txt`+`stdout.log`+`exit.txt`；3 个基线目录仅有 stdout/缺 exit.txt，由复核披露）：

| run | 套件 | exit | 结果 |
|---|---|---|---|
| baseline-coordinator | coordinator | 0 | 12p（改动前基线） |
| baseline-test_group_budget | budget | （缺） | 9p |
| baseline-test_group_order_entry | order_entry | （缺） | 22F（CLEAN 基线外国红） |
| baseline-test_group_risk_integration | risk_integration | （缺） | 3p |
| newfile-exit／newfile2-…three_leg | 新文件 | 0 | 13p |
| final-coordinator | coordinator | 0 | 12p 1.70s |
| final-budget | budget | 0 | 9p 1.45s |
| final-order_entry | order_entry | 1 | 22F 2.41s，`^FAILED` 集合与基线 diff 为空 |
| final-risk_integration | risk_integration | 0 | 3p 1.60s |
| extra-service_component | service_component | 0 | 25p |

## 独立复核

`.codex-tmp/dim09-review-status.md`（复核证据 `D:/temp/it10dim09-review-20261016-r1/`，本归档 `review-status.md` + `review/submodule.diff` + `review/rerun-*/`）：

- **裁定 ADOPT**。4 文件哈希全对（67b50a48/81e79337/efba3cd1/4c48f71c；1584/1720/929/1033 行）；逐行读全部新测试；v3 不变量在合同中逐条核实；`residual_vector` 仅当 version==3 发射、ctor 拒绝 v1/v2 非 None（contracts 1405/1474-1479）。
- **v1/v2 字节兼容探针**：HEAD 包（dc8bd7a）与原包对照输出 34=34 行逐字节相同（`probes/probe.diff` 空，`new.out`/`old.out`），16 个序列化面（v2 quote、hedge_residual_unresolved 快照+事件、v1 legacy 快照+恢复、配对事件、往返）。
- **residual_vector 探针**（`probes/probe_vector.py` `vector_probe.out`）：v2 载荷无键且非 None 向量被拒 "only a version-3 group can carry a residual vector"；v3 finish ["0","2.5","0"] 往返相等；`_SCHEMA_VERSION=2` 前后相同、grep SCHEMA/CREATE/ALTER 为空。
- 外国失败核验：`^FAILED` 集合（22=22）基线 vs 终态 diff 空，原因为既存 `test_group_order_entry.py` 22 节点在 CLEAN HEAD 即失败（`ContractValidationError: paper execution requires an explicit owner domain`，测试构造 `ExecutionScope("paper", ...)` 缺 `paper_owner_domain`）；本条不改该文件。
- 复核披露：3 个基线目录缺 exit.txt（仅 stdout.log）；cosmetic 文残留（`GroupBudgetQuote` legs 注释/docstring 仍 2/v2、v3 快照 receipt 消息含 "version-2..." 字样）。

## 根裁定

- **GO（增量级）**。本提交为 DIM09 子条款 1（ratio/乘数合同）、2/3（分段零/部分成交与拒绝/UNKNOWN 中断）、5（残差向量与预算守恒）的纯协调器层落地；v2/v1 行为防回归由逐字节探针作证。
- **非正式 PASS**：DIM09 的 host wire 接受面（SDK S1/S7/S9 闸门）与真实账户三腿另计（Step-2 / 014_1 observe-only）；不改变 28/68。
- 本归档为事后固化（交付时子仓/父仓已提交）；归档副本自 QA/复核目录按类别节选：**排除**各 run 的 `bt/` basetemp（可由 cmd+stdout 重建）、`compat/old_pkg`（2.8MB 包副本，可由 dc8bd7a 重建）、`compat/run`、`compat/vector-probe` 探针工作目录；保留的每个文件均为原字节（CRLF 原样，`.gitattributes * -text`）。

## 证据等级

`FIXED_SOURCE_EXECUTION_SUBMODULE_V3_CONTRACT_UNITS_WITH_V1V2_BYTE_COMPAT_PROBES_ONLY`（子仓单元 + 逐字节兼容探针；无 host 线协议/真实账户声明）。
