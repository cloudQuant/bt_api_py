# R242：MIG01 provider direct projection 与旧 adapter 源码闭包

日期：2026-10-16  
SDK仓库：`D:\bt_api_py` / `dev`，取证时HEAD `9a1495ad4be0670f7f2efe7e81c3ae9650877023`  
证据目录：`evidence/mig01-provider-projections-r242-20261016/`  
正式MIG01：仍未PASS。本轮是五个provider包的源码迁移闭包与指定direct projection源测，不包含wheel、隔离安装、ServiceHost或真实账户验收。

## 源码范围

本轮候选只含五个provider子仓的现有`dev`工作区；Base没有源码变化。五包合计53个精确路径，其中19个是从当前HEAD删除的旧gateway adapter模块或旧adapter测试。每个候选文件的新内容SHA-256、删除文件原Git blob OID/字节SHA-256/大小、包HEAD以及父仓当前gitlink都记录在manifest中。包范围如下：

- `bt_api_ctp`：11路径，修改5、新增0、删除6；基线`d747213b67037c41ea55944980ff54e908808db6`。
- `bt_api_ib_web`：13路径，修改9、新增1、删除3；基线`bf898064425cc4467cefaf7a7d9a4a7e1d54631f`。
- `bt_api_okx`：16路径，修改13、新增0、删除3；基线`6ba2cead8704b708cd902685602272a581b444d4`。
- `bt_api_binance`：6路径，修改4、新增0、删除2；基线`43657617525267cc6103d2cfb7ce4c85663c7176`。
- `bt_api_mt5`：7路径，修改2、新增0、删除5；基线`d9eb21926b5256e93f1c50166b7e10ece5f7cdd4`。

需要Root单独关注的两个修复已按实际生产合同核过：IB `IbPositionData.init_data()` 读取Client Portal `acctId`别名，`get_position_projection()` 将标准化后的账户值写入`account`；R2测试确实因该字段缺失而失败，当前候选和R4执行来源一致。OKX swap fee fixture改用费率小数`0.0002`/`0.0005`，生产projection按maker/taker负号规范输出`-0.0002`/`-0.0005`；原fixture把`0.02`/`0.05`与较小期望混用，R4只改完fixture后通过，production fee逻辑没有随delta改变。

## 验收记录

Root接受按“完整作者R2结果 + 只针对失败节点的R4修复结果 + faithful production diff review”作为本轮候选。R2在原来固定源码上运行16个selector、JUnit报告23个参数化case：21 passed、2 failed、pytest exit 1，并有一个现存`PytestUnknownMarkWarning`。两项失败正是上文IB账户别名输出和OKX费率fixture单位。R4只重跑这两个selector，2 passed、0 failed、exit 0，耗时2.23秒。R2和R4的stdout/stderr/JUnit/result/command/spec/runner/source-before/source-after等每轮11个原始文件均逐字节放入`raw-evidence.zip`。这不是一次整套全绿执行；R2与R4都没有被重跑。

每次运行均记录309个source pin且执行期间全部稳定，provider/Base/SDK各仓HEAD在各自run内稳定。对当前源码重新核对R4的309个固定source时，308项逐字节相同；唯一差异是R4之后恢复的IB README非运行文档内容（固定pin与当前pin见manifest）。当前生产代码、测试及其余固定source都匹配R4。R2的三项当前差异分别为这份README以及后来在R4通过的IB position和OKX fixture修正。

证据级别：`MIG01_DIRECT_PROVIDER_PROJECTIONS_SOURCE_NO_HOST`。Root依指示没有另跑重复的16-selector QA。没有启动Host、ZMQ、provider或账户调用；没有构建wheel、安装分发包或验证normal import/wheel contents。独立review的具体IB/OKX关注点与结论由Root指令和本轮source diff记录；本目录未伪造独立review原件。该轮证据不代表formal MIG01 PASS。

## 每包提交边界与后续

Root应先在各包`dev`上按manifest中的精确文件清单逐包提交，子包完成后再更新SDK父仓gitlink；不要把父仓其他lane的工作区改动带入这轮。候选包工作区当前index均为空，本轮作者未暂存或提交。现有接受范围不覆盖Root其他SDK/base/group/service WIP。

本轮完成后再承接已与AFI owner协调的OKX ticker纵向迁移：复用`OkxTickerData.get_market_projection()`和既有`GatewayMarketHub`的`WireEnvelope(schema_id="sdk.market.event", operation="market.event", payload={"key": key.to_dict(), "data": projection})`；provider publisher需要在已授权的service owner线程消费OKX WSS ticker队列并发布。此纵向slice尚未实现，也不在本轮包提交清单。MIG01剩余的normal/ZMQ隔离安装、发行内容/extras矩阵、provider factory/ServiceHost integration及其他消费者迁移仍要单独验收。

父仓candidate evidence scope恰为本目录的`raw-evidence.zip`、`manifest.json`、`round-record.md`三个文件；没有编辑全局progress tracker。