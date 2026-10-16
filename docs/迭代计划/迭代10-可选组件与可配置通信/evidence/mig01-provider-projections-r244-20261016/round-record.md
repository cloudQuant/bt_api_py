# R244：MIG01 direct provider projection源码范围重定界

日期：2026-10-16  
SDK仓库：`D:\bt_api_py` / `dev`，本轮取证HEAD `9a1495ad4be0670f7f2efe7e81c3ae9650877023`  
证据目录：`evidence/mig01-provider-projections-r244-20261016/`  
formal MIG01：未PASS。

R242原始包及其`raw-evidence.zip`、manifest、round-record保持原字节，作为旧gateway删除scope被拒收的HOLD历史。Root依据正式计划P7R第715、718–720、724行指出，AFI P9B/P9C/P9D消费者迁移先于物理删除；Base lazy map仍引用五个adapter，AFI仍导入当前会拒绝启动的legacy GatewayRuntime。故本轮从各provider候选中只撤回R242精确列出的19个删除路径，并逐个验证恢复内容与原HEAD blob身份相同；没有回滚本轮其它包改动，也没有更改R242历史包。

## 当前精确源码范围

R244只涵盖五个provider包中保留的direct projection/符号信息/正常feed合同、相关测试和文档：合计34路径，修改33、新增1、删除0。按包的范围：

- `bt_api_ctp`：5路径，基线`d747213b67037c41ea55944980ff54e908808db6`。
- `bt_api_ib_web`：10路径，基线`bf898064425cc4467cefaf7a7d9a4a7e1d54631f`。
- `bt_api_okx`：13路径，基线`6ba2cead8704b708cd902685602272a581b444d4`。
- `bt_api_binance`：4路径，基线`43657617525267cc6103d2cfb7ce4c85663c7176`。
- `bt_api_mt5`：2路径，基线`d9eb21926b5256e93f1c50166b7e10ece5f7cdd4`。

manifest列出每个候选文件当前SHA/大小和基于包HEAD的旧blob，记录每个包的`dev` HEAD、父SDK gitlink及单包提交建议。已拒收的19条物理删除在`r242_held_deletions_restored_from_head`中逐条记录原OID、SHA和字节数；这些文件现在精确还原到HEAD，R244 commit scope不包含它们。Base未改。本轮作者没有暂存或提交。

## 保留的源测结果

本轮不重复运行QA。Root已接受R2整套作者结果（16个selector、23个参数化case：21 passed、2 failed、一个既存`PytestUnknownMarkWarning`、pytest exit 1）以及R4只重跑两失败selector的修复结果（2 passed、exit 0、2.23秒），并报告IB account alias/OKX fee fixture direct scope通过faithful production-diff review。原始两轮各11个输出文件原样保存在R242 ZIP；R244复用该ZIP的相同字节与成员pin，不伪称R244新跑了一次测试或出现一次全套23项全绿。

生产/测试固定来源保持：R4的309个pins中当前300项完全相同，另外9项均是R4之后调整的文档文件（见manifest逐个哈希）；非文档源全部与R4执行时字节一致。IB差分验证Client Portal `acctId`在`IbPositionData`中归一化并通过position projection输出为`account`。OKX swap费率测试fixture以`0.0002`/`0.0005`小数费率为输入，并验证输出的`-0.0002`/`-0.0005`；这两个R4修复节点都在原结果中通过。其他文字变化只准确说明旧包仍在源码树但已退役/不受支持，没有改变运行行为。

证据级别：`MIG01_DIRECT_PROVIDER_PROJECTIONS_SOURCE_NO_HOST`。没有Host、ZMQ、live provider、账户、wheel build或隔离安装。本证据不接受物理adapter删除、正式P7R闭包或MIG01。

下一纵向slice应在本轮五包按Root顺序提交后与AFI10协作，接通OKX ticker → 现有`GatewayMarketHub`/`ServiceMarketClient`事件路径。该market feed migration尚未在R244实现；adapter物理删除继续等待AFI消费者和Base lazy map迁移。R242保留为原始HOLD候选，不修改Root全局progress。

父仓证据候选恰为本目录三文件：`raw-evidence.zip`、`manifest.json`、`round-record.md`。
