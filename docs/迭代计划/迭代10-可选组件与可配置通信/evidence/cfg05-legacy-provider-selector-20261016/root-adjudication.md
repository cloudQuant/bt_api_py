## 第133轮：正常入口拒绝旧 Gateway selector

Backtrader/dev db96d8566188bc602a77ae37539dc1269fe7e8ea（父a50aea8c）提交两个路径：CLI与legacy迁移测试。BT_STORE_PROVIDER经strip/lower后为gateway或以_gateway结尾时，在任何运行派发前返回MIGRATION_REVIEW_REQUIRED；direct或未设置仍可进入v4本地回测。判断与真实Store的provider选择语义相符。

作者r1在pytest-asyncio收集阶段失败（exit2、业务执行零项）；r2为4passed/1failed（exit1），失败仅为测试把输入Gateway词误认作固定提示中的原始值泄露；修正测试后r3只重跑三种拒绝值，3passed/exit0、一条既有配置warning。两项direct/unset正例沿用r2成功结果，生产CLI pin未变。Root核对原始JUnit/log及最终pins，不表述为一次5/5。独立静态审查GO，Backtrader提交精确两个路径、index空、无关tracked修改保留。

该正例仍使用合成runner，正常code-owned v4入口到真实Cerebro运行的新增节点待Root执行。CFG05要求保持“仍受支持”的v4/v5可用；当前v4受支持，v5是未来计划，不能把新增v5实现当作本条额外缺口。未接受真实provider/账户，正式23/68不变。

证据见evidence/cfg05-legacy-provider-selector-20261016/root-adjudication.md。
