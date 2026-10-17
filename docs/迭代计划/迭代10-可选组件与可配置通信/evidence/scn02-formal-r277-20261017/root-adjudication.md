# SCN02 第277轮正式裁定（2026-10-17）

**裁定：正式 PASS；正式累计 51/68。** 本轮按正式迭代计划 §10 的 SCN02 条款，合并九个 unified 目录的读写边界与既有正常入口证据，并补验 012_1、012_2 两个公开只读 orderbook 入口。R276 已计入此前累计 50/68；本轮增加 SCN02 一项。

## 012 两个只读入口

两个入口分别为 012_1 的 run_live_observe 和 012_2 的 run_live，均使用公开只读 orderbook consumer。测试经两个 SDK client、ServiceReadProxy 和 BtApiStore 读取数据，并启动四个本机 synthetic Gateway 子进程：每个 venue 各有 account-session worker 与 market hub。断言覆盖 typed market key、depth、sequence、identity、合约单位、source descriptor 与正常关闭。读权限不授予写权限；没有建立跨 venue 执行对齐或可信同钟能力。

- 012_1_midfreq_cross_exchange：JUnit 为 1 test、0 failures、0 errors、0 skipped；stdout 的 pytest 摘要为 1 passed、1 warning。四个子进程均报告 ready、close acknowledgement、join、exit code 0，关闭后不存活，没有 forced cleanup，pipe/process 已关闭且没有剩余 owned PID。PowerShell 外层因向保留自动变量 $PID 赋值而在 WaitForExit 前失败，因此 Python/pytest 进程退出码未观察。不得将其记为 exit 0。
- 012_2_event_driven_cross_exchange：唯一 selector 为 1 passed，pytest/Python exit 0，耗时 21.75 秒；stderr 为空，有一条既有 Quandl deprecation warning。四个子进程正常关闭并退出，来源清单 1408 项前后不变。

本轮不重跑已接受的其他节点。原始调用、输出、JUnit、生命周期诊断及 source pins 见同目录 raw-evidence.zip；其 27 个成员的来源路径、字节数和 SHA-256 列于 manifest.json。

## 九目录能力映射

- 007_ctp：能力仍限于 LOCAL_REPLAY_ONLY 和离线 doctor；历史 writer 未恢复，生产 route 仍未注册并 fail closed。R179 留存的十个 shell as-invoked 失败是历史证据。随后 Backtrader commit c915c58f 修复十个 run.py 的 checkout-root fence，并记录 24 次验证；这项路径修复不表示恢复旧写入口或授予交易权限。
- 012_1_midfreq_cross_exchange：公开跨 venue orderbook 只读观察；bounded OKX 与 Binance demo mechanical smoke 仍须 operator/manifest 明确授权，research 写权限仍拒绝。
- 012_2_event_driven_cross_exchange：公开 orderbook 只读观察；HFT 仍为 failed/not admitted，bounded demo 也不构成 HFT 许可。
- 013_1_midfreq_cross_arbitrage、013_2_highfreq_calendar_arbitrage：已有正常服务观察入口；两腿写演练为独立的 registered-sim、policy 与审批约束路径，观察授权不能开启写权限，目录名称也不代表 HFT 资格。
- 013_3_sa_midfreq_simnow：单合约零写服务观察；写演练须走独立 bounded admission，并保留 max-actions、close 与 reconciliation 条件。
- 014_1_ctp_options_lowfreq：C/P/F 三腿正常 SDK/Backtrader service-entry 观察；三腿写操作仍受逐项批准、group/drill 合同和 ClockMapping 条件约束。DIM09 第275轮的服务端三腿状态、wire 与 recovery 证据沿用原裁定。
- 014_2_ctp_options_midfreq：decision-only 的 synthetic ServiceHost/SDK/Store/Feed/Cerebro 观察入口；可能因 clock 未获资格而保持 blocked，写操作不支持且明确拒绝。
- 015_ctp_options_highfreq：tick-only 零写观察；写操作不支持并明确拒绝，缺少 qualified same-clock evidence 时 HFT 保持 NOT_ADMITTED。

既有 014_1/015 shared-helper QA 未绑定当时 helper 的精确 hash；独立 review 指出这一限制。因此本裁定引用其既有 service-entry 证据范围，不声称该次 QA 验证了某个精确 helper 字节版本。其余目录级历史证据按原始轮次引用，没有在 R277 重跑。

## 裁定边界

两项新增 Host 运行均使用本机 synthetic provider，不代表真实账户、外部 venue、生产 provider 网络或真实写交易验收。orderbook age 检查采用 provider-observed/header time 启发式，不是可信时钟、execution freshness 证明或跨 venue 同钟资格。012_1 的 pytest 退出码未观察。012_1 的 bounded demo 仍须 operator/manifest 授权；012_2 仍未获 HFT admission。没有宣称盈利能力，也没有扩大任何写权限。

正式计划的九目录边界及既有证据索引见同目录 manifest.json 与 raw-evidence.zip；完整正式计数维持 68 项，当前正式 PASS 为 51/68。
