# I52-A：版本化配置字段源码小轮

日期：2026-10-30。子仓库 `bt_api_runtime` `dev` 提交 `0eff9b6ef1af12f7a25b740c63c3324bd39eaf30`，父提交 `a8e224b9fb8b268b3b4b0eadba75f1ffe386b6d3`；SDK 父仓 `bt_api_py` `dev` 的本轮父提交为 `36b31f4548ce1abbfb2d3252237455bb4dc23dc8`。父仓本轮仅更新 runtime gitlink 和本记录；其提交哈希由包含此文件的 Git 历史确定。

同一 `parse_config` owner 分派 schema v1/v2；v2 纯数据合同加入 product、data_source、clock 引用、插件开关、受限嵌套策略参数和显式能力申请。参数快照不可变，可显式深拷贝为普通 Python 数据；策略描述符仍需校验实际字段。paper/live 的环境和写能力错配拒绝；replay/shadow 识别但延期。v2 resolver 明确返回 `schema_v2_resolution_deferred`，因此当前 v2 不能运行，不建立真实资源身份或授权；v1 的已接受只读行为保持不变。

验证与限制：

- Python 3.8.20 与 3.11 源码合同测试各 47 passed；补充嵌套深拷贝断言后分别通过该定向用例。独立源码审阅通过纯解析范围。
- 合同 wheel `bt-api-run-contract 0.1.2` SHA-256 `4cda8c352accda36321207c0c7bc642d9d80c105e1cedf4da9a4f6d5a5f4b27c`；分别安装到 Python 3.8/3.11 目标目录，各运行 47 项通过。运行环境使用已有解释器和依赖，不是隔离 venv 或发行验收。
- 只读 runtime `0.1.1` 固定依赖合同 `0.1.2`；wheel SHA-256 `591cf599b4df6a93d5992e02144dc3ae3a614eeaccbd08d4c02ff56bd9a86854`。两个 wheel 安装到同一目标目录后，使用本地 Backtrader 和 SDK base 源码运行现有 v1 只读装配测试：25 passed、1 条 Quandl 弃用警告。合同与 runtime 的 Python payload 均与源码及安装字节一致；未做完整依赖解析或真实部署。
- 实际 provider/account/credential/endpoint identity、产品与数据源/时钟交叉绑定、v2 resolver、插件及许可、真实账户和订单行为均未验收。首次 wheel 元数据校验脚本误把依赖行格式写成带空格，导致断言失败；已修正脚本并确认实际元数据固定 `bt-api-run-contract==0.1.2`，未修改候选代码。

本轮未运行 replay、shadow、旧本地撮合或账户交易；保留既有 gateway 子仓和证据记录修改，构建缓存未纳入提交。
