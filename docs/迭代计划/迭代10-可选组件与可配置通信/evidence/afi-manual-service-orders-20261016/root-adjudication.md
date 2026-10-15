## 第124轮：AFI受认证手工单进入统一Execution owner

AFI新增服务订单HTTP入口、服务访问权限、持久operation-key模型/迁移及操作键到稳定intent的映射。键绑定subject、principal、actor、delegate、服务器scope绑定和请求指纹；相同键/内容复用intent，不同内容冲突。账户、策略、操作者、委托和scope来自服务器可信目录，客户端额外actor/审批字段拒绝。调用进入既有service-read owner线程与SDK runtime，再交给正常Execution owner，不新增旁路派发。已批准目标缺少完整可信审批合同仍明确拒绝，本轮没有授予该能力。

真实链先发现AFI错误要求双层业务reply：SDK已去掉wire层。新无Host回归使用真实SDK start/call/Registry及Execution._reply复现旧503，修复后focused unit8/8通过（两条已知pytest配置warning）。r7真实链通过首轮HTTP检查，却在lifespan内部提前断言deployment.closed；仅把四个断言移到该context正常退出后，未修改生产清理或放宽断言。r8唯一新Host节点1passed/exit0/11.01秒，验证两次lifespan、A同键顺序重放、B独立intent、409内容冲突、422伪造actor、403未知scope，服务侧actor/delegate和owner-thread一致，provider仅两次写入，worker/deployment/Host正常关闭。25个源pin及AFI/SDK/Execution/Base HEAD前后相同。三条warning为两个既有pytest timeout配置项及Starlette 422弃用；原r6b/r7失败证据保留。

独立静态review GO；review消息曾误摘录旧deployment pin，已更正为当前37020字节/60a17fb8…9e7c43b，Root与原manifest核对一致。Root核对原r8 command/JUnit/exit/pre/post和当前15个AFI源，不重跑已通过节点。AFI/dev提交1cf08d2a4673ea557bf8d11e7a1642e541ea7199，父dd1e14a；精确15paths、empty index，README/Windows脚本等无关改动保留。SDK本轮只归档记录，没有AFI gitlink。

本节点在单一Python进程内顺序重建两次lifespan，应用操作键DB为内存SQLite；Execution owner使用合成provider/真实本机Host与SQLite。尚未证明双AFI worker并发同键、持久应用DB/进程重启、已批准目标/审批撤销或真实账户，不能宣布完整AFI01/AFI07。下一轮补持久数据库与真实两进程竞争/恢复，再推进审批服务合同。正式22/68不变。

证据见[evidence/afi-manual-service-orders-20261016/root-adjudication.md](evidence/afi-manual-service-orders-20261016/root-adjudication.md)。
