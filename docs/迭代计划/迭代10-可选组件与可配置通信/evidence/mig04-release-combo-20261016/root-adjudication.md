# 第161轮根裁定：MIG04 指定发行组合安装与正常入口兼容性（正式通过）

- 裁定：GO（正式通过）。条款（正式迭代计划.md MIG04）：
  "base/SDK/transport/四包/provider/BT/certlib/AFI 指定发行组合可安装；代码版本、schema/wire、extras 与状态库格式兼容或明确拒绝 | wheel/sdist 干净安装及正常入口跨仓集成，不用 submodule 源码导入代替发行验收；普通路径无四包/无 ZMQ"。
- 独立评审：GO（`.codex-tmp/mig04-r3-review-status.md`，归档于本目录 independent-review.md）。
- 条款逐片段映射：
  - 指定发行组合可安装：10/10 tar（哈希记录 + commit-time 全量重归档逐字节一致）+ 10/10 wheel exit 0 + 4 venv 安装 exit 0 + pip check 4/4。**certlib 例外（必须明示）**：pin 上无打包配置、无发行物（02c 原始 pip 拒绝照实归档），对其接受仅为 archive 抽取上的 import 级验证，本裁定绝不宣称 certlib 可安装——依据条款 “…或明确拒绝” 的处置类。
  - 代码版本兼容：pins 冻结且经评审验证为当前 dev 祖先（SDK 46697739、risk 131175a4、AFI 34f59117；仅 pin 纪律，live heads 会后移 N4）；freeze 记录解析版本。
  - schema/wire 兼容：config_schema_version 1/2 均解析，WireIdentity/WireScope/CURVE 经 wheel 装载的 ServiceHost + gateway + transport 真实 start→close（smoke 范围：合成 provider、零客户端流量，如实声明）。
  - extras 兼容或明确拒绝：ml 为 live 明确拒绝（ImportError 点名 'ml'）；analytics 仅在模拟 numpy 缺席下拒绝（N1）；base 路径无 numpy/sklearn/scipy/joblib。
  - 状态库格式兼容：经 wheel 的 ServiceHost 创建/使用 registry SQLite（注册表/会话/写声明表）并正常关闭（最小使用 + policy_catalog API/import 级）。
  - wheel/sdist 干净安装 + 正常入口跨仓集成：全新 venv 仅 wheelhouse；CLI/插件 EP/backtrader/AFI 入口。AFI 原 45 记录为 cwd 遮蔽（D1）——commit-time 补充 45b 于净 cwd 重跑，app/backtrader/bt_api_binance/bt_api_py 四模块全部 site-packages 来源、exit 0。
  - 不用 submodule 源码导入代替发行验收：10/20/40 原始 JSON 模块来源全为 site-packages；certlib 例外如上。
  - 普通路径无四包/无 ZMQ：策略 venv 五模块 ModuleNotFoundError。
- 评审 §8 登记项处置：D1/D2 由 commit-time 补充关闭（45b 净 cwd 复核；supplement/archive-repro.txt 对 10 pins 全量重归档与 01b 逐字节一致，git 2.42.0）；D3（SDK 工作区 2026-10-15 既有未跟踪杂物）与 D4（试点后并发提交致 porcelain 漂移 13/0/0/0/0/3/0/0/6/23）均非试点所致；stray-tar 事故已披露、残留扫描为零。
- 接受基线：independent-review.md 即评审文件字节（sha256 `b3652fc2a43e0cf2925fb893c500fe6e0429ffd09eb300b228eba7225fa03d7b`）。
- 证据等级：AUTHOR_FIXED_SOURCE_PILOT + INDEPENDENT_STATIC_REVIEW + COMMIT_TIME_SUPPLEMENTS；本机、合成 provider、loopback，无真实账户。
- 披露（不阻塞）：N1 analytics extra 非 pip 可达；N2 certlib 无打包配置（import 级、例外如上）；N3 freeze 即 lockfile；N4 heads 移动依赖 pin 纪律；N5 wheel 字节不可复现；schema/wire 为 smoke 范围；provider 仅 binance wheel（ctp 不在组合内）；01-archive.log 为 0 字节空文件，由 commit-time 全量重归档替代佐证。
