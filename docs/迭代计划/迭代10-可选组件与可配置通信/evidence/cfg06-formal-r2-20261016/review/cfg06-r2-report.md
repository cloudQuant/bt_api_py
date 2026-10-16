# CFG06 r2 正式固定来源 QA 轮 — 执行报告（QA-round runner，2026-10-16）

- 执行者角色：QA-round runner（不裁定、不改任何 docs、未做任何 git 写操作、未编辑任何 tracked 文件；仅新建本报告与状态文件于 `.codex-tmp/`）。
- 权威计划：`.codex-tmp/cfg06-formal-r2-plan.md`；builder：`.codex-tmp/it10-build-cfg06-qa-spec-r2.py`；冻结 runner：`.codex-tmp/iteration10-focused-qa.py`（sha256 `8b7f470b…`）。
- 结论（供 root 裁定，非裁定本身）：**运行状态 exit 0，65/65 全通过，pins before==after，heads 不变；本轮材料齐备，可进入 root 翻账本裁定。** 披露块需按计划第 9 行换成"含 analytics 节点"的措辞后使用。

## 1. 精确命令与退出码

| 序 | 命令（工作目录 D:\bt_api_py） | 退出码 |
|---|---|---|
| 1 | 一次性设计检查：`ls` 两个目标路径均不存在 | 0（均不存在） |
| 2 | 端口预检：读 `_group_policy_host_fixtures.py`/`arb03._endpoint()`；`netstat -ano \| grep -i listening`（60 条）；`ps -W` 快照 | 0 |
| 3 | builder：`PYTHONIOENCODING=utf-8 C:/anaconda3/python.exe .codex-tmp/it10-build-cfg06-qa-spec-r2.py` | **0**（输出 `pins: 147`） |
| 4 | spec 独立复核：`C:/anaconda3/python.exe - <<EOF`（重算 147 pins + 地标 + 8 节点） | 0（0 失配） |
| 5 | runner：`PYTHONIOENCODING=utf-8 C:/anaconda3/python.exe .codex-tmp/iteration10-focused-qa.py .codex-tmp/iteration10-cfg06-qa-spec-r2.json` | **0**（pytest_exit=0；runner 终端 0） |
| 6 | 运行后核验：内联 python 解析 result/source-before/after/junit/exit/stdout/stderr + sha | 0 |
| 7 | 运行后 `ps -W` / 目录清点 | 0 |

- 唯一一次运行，未重试；无 abort 触发。
- 时间线（+0800）：14:09 设计检查；14:10:02 运行前 ps；14:10:06 pytest 子进程启动（winpid 77392）；14:10:48 pytest 完成；14:10:49 runner 收尾完成。**整轮 14:10:49 前完成，早于 15:05 时限 54 分钟**；15:05 后未运行任何命令。

## 2. spec 与 pin 复核（builder 输出）

- spec：`.codex-tmp/iteration10-cfg06-qa-spec-r2.json`，artifact_id `iteration10-cfg06-qa-20261016-r2`，evidence_level `FIXED_SOURCE_CFG06_FORMAL_COMBO3_HOST_MATRIX_WITH_POLICY_UNITS_AND_REGRESSION`。
- **8 NODES 确认**（顺序：闸门 / 控制面 / policy_catalog / bundle / execution_group / wire_reply / **analytics 控制面** / combo3）。
- pins：**147**（替代预演 142；预演 9 个失配 pin 已按最终字节重钉）。独立全量重算 147/147 与磁盘一致（0 失配）。
- 地标复核：`bt_api_py/bt_api.py`=`91363d1ced5b…caab4` ✓；`bt_api_py/_gateway_wire.py`=`85c8ce451430…3616c` ✓。抽查：analytics 测试 `8114606c…`、combo3 `d3e91b94…`（与 r171 提交字节一致）、fixtures `9ba0c856…`、runner `8b7f470b…` 全部匹配。
- repositories（builder 断言全 dev 分支）：bt_api_py=`ee3b05c2…`（MIG01-R3）；risk=`adb6ec58…`；base=`b4aabd1e…`；execution=`f5c03064…`；transport_zmq=`a397fdb4…`。

## 3. 运行结果（runner 输出目录：D:\temp\it10cfghost-formal-20261016-r2，未搬移）

- result.json：`pytest_exit=0`；`source_count=147`；`sources_unchanged=true`；`heads_unchanged=true`；finished_utc `2026-10-16T06:10:48.196901+00:00`（=14:10:48 +0800）。
- **pins before==after**：source-before.json 与 source-after.json 内容相等（147/147）；两文件 sha256 同为 `7d7e373c91c1d5fca215583df0f00419cefee015899e5436b11ee1532a23fd11`。
- **heads before==after**：5 仓逐一同值（见 §2）。
- exit.txt=`0`；stdout.log=941B，sha256 `710200c89b6c9fa7a187948eb1326498a5aa5652aa57ae80e50d1915562f3600`；stderr.log=0B（sha256 `e3b0c442…`=空串）。
- stdout.log 原文关键行：`65 passed, 2 warnings in 40.87s`；2 条为 zmq `_future.py` 的 Proactor/add_reader RuntimeWarning（无害，与历史一致）；**无任何 FAILED/ERROR**。
- 工件自证：输出目录 spec.json 副本 = live spec（sha `e680476d…`）；runner.py 副本 = 冻结 runner（sha `8b7f470b…`）；command.json 含完整 argv/env；running.json 记录 pytest pid 77392。

### 3.1 每模块 junit 计数（junit.xml，8/8 模块）

| # | 模块（classname 前缀 tests.） | 通过 | 非通过 |
|---|---|---|---|
| 1 | unit.test_service_group_policy_admission_gate | 13 | 0 |
| 2 | unit.test_service_group_policy_control_plane | 18 | 0 |
| 3 | unit.test_service_policy_catalog_control_plane | 5 | 0 |
| 4 | unit.test_service_components_group_runtime_bundle | 1 | 0 |
| 5 | unit.test_service_execution_group | 11 | 0 |
| 6 | unit.test_service_execution_group_wire_reply | 6 | 0 |
| 7 | unit.test_service_portfolio_analytics_control_plane | 6 | 0 |
| 8 | integration.test_group_policy_host_combo3 | 5 | 0 |
| — | **合计** | **65** | **0** |

- combo3 5 项明细（历史上 staging run-a 曾并发争抢失败的 `test_combo3_admitted_group_reaches_paired_and_gate_is_not_public` 本轮 8.0s 通过）：document scope denial 4.9s、document group denial 4.4s、admitted-group pairs + gate-not-public 8.0s、durable tamper/version drift 4.8s、role-alone cannot start public host 0.0s。
- junit 结构说明：pytest 默认将全部模块归入单一 `<testsuite name="pytest">`；上表按 testcase `classname` 分组统计（可由 junit.xml 复现）。
- **失败项：无。无失败日志需要保留（stdout/stderr 原样留在输出目录）。**

## 4. 机器条件与演练重叠依据

- SCN02 R6 演练进程（bash PID 13961 / WINPID 197610，anaconda python，13:44:48 启动）在本轮 pre-run（14:10:02）、pytest 窗口（14:10:06-14:10:48）全程在场；**14:12:46 快照时已自行退出**（晚于本轮完成，非本轮影响；其自身结局不在本任务范围）。另有两个 Oct-13 起的长寿 python（128016/132224，无关）全程在场。
- 重叠依据（预检结论）：(a) combo3 经 `arb03._endpoint()` 用动态空闲端口（`bind(127.0.0.1,0)`），非固定端口，无冲突面；(b) `netstat -ano` 60 条 LISTENING 中无一条归属演练 WINPID 197610（其为零监听出站客户端）；(c) 源码 pin 提供字节级证据，机器负载不改变判定；(d) 本轮 46s，重叠窗口仅 40.87s，远早于 LOAD01 时间门。
- 未触发任何 step-2 停止条件（无固定端口、无占用冲突）。

## 5. 原始证据路径（全部保留在原地）

- `D:\temp\it10cfghost-formal-20261016-r2\`：spec.json、runner.py、command.json、running.json、source-before.json、source-after.json、exit.txt、stdout.log、stderr.log、junit.xml、result.json、pytest-tmp\。
- `.codex-tmp/iteration10-cfg06-qa-spec-r2.json`（live spec）；`.codex-tmp/cfg06-r2-status.md`（过程状态）；`.codex-tmp/cfg06-r2-runner-stdout.log`（runner 终端输出：pid 行 + result JSON + `runner_exit=0`）。
- 未复制搬移、未删除、未覆盖任何文件。

## 6. 残余诚实（scope 限定，据缺审 §7/§8）

- 本轮为**合成/源码级**固定来源轮（无 live venue、无真实账户）；覆盖 = 8 模块 65 项在最终字节上的运行 + pins/heads before==after 自证。
- 跨进程真实独立分析进程属 AFI04 条款（计划 :796），不在本 PASS 认证范围；本轮 analytics 证据为同进程真实角色切片（6 项）。
- "无任意模块加载"由配置白名单/负例支撑，无运行期任意加载探针；`SCOPE_DENIED` 为注释级；INVALID_POLICY_VERSION/POLICY_VERSION_CONFLICT 为 owner 侧产码；r165 冻结 docstring 漂移保留不改；combo-3 staging run-a 并发争抢失败如实保留在历史记录。
- r154/r156 回执仅对其 pinned bytes 有效（今日漂移 20/170、12/172），其被本轮节点覆盖的片段以本轮字节为准。

## 7. 开放问题（供 root 裁定）

1. **披露块措辞**：计划第 9 行要求把账本披露块从"7 节点"替换为"含 analytics 节点"的措辞——本轮实际执行为 8 节点/65 项（13+18+5+1+11+6+6+5），请 root 在翻 PASS 时采用含 analytics 的替换文本；缺审 §7 披露块原文其余五条不变。
2. **演练早退**：SCN02 演练 13961 于本轮完成后（~14:12:30 前后）退出。不影响本轮（pins/heads 已定格于 before==after），但 SCN02 工作流可自行核对其结局；是否需要在 CFG06 叙事中提及由 root 决定（建议仅作机器条件注记）。
3. **staging run-a 语境**：本轮 combo3 为一次干净 5/5，r171 staging run-a 的 1-failed 为并发争抢历史事实、已如实保留；按缺审披露 (5) 以正式轮结果为准。
4. **时效注记**：本轮 pin 定格于 2026-10-16 14:10 的工作区字节（bt_api_py HEAD ee3b05c2）；此后任何源码/子模块提交都会使 pins 再次漂移，翻账本前请勿在相关 dev 树追加提交。

*报告人：QA-round runner；日期：2026-10-16 14:15 +0800；不翻动任何状态，仅供 root 裁定。*
