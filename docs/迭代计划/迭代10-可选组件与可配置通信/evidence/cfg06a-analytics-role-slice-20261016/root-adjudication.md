# 第156轮根裁定：portfolio_analytics 真实角色切片（CFG06a）

- 裁定：GO（增量级）。范围仅限 CFG06a 增量：risk `service_analytics.py`、SDK `service_components.py` +87/-0、新控制面测试；不构成 CFG06 正式通过。
- 独立静态复核：GO（`.codex-tmp/cfg06a-review-status.md`，只读复核；复核者独立复跑新套件 6 passed、冻结回归对 6 passed）。
- 固定来源验收：`.codex-tmp/iteration10-cfg06a-root-spec-r1.json`（172 pins），输出 `D:/temp/it10cfg06a-qa-20261016-r1`：runner/pytest exit 0、12 passed、junit 12/0/0/0、sources/heads 前后不变、stderr 空、stdout sha256 `a67cb65ea7c214a8aea953ffa6700334255b1e3f4447409597ab91abb44a5422`。
- 提交：risk `131175a4`（父 a1485ee2；仅 `src/bt_api_risk/service_analytics.py`；DIM01 WIP 邻居 `core/admission.py`、`core/exposure.py` 原样保留）；父仓本提交 = `bt_api_py/service_components.py`、`tests/unit/test_service_portfolio_analytics_control_plane.py`、risk gitlink、进度/汇总文档与本证据目录。
- 证据等级：AUTHOR_WORKTREE_QA_AND_STATIC_SOURCE_REVIEW_ONLY（本机工作区真实 ZMQ+CURVE 线内；非跨进程/双机）。
- 剩余缺口：AFI04 跨进程 analytics 服务运行；group_policy 角色（CFG06b 在途）；单进程 store 无 OS 文件锁。
