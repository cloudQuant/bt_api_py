# 第168轮根裁定：两个过期单测修复 + load01 边界守卫（提交 `72ebc681`）

- 裁定：GO（纯测试修复轮，无产品改动；无正式条款变化，正式 28/68）。依据：净 HEAD 导出版复现两红、归因实验锁定回归源、修复后两文件全绿且提交 blob 与工作区逐字节一致。
- 范围（3 个测试文件，+32/-4，提交 `72ebc681`）：
  - `tests/unit/test_service_config_scoped_execution_routes.py:407`：`close_clients(*_args)` → `close_clients(*_args, **_kwargs)`（arb02 清理 helper 现传 `allowed_call_errors=`，位置参数桩必须接受任意 kwargs）。修复前红：`TypeError: close_clients() got an unexpected keyword argument 'allowed_call_errors'`。
  - `tests/unit/test_service_host_integration.py`：execution 分支资源 provider 按 b63000b2 起的四键 builtin 补齐 `{"store", "targets", "dispatch": _UnusedDispatch(), "admission": {"allow_unprotected": True}}`（query-only 节点，新增模块级 `_UnusedDispatch` 桩 submit/cancel 均 raise；`account_books` 刻意不加）。修复前红：`RESOURCE_KEYS_MISMATCH`（service_components.py:1295-1296）。
  - `tests/unit/test_load01_hundred_strategy_30min.py`：新增 `_FULL_LOAD_SECONDS = 30 * 60`；重连窗口 guard `window >= 1 and window < _FULL_LOAD_SECONDS // 300`（window 6 名义起点恰为 1800 s 结束边界，进程看到 elapsed ≥ 1800 后才收到 stop_event 会调度出第 51 次重连，超出 5 窗口预算；仅名义起点严格位于负载时间线内的窗口可调度）；`duration_seconds = 120 if mode == "pilot" else _FULL_LOAD_SECONDS` 显式绑定。
- 验收证据（`D:/temp/it10regfix-20261016-r1`，归档 19 件）：
  - 00-baseline-head（净 HEAD 导出，20 gitlink 全部按 SHA 归档）：red1 exit 1（1 failed/20 passed）、red2 exit 1（1 failed）——两节点在净 HEAD 即为红，与 reg32 脏文件无关。
  - 10-attribution（可选归因）：`b63000b2^` 该节点 1 passed → `b63000b2` 1 failed；`git diff b63000b2^ b63000b2 -- tests/unit/test_service_host_integration.py` 为空 —— 回归源锁定为 `service_components.py` 的 account_owner 四键 builtin 变更。
  - 20-red1 21 passed / 30-red2 1 passed / 40-final（fresh basetemp）21 passed + 1 passed，全部 exit 0；sha256：config_scoped `306258ae…`、host_integration `97330020…`、load01 `8291666106…`（提交 blob == 工作区逐字节，LOAD01 冻结 pin 依赖此值）。
  - load01 边界：数值扫描证明恰 50 次重连、window 6 不再调度；narrow `-k` not-sustained 节点 4 passed（`D:/temp/it10load01-20261016-r1` + `.codex-tmp/load01-fix-status.md`）。
- 独立复核：不需要独立前复核（无产品字节、无条款声明）；作者侧复现-归因-修复-终验四步留档，附带 sibling-stub 全仓扫描（`def close_clients(` 仅两处，另一处为正常 helper 未改）。
- 条款映射：无正式条款变化；修复 dev HEAD 上两个既有红节点（同时清除 reg32/ARB06 等轮的披露 D3 项），并收口 LOAD01 运行器时长边界（衔接第 88/91 轮 load01 线）。
- 提交：`72ebc681` = 上述 3 文件 + 本证据目录（report.md + sha256.txt + two-files.diff + 5 步目录）+ 进度/汇总文档；673 MB 原始导出树保留于 `D:/temp/it10regfix-20261016-r1`（未全量入库）。
- 证据等级：TEST_ONLY_REPAIR_WITH_HEAD_EXPORT_ATTRIBUTION_AND_LOAD01_BOUNDARY_SWEEP。
