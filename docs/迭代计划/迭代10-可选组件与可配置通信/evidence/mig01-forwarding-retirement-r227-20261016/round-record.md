# 第227轮：MIG01 forwarding writers/provider loader 源码退役证据

日期：2026-10-16  
仓库：`D:\bt_api_py` / `dev`  
证据目录：`evidence/mig01-forwarding-retirement-r227-20261016/`  
正式MIG01：仍未PASS；全局正式状态仍39/68。

## 范围和提交状态

本轮固定来源候选共67个精确overlay：SDK父仓35个文件、`bt_api_base` 9个文件、14个provider子仓合计23个文件。67个当前文件的size与SHA-256均匹配候选源manifest。本轮证据先归档；Root按既定顺序提交Base和provider子仓，再更新SDK父仓gitlink与证据。归档时Base/provider子仓提交SHA仍为pending，SDK父仓记录的验收前HEAD为`77b86e2139708455722a3f11955cce18b14647fe`；本记录不把未发生的提交写成已提交。

## Root执行记录

三轮原始输出和当次实际spec/runner均在 `raw-evidence.zip` 中，每轮11个成员；未改写R3/R4的失败记录：

- R3：74 passed、23 failed、1 skipped、21 warnings，exit 1，15.68秒；4603个来源pin及HEAD前后不变。
- R4：23 passed、1 failed、2 warnings，exit 1，5.39秒；4606个来源pin及HEAD前后不变。剩余失败是private事件topic夹具不符：生产schema按`strategy-1`路由到`strategy.strategy-1.account`。
- R5：仅重跑该失败节点，1 passed、无warning，exit 0，3.92秒；4607个来源pin及16个HEAD前后不变。精确topic、pending event与拒绝前零poll/cache断言均保留。

97个R3/R4既有通过测试函数与最终R5导出逐个作完整AST比对，97/97相同；无缺失/改写。配合R5最后节点通过，跨轮唯一覆盖98个通过、1个skip。该覆盖由顺序修复轮合并而来，不声称一次98项全绿。归档也附有候选source manifest、67 overlay pin清单及AST比对脚本/结果；Root R5实际执行spec SHA-256为`51aa555285c83c21fb9cfb5f63a23f75fbb5deff059d22b56048823e5069407e`，runner SHA-256为`8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`。

## 未覆盖与证据边界

本轮是SDK/Base/provider正常源码和本机合成测试的退役验证，不运行Host、不连接账户/真实provider，也不构成MIG01正式PASS。仍待独立收口runtime_plugins第二writer、GatewayRouter/ZMQ transport可直接调用入口、旧目录/SDK发行闭包及隔离wheel验证。历史fixtures保留。

归档SHA-256：`873c360c2479a8dd6327be8211590cb809cab231a88ba6143cc24f89107ce92c`（1876820字节，38项）。本记录不引用自身或manifest的递归hash；三文件包的其余pin见`manifest.json`。
