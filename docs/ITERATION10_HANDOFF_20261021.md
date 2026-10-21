# 迭代 10 交接（2026-10-21）

用户已要求收尾，后续开发交由其他人。当前停止新开发、测试、负载及账户调用。已验收的小范围代码已提交并推送；未验收候选单独保留，没有覆盖生产源码。

## 状态与入口

正式进度 **62/68**，本机范围 **62/65**，本轮新增正式 PASS 为 0。权威明细见[验收状态汇总](迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md)。

| 项目 | 当前状态 | 接手后的下一步 |
|---|---|---|
| LOAD01 | 10 策略、2 合成账户；完整矩阵未通过。最新 `63cb406c` pilot 在账户 owner 启动阶段报 `CAPABILITY_PLAN_MISMATCH`，未进入负载窗口。 | 使用包含 `99ef6370` 的已提交 dev 重新导出，核对失败代次及协调锁后跑新 120 秒 U0 pilot；成功后才跑 U0/N1–N6 各 30 分钟。 |
| VENUE01 | 真实 SimNow 尝试仍未取得账户基线和共享行情结果。R7 停在账户启动/关闭不确定。 | 先完成下述 startup-cleanup 候选的组合验证和分仓交付，再另行审查新代次只读尝试；不要重跑已消耗 R7。 |
| VENUE02 | 提交/撤单/部分成交/恢复平仓/Risk 的本机合成链路已有提交；真实账户完整链路未通过。 | VENUE01 生命周期和基线明确后，再执行最多 1 手的已授权模拟交易；撤销本轮未成交单、平掉本轮新增仓位，不动原仓位。 |
| ARB05 双机部分、DIST01、DIST02 | 用户明确暂缓，不计 PASS。 | 保留草稿，等待用户恢复这一范围。 |

## 已交付代码

仓库 `D:/bt_api_py`，分支 `dev`：

- `bff61f0de9e9b31fac95f4db54933bc38bfd03c6`：按请求缓存 bounded market reply 的精确字节预算。4 个不同本机检查通过；12 组新进程 ABBA 输出一致，小页/近上限页中位耗时分别降低 37.8%/51.3%。仅为局部性能证据。
- `63cb406c3fd1842ae9900c83619129f26b927378`：更新两个已过期的 N6 源码哈希，13 项源码绑定核对一致，验收门槛不变。
- `99ef63702a31cdae8469f6da90bf86b3cb314517`：修复 account-owner 同时未启用 approved-target 与 recovery-close 时的严格能力比较。真实本机 Host 的 3 个合成场景通过；额外 operation、错误 write 集合仍被拒绝。此提交之后没有重新运行 pilot。

上述提交已推送 `origin/dev`。相关范围、警告及验证边界见 [Host 修复记录](evidence/iteration10-load01-owner-capability-fix-20261021/round-record.json)和[最新 pilot 失败记录](evidence/iteration10-load01-u0-startup-plan-failure-20261021/round-record.json)。Host 聚焦测试的输出只在对话工具记录中，没有另存 stdout/XML；没有为归档重跑。

## LOAD01 恢复执行所需信息

- 继续使用现有 `.codex-tmp/load01-10x2-20261020/export_fixed_source.py` 和 `launch_one_profile_r4.py`；不要另造运行框架。导出器要求传入当前完整 HEAD、空暂存区和六个 SDK gitlink 对应的 checkout HEAD。
- R4 launcher SHA-256：`de68b1d56d8323a21337be48398203922babb1e360c64487fbf211b13843704d`。它要求导出器返回的 `source-export-pins` / `preflight` 哈希；完整运行另外要求成功 pilot 报告及 launch-observation 哈希。
- 最新已消耗失败目录：`D:/temp/load01-10x2-20261020/run-generations/63cb406c3fd1842ae9900c83619129f26b927378/runs/u0-pilot-63cb406c3fd1-1792546705817722400`。report SHA-256 为 `6503f5557cbcb587ff574101017f2a1b3b702a51135e81493bbb6a0c35eba758`，launch-observation 为 `7bd6bc066008c875e6d6914dfdba2fa7b6571304619aa9196877858a27e2d0a8`。
- 该次三 child 的 closed 标志为 true，退出码 0/1/1；无 termination request，正常关闭未证明。事后 launcher/controller/三个 child 的 PID 37504、36904、42068、14268、38656 均不存在；这不是正常关闭证明。
- 共享锁 `D:/temp/load01-10x2-20261020/run-generations/active-generation.lock` 仍保留本次失败 token，精确副本已进失败证据 ZIP。接手者须核对这次失败代次和进程状态，保留锁及证据再处理下一代次；不要直接删除锁后重试。R4 内置的历史 reconciliation 针对更早的 `337c6e73`，不能代替本次核对。
- `source-bff61f0de9e9` 是失败的源码预检导出；`source-63cb406c3fd1` 是已消耗 pilot 的固定源。两者保持原样，不能覆盖复用。
- 门槛仍为 200 source/s ±5%、单次 RPC 5 秒、恢复 10 秒、运行期 2 GiB 空闲保留。启动可用内存最低非 N5 为 3,204,448,256 B，N5 为 3,279,945,728 B。报告完整 p50/p95/p99；只作工程稳定性验收，不声明 HFT 达标。

## SimNow 未验收候选

本机目录：`D:/bt_api_py/.codex-tmp/venue01-startup-cleanup-candidate-20261021/`。

可转交 ZIP：`D:/temp/iteration10-handoff-20261021/unaccepted-candidates.zip`，258,118 字节，SHA-256：

`e6f39dbd733d6463b26585ae2e225b79afda800a13f3e04e5c5bac17615293b2`

ZIP 只包含明确选择的公开 Python 源码、测试、逐仓 patch、修复前副本和 QA 失败记录；不包含账户文件、环境文件、原生二进制、缓存或整个复制包。这是**本机未验收交接件，不在 Git 提交中**；异机接手时需另外复制 ZIP。

候选涉及 CTP client/provider、Gateway account-process 和父仓库 CTP process projector。它区分未创建 owner、工厂结果不确定、已创建 API 的启动清理；保留正 generation 活动会话规则。两种 stop API 都取消未完成启动，未解决的工厂状态禁止再次 start，SPI 构造结束前不提前 Release，preflight close 后重新核对 owner。

独立静态审查结论为 `GO_SOURCE_FAKE_ONLY`，不是实际关闭验收。作者增量 fake 检查为 7 passed/7 deselected，另一个受影响用例 1 passed/13 deselected；输出仅在对话工具记录。Root 新 wire 测试在 collection 阶段因 `.codex-tmp...tests` 相对包名失败，**0 个测试运行**；源码未改变。原 JSON/XML 在候选目录及 ZIP 中。下一步是将 pytest root/config 指向候选 tests 目录，核对导入路径后运行尚未执行的 4 个同步 fake-Pipe/projector 场景，再决定移植和规范化仓库测试。不要把其描述为真实 spawn 或真实账户证明。

当前候选源是完整副本，必须审查 patch 后移植到各仓现有 dev，不能盲目覆盖。匹配基线：CTP `ae14a757b8bb441af34610e5c8c3cab3f509b941`，Gateway `fd83283ad789a241c432cf8f3f716707acd816d7`，父仓库 `99ef63702a31cdae8469f6da90bf86b3cb314517`。先验收、提交 SDK 子包，再更新父 gitlink。

R7 最新事实见[工具会话观察](evidence/iteration10-venue01-readonly-r7-20261021/tool-host-exit-observation.json)：2026-10-21 01:40:40 UTC 两历史 PID 35644/35288 不存在；对 session 26270 的空读取返回 exit 1。没有发送退出、kill、手动 Release 或新账户操作；退出原因和正常原生清理仍未证明。原始失败及已消耗停止保持不变。原 25 文件快照保存在原 R7 目录的 `retained-public-sources-20261021.zip`；当前源码已因接受的 LOAD 修复改变，不能再声称当前磁盘仍匹配 R7 的旧全图。

## 保留的工作区状态

- 父仓库旧 `docs/evidence/iteration10-load01-n6-quota-source-20261018/round-record.json` 状态差异未动。
- CTP `tests/test_account_provider.py` 有原先 126 行 characterization WIP；ZIP 含其 patch，未提交。
- Gateway `market_hub.py` / `test_market_hub.py` 仍有旧状态差异；不要为清状态提交无意义换行变更。
- 两个双机草稿保留于 `scripts/iteration10_distributed_acceptance.py`、`tests/unit/test_iteration10_distributed_acceptance.py`，并进入未验收 ZIP。暂停前准备配置用例曾通过，本地端到端因能力规划不一致失败；未重新验收。
- Backtrader `D:/source_code/backtrader` dev 为 `d06d8a0f3dd5e0bbc590246c6055e60c1b05948b`，现有通知相关的两个提交 `33d149259`、`d06d8a0f3` 比本地 origin/dev 超前 2，属于其他工作，本次未代为推送；已有删除路径 `0` 保留。
- AI-for-Investor `D:/source_code/ai-for-investor` dev 为 `91cb89b882f9c4a2751abb05dce3d1824d4cdf9e`，与本地 origin/dev 对齐；已有 `src/backend/data/live_trading_instances.json.lock` 差异保留。

本机完整的有限仓库状态快照在 `D:/temp/iteration10-handoff-20261021/repository-status.json`。本次仅收尾本轮已接受的代码和交接文档，不清理其他人的改动或历史证据。
