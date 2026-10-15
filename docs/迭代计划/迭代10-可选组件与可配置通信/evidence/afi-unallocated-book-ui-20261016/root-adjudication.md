# 第114轮：AFI未分配账与增量残差分离展示

AFI组合视图增加live账户的UNALLOCATED账展示，把当前持仓/累计费用、provider变化与相对baseline的增量position/fee residual分开。账户总额沿用原投影，不由策略归因反算，不新增策略cash/value，不把overlap相加。API类型兼容旧响应：缺account_book_sources时隐藏新增区，缺unallocated_book时显示不可用。保留coverage incomplete、非可入账及overlap非加总提示，增加中英文文案。

Root以固定五路径及446个前端源码/配置pin运行两个新Vue组件测试，Vitest 2 passed / 35 skipped、exit0、1.47秒，stderr空且AFI HEAD和全部pin不变。35项未运行，不声明整文件37项通过。作者另报告typecheck、目标ESLint和diff-check通过，独立静态审查GO。证据是合成API的UI组件验收，不是浏览器实际后端/账户或完整AFI02/03验收；第110轮真实HTTP/四Host API证据独立保留，未重跑。

AFI/dev精确五路径提交db32c3ce46e6b8e03bdaef42f3e18ce20c64c4ac，父2d081f43；无关README/Windows脚本修改保留且index空。父SDK/dev本轮只记录证据及两份进度文档，父4b059936；实际SDK提交由Git记录给出。正式22/68不变。后续仍需paper、多币种/FX、全异常矩阵与产品迁移的独立完整验收。
