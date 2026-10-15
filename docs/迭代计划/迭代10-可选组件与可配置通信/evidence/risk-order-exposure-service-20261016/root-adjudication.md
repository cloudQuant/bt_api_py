## 第134轮：Risk 订单敞口完整观察链服务接线

本轮仅提交SDK Risk order entry、SQLite journal及exposure integration test三个源码/测试路径。实际BtApi归一化查询进入Risk证明链，修复EXPOSURE_APPLIED持久时间字段、post-ACK UNKNOWN精确事件形状和CANCELLED零leaves不应要求活动订单估值价格；保留严格claim、permit、attempt、source revision与writer fence校验。

Root r20真实spawn ServiceHost/CURVE/SDK节点1passed/exit0、10.18秒、一条既有pytest配置warning，stderr空；368项固定来源前后不变。覆盖首次FILLED、ACK与后续部分成交、重复查询不重复计量、信息不完整UNKNOWN保留敞口、完整CANCELLED释放leaves、释放后新订单、超限拒绝不新增provider写入。Host和strategy正常close/join/exit0，测试确认无fallback terminate/kill和清理失败。r19本地完整链及严格形状节点2passed/exit0是辅助证据，不替代该Host；通过节点未重跑。

来源是r8历史候选加本轮overlay：SDK历史213f1114、Execution raw 743a0b9f与固定Base/Risk/Gateway/Monitor/Transport；不是当前整个dev运行证明。365/368固定输入与捕获时checkout相同，另三个SDK Execution/account-books/group文件有已提交后继版本；源码清单/provenance完整保留。测试依赖的risk-only fixture函数已在HEAD，当前该文件的两项未运行typed-terminal测试不提交；components的格式和未使用export、registry格式、其他WIP均保留。独立静态GO与Root精确来源/结果检查支持此范围。

证据等级为本机真实服务进程、合成provider的Risk订单敞口接线；无真实账户/生产baseline声明。完整RISK02仍需按正式条款补所需恢复与失效分支，当前Host节点未覆盖敞口应用后重启、TTL/数据过期、FX或非单位乘数，不从包级证据推导全链通过。正式23/68不变。

证据见evidence/risk-order-exposure-service-20261016/root-adjudication.md。
