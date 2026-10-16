# 第202轮：ARB02 正式验收

ARB02 → formal PASS，累计34/68。正式计划第768行允许真实持久化服务配合合成provider；不额外要求真实账户或同一节点重跑所有条款。

已接受R144/R152/R190证明组预算竞争/拒绝、完整预留后才首腿、未派发预留到期释放、已派发UNKNOWN不按TTL释放、已获全部预留而首腿前崩溃恢复不超卖/不丢占用。本轮补唯一账户级拒绝缺口：真实三个ServiceHost/CURVE和SQLite，组预算满足且A已commit；B实际Risk mapper按USD100请求与USD50额度返回typed拒绝。两账户provider写/订单查询/成交查询均为零；A仅在同scope/group的ACTIVE-unclaimed证据成立后释放，B无预留，组预算释放；协调者重启查询保持拒绝，再次新group实际复用预算并重复验证拒绝，无UNKNOWN/恢复记录。

Root r3单新节点1 passed/10.84s/exit0；250 source pins与SDK/base/gateway/transport四HEAD不变，stderr空。SDK测试基线a88edd28；Execution/Risk源码在输入闭包精确绑定，非额外HEAD门。独立review resume_pass_audit_reg_risk_cfg对最终17e637a7…测试GO_STATIC，完整facts字典和持久event均与生产合同吻合。r1错等不存在的Risk reserve事件、r2使用小写枚举预期导致失败，原件都保留；r3只修测试预期，生产源未改，历史已接受套件未重复。

仅新测试、逐字节原始三轮QA归档/manifest/本记录及状态文档提交至现有SDK/dev，父a88edd28，提交SHA见Git历史。不扩大ARB04–ARB06或DIM09结论。
