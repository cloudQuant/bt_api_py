# 迭代 52 A1：策略参数控制字符校验

日期：2026-10-31。仓库：`bt_api_runtime`，既有 `dev`；父提交 `fccc32de262b6461dd41be39d359b9277c6320b8`，本轮提交 `da5961dbe4473403caabbd0299c327174d006803`。

V2 策略参数的字符串和嵌套映射键拒绝 Unicode `Cc` 控制字符，包括原来遗漏的 C1 区间；普通中文及其他正常 Unicode 字符不因这一规则被拒绝。独立静态审阅确认代码、测试、版本及精确依赖范围。合同包版本调整为 0.1.5，runtime 为 0.1.11，后者精确依赖合同包 0.1.5。

Python 3.11.5 源码合同测试 57 项通过。以本地源码构建两个 wheel 并安装到独立目标目录，再结合本地 SDK 和 Backtrader 源码运行合同与受影响 runtime 测试：190 passed，2 条警告（pytest 未知配置项和既有 Quandl 弃用）。包内相关模块与源码、目标安装字节一致，runtime metadata 精确依赖 0.1.5。合同 wheel SHA-256 `9e8f57f8f6f596dbcf2f5f43cd15931ab4802b34969c1f3200b34eb739fa7f83`；runtime wheel SHA-256 `e518720351e3a3e285cd9a7272d4ffcbbcb1242cd2b14d6f478a9368a3985f49`。

Ruff 检查两份 Python 文件仍失败，共 44 项；对照父提交逐文件逐规则计数相同，本轮未新增该检查的诊断。`git diff --check` 通过。目标安装依赖本地环境，不是隔离安装或发行验收；未操作真实 provider/账户，也未运行延期模式或增加正式 case PASS。
