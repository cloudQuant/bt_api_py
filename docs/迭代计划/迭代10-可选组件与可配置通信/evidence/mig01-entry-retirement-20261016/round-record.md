# MIG01 SDK 入口退役证据包

本包准备将 SDK 的四个已验收入口文件与 Root 原始 QA 一并归档到父仓 `D:/bt_api_py`，不更新 Backtrader gitlink。父仓记录时的分支是 `dev`、HEAD 为 `937350b138ea952a14e1ab08539dc295efb1d0a4`；SDK 子包 `bt_api_base` 为 `b4aabd1e17b9c7252fa7b4d266932087d99b5517`。这四个文件的当前 SHA-256 均与 SDK Root R1 的 source-before/source-after pin 相同。Root R1 当时绑定的父仓 HEAD 是 `d400f0f98724554c0aed3f41ebfaffcff4d9e774`；不能据此宣称后来全部 49 个来源仍在当前 HEAD 上重新验证。

本包中的 SDK 文件是 `bt_api_py/gateway/__init__.py`、`bt_api_py/gateway/client.py`、`tests/test_gateway_client_and_certification.py` 和 `tests/test_mig01_gateway_entrypoints_retired.py`。固定来源 SDK Root QA 在两个指定节点上通过 2/2，pytest exit 0；49 个来源和当次父仓/base HEAD 前后不变，Root 报告用时 1.41 秒。新子进程节点验证包与 client 模块来自该仓，并验证导入/构造入口不会加载 forwarding runtime。具体字节 pin 见同目录 `manifest.json`。

BT 部分保留了 Root R1/R2/R3 的原始 11 文件输出。R1 因 `BACKTRADER_LIGHT_IMPORT=1` 令 `tests.test_utils.factories` 无法读取 `bt.feeds.BacktraderCSVData`，两处 collection error，exit 4；这属于运行环境/导入模式问题。R2 收集 90 个 case，88 passed、2 failed、2 warnings，exit 1。两个失败都是 `test_ctp_forwarding_store_direct_writes_fail_closed_before_api` 的参数实例：`object.__new__` 夹具未初始化 `_service_read_only`，在到达 CTP 拒绝守卫前触发 `AttributeError`。R3 只重跑这两个参数实例，改用正常构造的 direct Store 和注入计数 fake API，再将 backend 变更为 `forwarding`；两例通过，exit 0，仍有一条既有 Quandl deprecation warning。R2 的 88 个通过 case 没有重跑。R2 与 R3 共同覆盖 90 个唯一 case，不能描述为单次 90-case 全绿运行。

生产字节来源明确为 `D:/source_code/backtrader/backtrader/stores/btapistore.py`，R2、R3 的 Root source maps 和 BT 提交收据均 pin 为 SHA-256 `22ac42be89f8ea7f72a2b26de6ec35f7c427617eb264faf8174f0fb5543987d0`。Backtrader 已独立提交 `38f764dbf2d51adcba4c6a0901734639096365a3`（parent `edfb4ddb64ee42e0d1f90d7a111def63447ccd67`）；六文件 postcommit receipt 为 `D:/bt_api_py/.codex-tmp/iteration10-mig01-bt-entry-retirement-postcommit-r1.json`，SHA-256 `c4a79a1a499699f87100b8eee4d92ea8e726456bf807c6c967f438f53464e1d9`。收据核对提交范围、空 index 和生产 pin。

BT R2 的旧 `tests/unit/stores/test_btapistore.py` 源文件没有保存副本；原始 `source-before.json` 中保留的元数据 pin 是 `960a3960553ab93ba983e943c0ecf93f27c033edcfcbbadbde00a8627b8264f2`。本次不重构或重建该旧源码。R3 使用的最终文件 pin 是 `5d59d4cf2e5b82128aa021fcf88f0d3908296ee1e4463029740bd730bbe3d341`；静态边界核对中目标函数之外的字节摘要为 `114f244282b19b7a9c664a2e6f3f4fc24b868bc86c1fbdbec109ed5460b674c4`。

`root-qa-raw.zip` 原样收录 SDK Root R1 与 BT Root R1/R2/R3 四个输出目录，每轮各 11 个文件，共 44 个成员；`manifest.json` 记录这 7 个父仓交付路径、成员 pin、四个 SDK 文件 pin、R2/R3 结果与 BT 提交收据。ZIP SHA-256 为 `4fb5fe01125686b2ab99c5b6cb1ec2c7265b13bbc5c8def4a30b347995404fe9`，manifest SHA-256 为 `86809f123cad2525a7a6db9bf1f334e3668cdfe192c0cd91365e031399176b4a`。

证据仅为固定来源的 source/fake 测试；不包含 Host、provider 或账户验收。MIG01 仍未完成或正式 PASS：旧目录、base 接口树和历史 fixtures 保留，SDK 其他消费者、构建/wheel 依赖闭包、隔离安装与正常调用扫描仍待验收。本轮编号待 Root 在 MIG04 R211 后分配；未修改正式进度文件。
