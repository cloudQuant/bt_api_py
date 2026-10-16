# 第211轮 Root 正式裁定：MIG04 指定 Binance 发行组合

**Root 裁定：MIG04 正式 PASS，仅适用于本文件定义的冻结 Binance 发行组合。** 本轮同步更新验收汇总和进度，正式通过数由 37/68 增至 38/68。

正式条款（`正式迭代计划.md:785`）：

> base/SDK/transport/四包/provider/BT/certlib/AFI 指定发行组合可安装；代码版本、schema/wire、extras 与状态库格式兼容或明确拒绝 | wheel/sdist 干净安装及正常入口跨仓集成，不用 submodule 源码导入代替发行验收；普通路径无四包/无 ZMQ

第161轮曾裁定 GO，但将 certlib 明确限为 archive 解压导入，未证明其可安装。第194轮据此按严格条款重开，原裁定与原始证据仍保留。本轮补上真实 sdist/wheel 安装和消费者组合来源证据，不沿用旧例外。

**证据闭合。** 第197轮构建了 `bt-api-binance-certlib 0.1.0` 的真实 sdist 和 wheel，分别在独立新 venv 离线安装。两个环境都从 site-packages 导入全部七个模块，并调用安装后的 `runner.main_case` 普通 dry 入口，exit 0。sdist 12,656 B / `deb5520156d2838f7def48e507f6963edf8ff0117e8b13f481f3700d886f0b26`；wheel 14,954 B / `164ca44a402ba8153cf55890b9b954ce29becf57ae9bbbc8feefc283a007a2c3`。元数据为 Python `>=3.11`、`PyYAML>=6.0,<7`。检查未修改 `default_env_path`，仅使用不存在的受控 `.env`，live 凭据发现未测。

消费者 R1 的 steps 01–05 均已 exit 0、stderr 空；本轮没有重跑它们。01 固定下载公开依赖，锁含115个精确依赖版本；wheelhouse manifest 为121个 wheel。02 从 wheelhouse-only 安装指定组合，03 `pip check`，04 经已安装包的普通 MIG04 Binance dry 入口，输出 `READ/live=false/status=DRY`，05 验证 certlib owner、版本、七个 site-packages 模块来源和哈希。原始 R1 总结果仍为 `HOLD_COMBO_CHECK`，因为当时 step06 的 harness 因本机导入探测失败；该旧结果未改写。

R9 只增量补跑同一 fresh consumer venv 的 step06，exit 0、未超时。`app`、Backtrader、base/event_clock、Binance provider、certlib 和 plugin 均从 consumer venv 的 site-packages 导入；解析 `bt_api_binance.plugin:register_plugin`。五个旧直接消费者 wheel（AFI、Backtrader、base、Binance、SDK）hash 与 R161 完全一致；新 certlib wheel 与 R197 完全一致。未重建或重装旧10个 wheel，也未重跑 steps 01–05。

R161 其他条款覆盖原样保留：10个既定 sdist/tar 和10个 wheel 构建成功，4个 wheelhouse-only venv 安装和4次 `pip check` 成功；schema v1/v2 解析；wheel-loaded ServiceHost、gateway、transport start→close；extras 的明确拒绝/边界；wheel-loaded SQLite registry 最小 create/use/close；普通策略 venv 无四包且无 ZMQ。AFI `app`、Backtrader、Binance provider 和 SDK 的 site-packages 来源由净 cwd 补充45b关闭。第161轮原件、独立评审和原始披露未改。

**冻结发行组合。** R161 的十个直接 wheel 和源码 HEAD 如下；R9 在原五个消费者 wheel 上增加 certlib wheel。hash 为 wheel 内容 SHA-256。

| Distribution | Version | Frozen source revision | Wheel SHA-256 |
|---|---:|---|---|
| `bt_api_py` | 0.15.5 | `466977392985ef1d7fa9b282a220096c8d19dff8` | `6ed0ccc39991ef715f33a8a816c41ee6244b4490b911c2204eba4486a04377b8` |
| `bt_api_base` | 0.15.8 | `b4aabd1e17b9c7252fa7b4d266932087d99b5517` | `840090fbc13e7b1fefdc686805ec8685a5e5a7dfd39ace3e7a5763922fd5dbb0` |
| `bt_api_transport_zmq` | 0.1.0 | `a397fdb460d2deb22e67ceb4963992ef63dbbb0e` | `2d7bd6421b573ea048c74b26931d57c79e5591f38a7a7ed67e28ec46bc3bf65d` |
| `bt_api_gateway` | 0.1.0 | `59b164ab7f89c6428ae6546103aad6058dab4c16` | `bad35ebfcdefe2b64c9b7694aedfd9648993a2785f60d37db4e3aa6d00d9ae12` |
| `bt_api_execution` | 0.2.0 | `dc8bd7a3682d00cfaef4f15261607043c6d24fca` | `bfc396e7be9b64c07b1b34ac62b07daac918231788974b1605dc0c5d32836e0a` |
| `bt_api_risk` | 0.1.0 | `131175a4070c930f1dd5186ea707d6bc9bbb4310` | `0adb56b3fc9b00e9a03d29b735a4d56ee542aa2418ae6661058844f741d602b3` |
| `bt_api_monitor` | 0.1.0 | `536a4f04747182749fabdac5e28624f80465bc53` | `6bf28cb42f3a6dd1ecf143ce5ac385b4e76540545d778d07826c15e59bda440d` |
| `bt_api_binance` | 2.0.1 | `b438a277c92a22851a16be0be1a8c8ee9f852b79` | `7cac597539bfa6a2a6fa6249e9983df1494d30c4f427898346f22940c9b89a23` |
| `backtrader` | 1.4.0 | `b002afa2da66ca6d14f1623280109be8e653a1b6` | `d825efaeb82245fe91dbfcce213e85f6bacf43a1ef27f5c5955463f20ab0749d` |
| `ai-for-investor-backend` | 0.1.0 | `34f59117ac9a2e73f00b6908cfeeb11fa53f5610` | `287ee6fcef1ff50f0d51600e951285741596cc3a1d365906dbb0d7afcb0fa234` |
| `bt-api-binance-certlib` | 0.1.0 | modules `be07a50ed3b4ddb5b419855827e7a965d5230b84`; metadata commit `223d5b68a55bb167e2d310f9565b511093b01742` | wheel `164ca44a402ba8153cf55890b9b954ce29becf57ae9bbbc8feefc283a007a2c3`; sdist `deb5520156d2838f7def48e507f6963edf8ff0117e8b13f481f3700d886f0b26` |

R197 记录的实际构建源码是 `be07a50`，当时 pyproject 是未跟踪文件。其固定字节 SHA `7a54ffeefddb0b53f16f7d74247669f369bcde1930addff3b8c3a257c2f97e5c` 与 `223d5b` 提交中的 blob 相同；发行模块的七个 `.py` 也逐个 pin。故来源依据是源码内容、metadata 内容和实际产物 hash，不声称 R197 从干净 `223d5b` checkout 构建。R161 的 Backtrader 消费者 wheel 仍是表中的 `1.4.0` 工件。

**本机探测的准确边界。** R9 记录了一次固定 `cmd.exe /d /c ver` 平台预热；NumPy 的 `lscpu` optional probe 因命令不存在而 `FileNotFoundError`，未创建进程；base 在 import 时读取一次 hostname 元数据，未保存 hostname；urllib3 对 `::1:0` 做一次真实 AF_INET6 loopback 能力 probe，随后关闭 socket。它们都是本机 OS/harness 事件，不是 provider/account 行为。R9 将 `network_absence_claim` 明确置为 false；本轮不声称网络不存在。未授权的其他 socket/process 活动仍受拒绝策略约束。

独立评审对 R197 包闭合和最终 R9 静态增量均给出 GO；最终 R9 QA 原始记录的 `formal_mig04_acceptance=false` 保持不变；正式接受由本份增量 Root 裁定建立。

**范围限制。** 只接受上表的固定 Binance 组合；当前或未来 dev checkout 中未进入这些 wheel 的新代码不自动继承。验证使用 Windows/Python 3.11.5；service/wire 是 synthetic provider、零客户端流量 smoke，SQLite 是最小使用，provider 只含 Binance。未验 CTP、SimNow、真实账户/provider、live credential discovery、完整网络隔离、DIST01/02 或 ARB05 双机子场景。

Root 已核对正式条款、独立评审结论、16 项外部证据引用及归档全部 45 个原始 payload 的大小/哈希，接受 MIG04 正式 PASS，计数 38/68。`raw-evidence.zip` 与候选 compact ZIP 字节完全相同，内部 pending 字段是归档创建时状态。R161、R197 的既有提交证据只由路径和哈希引用，没有重复打包；没有重复已接受的构建、安装或测试。