# 第179轮根裁定：SCN02 R5 边界事实轮（012_1/012_2/007/015，2026-10-16）

性质：**事实采集轮，不构成 SCN02 正式 PASS**；评分留待 R7 裁定轮。本轮无仓库改动（Backtrader/bt_api_py 均未改）。

## 采集事实

- 012_1/012_2 `run.py --live`（无写旗标）→ exit 2 `BLOCKED / live_observation_unavailable`（`channel: observe`、`write_grant_result: not_requested`）——引用 R1+R2 已接受证据（逐字节引用，未重跑）。
- 015 `run.py --live --allow-write` → exit 2 `write_tier_not_implemented_for_decision_only_strategy`（拒绝先于 SDK 路径/凭据加载/evidence 目录/网络，写计数 0）——引用 R1+R2；并静态核对拒绝分支先于 py3.11 检查、`_ensure_dev_sdk_path`、imports、`_load_env`、evidence 目录创建。
- 007 `run_runtime.py`：`--help` 仅 `[-h] [--runtime-dir]`；`--config/--dry-run/--mode/--all` 全部 `unrecognized arguments` exit 2（注册路由拒绝）；无参 `LOCAL_REPLAY_PASS` 引用 R2。
- 007 遗留 shell 新跑（mission-exact env）：10 个 `ctp_*_examples/{backtest,live}/run.py` 全部 as-invoked exit 1 `ModuleNotFoundError: backtrader_runtime`（含旗标变体与无参调用）；根因（只读诊断）：围栏 `parents[3]/"runtime"` → `examples/unified/runtime`（不存在；正确 `parents[2]` 目标 `007_ctp/runtime` 存在）、且向 sys.path 插入 `D:/source_code` 而非 checkout root；由 iteration43 重定位 `db5ad2b21` 引入。补充环境（追加 repo root）下证实设计内拒绝完好：`PRESET_POLICY_VIOLATION(legacy_cli_arguments_not_supported / runtime_not_registered)`、in-process `legacy_direct_execution_not_supported`。
- 环境/完整性：全部运行零写、零网络、无凭据读取；porcelain 前后一致、11 个入口 blob 前后哈希一致；014_2 目录按指令完全未触碰。

## 裁定与后续

- 上述引用项不重复运行、不重复记账；007 围栏越位（parents[3]）确认为 MIG01 邻域的真实缺陷：行为仍 fail-closed（未执行任何运行时/框架/provider），但文档所述“shell 返回注册拒绝”不可 as-invoked 复现。登记为工作线 #50，R7 修复轮（示例 shell 路径数学修正 + as-invoked 复验）随后执行。
- 证据等级 `FACTS_ONLY_ZERO_WRITE_CITED_AND_FRESH_CAPTURES_WITH_LEGACY_FENCE_DEFECT_FLAGGED`；原文报告见本目录 report.md（86 件原始证据 + .gitattributes + 99 汇总）。
