# CFG06 r2 正式固定来源 QA 轮 — 状态文件（QA-round runner，2026-10-16）

- 角色：执行者（QA-round runner）。不裁定、不改 docs、不做任何 git 写操作。供 root 翻账本用。
- 权威计划：`.codex-tmp/cfg06-formal-r2-plan.md`；builder：`.codex-tmp/it10-build-cfg06-qa-spec-r2.py`；冻结 runner：`.codex-tmp/iteration10-focused-qa.py`（sha256 8b7f470b…，与缺审 §2 登记一致）。
- 前置 DONE：MIG01-R3 已提交；D:\bt_api_py HEAD = `ee3b05c2ed81ea27598dab1c60bb6f6d3339324d`（在 r2 pin 闭包内）。
- 时限门：整轮须在 15:05（北京）前完成；15:07-15:45 归 LOAD01，不得运行。

## 步骤 1 — 一次性设计检查（14:09 +0800）
- `.codex-tmp/iteration10-cfg06-qa-spec-r2.json`：不存在 ✓
- `D:\temp\it10cfghost-formal-20261016-r2`：不存在 ✓
- 未删除、未覆盖任何文件；一次性设计成立。

## 步骤 2 — 端口/服务冲突预检（14:09-14:10）
- combo3 端点分配方式：`tests/integration/_group_policy_host_fixtures.py:440,453` 经 `arb03._endpoint()` 分配（`tests/integration/test_arb03_dim05_group_partial_fill_service.py:141-145`）= 动态空闲端口助手（`bind(127.0.0.1,0)` 取临时端口后关闭探针）→ **非固定端口**。
- SCN02 演练进程（bash PID 13961 / WINPID 197610，13:44:48 启动，`/c/anaconda3/python`）：`netstat -ano | grep -i listening` 全量 60 条 LISTENING，无一条归属 WINPID 197610 → 该进程为出站客户端，不持有监听端口。
- 结论：与 SCN02 演练窗口重叠可接受（计划允许，条件 = 记录依据）。依据：(a) combo3 用空闲端口，无固定端口冲突面；(b) 演练零监听；(c) 源码 pin 提供字节级证据，机器负载不影响判定。
- 快照：netstat 60 条（常见服务 135/445/3306/8382 等；无 combo3 相关固定端口）。

## 步骤 3 — builder 执行与 spec 检查（14:10-14:12）
- 命令：`cd /d/bt_api_py && PYTHONIOENCODING=utf-8 C:/anaconda3/python.exe .codex-tmp/it10-build-cfg06-qa-spec-r2.py` → **exit 0**。
- 输出：`pins: 147`（取代预演 142；预演 9 个失配已按最终字节重钉）；5 仓全部 dev 分支（builder 内置断言）。
- spec 检查（独立重算）：**8 NODES** ✓（analytics 在 wire_reply 与 combo3 之间）；147 pins 与磁盘全量重算 0 失配；地标：`bt_api_py/bt_api.py`=91363d1c… ✓、`bt_api_py/_gateway_wire.py`=85c8ce45… ✓；抽查：analytics 测试 8114606c…、combo3 d3e91b94…、fixtures 9ba0c856…、runner 8b7f470b… 均一致。
- spec：`artifact_id=iteration10-cfg06-qa-20261016-r2`；output=`D:\temp\it10cfghost-formal-20261016-r2`；evidence_level=FIXED_SOURCE_CFG06_FORMAL_COMBO3_HOST_MATRIX_WITH_POLICY_UNITS_AND_REGRESSION。
- repositories：bt_api_py=ee3b05c2…；risk=adb6ec58…；base=b4aabd1e…；execution=f5c03064…；transport_zmq=a397fdb4…。

## 步骤 5 — runner 执行（唯一一次，已完成）
- 命令：`cd /d/bt_api_py && PYTHONIOENCODING=utf-8 C:/anaconda3/python.exe .codex-tmp/iteration10-focused-qa.py .codex-tmp/iteration10-cfg06-qa-spec-r2.json` → **runner exit 0**。
- runner 输出目录：`D:\temp\it10cfghost-formal-20261016-r2`（唯一指定输出；未搬移任何文件）。
- 时间线（+0800）：14:10:02 运行前 ps 快照 → 14:10:06 runner 启动（pytest 子进程 winpid 77392）→ 14:10:48 pytest 完成 → 14:10:49 runner 写 result 完成。**全程 46s，远早于 15:05 时限（未触发任何 abort）。**
- ps 快照：运行前=run 窗口内均含 SCN02 演练 PID 13961（WINPID 197610，自 13:44:48）；另有 2 个 Oct-13 起的长寿 python（128016/132224，无关）。运行后 14:12:46 快照：13961 已不在（演练自行退出，晚于本轮完成时点，非本轮影响）→ 演练重叠仅发生在 pytest 40.87s 窗口内，依据已记录（空闲端口 + 零监听）。

## 步骤 6 — 运行后核验（全部通过）
- result.json：pytest_exit=0；finished_utc=2026-10-16T06:10:48.196901+00:00（=14:10:48 +0800）；source_count=147；sources_unchanged=true；heads_unchanged=true。
- pins before==after：source-before.json == source-after.json（147/147，内容相等；两文件 sha 均 `7d7e373c91c1d5fca215583df0f00419cefee015899e5436b11ee1532a23fd11`）。
- heads（before==after，5 仓）：bt_api_py=ee3b05c2…；risk=adb6ec58…；base=b4aabd1e…；execution=f5c03064…；transport_zmq=a397fdb4…。
- exit.txt=`0`；stdout.log=941B（sha `710200c89b6c…`，末尾 `65 passed, 2 warnings in 40.87s`，2 条为 zmq/Proactor RuntimeWarning，无 FAILED）；stderr.log=0B（sha e3b0c442…=空串）。
- junit.xml=65 tests / 0 failures / 0 errors / 0 skipped。按 classname 分模块（8/8 全覆盖）：
  1. test_service_group_policy_admission_gate 13
  2. test_service_group_policy_control_plane 18
  3. test_service_policy_catalog_control_plane 5
  4. test_service_components_group_runtime_bundle 1
  5. test_service_execution_group 11
  6. test_service_execution_group_wire_reply 6
  7. test_service_portfolio_analytics_control_plane 6
  8. test_group_policy_host_combo3 5（含历史上 staging run-a 并发争抢失败项 `..._admitted_group_reaches_paired_and_gate_is_not_public`，本轮 8.0s 通过）
- 工件一致性：输出目录 spec.json 副本 sha `e680476d…` == live spec；runner.py 副本 sha `8b7f470b…` == 冻结 runner。
- 失败记录：无（0 失败；无需保留失败日志）。

## 步骤 7 — 时限门
- 满足：整轮 14:10:49 +0800 前完成（≈ 15:05 门前 54 分钟）；15:05 后未运行任何命令（此后仅做只读核验与写状态/报告）。
