## 第131轮：Execution/SDK 持久恢复快照与增量读取

Execution/dev f04851d91a7b336636b02ed9b6dd5cba1dc71037（父5e6ba3e）提交store及聚焦测试两路径；SDK本轮提交账户簿适配器、公开client恢复方法与聚焦测试三路径，并更新该gitlink。SQLite同一事务读取原子cut，检查全局journal连续性，按受信目录和认证scope选择订单/事件；目录前后变化、generation不符、未知事件或超限均拒绝，没有静默截断或provider读取。默认账户簿请求保持兼容。

恢复合同为持久策略scope，不按WireScope.run_id隔离；快照为DURABLE_CUT_NOT_PROVIDER_LIVE。快照最多512订单/20000历史事件、wire结果48KiB，事件页最多128条；超限失败，不宣称无界全账户恢复。客户端使用正常query绑定并解开一层业务result；签名手续费允许负maker rebate，原始tags不投影。

作者记录包级4passed/0warning、SDK5passed/1条既有pytest配置warning，均exit0；独立静态review GO。原始命令与结果来自保留的作者工具记录，没有保存raw log/JUnit或完整依赖来源快照。Root核对五路径pins、SDK diff/测试和包提交，不重跑已通过QA；证据等级为作者工作区QA加静态源码审查，不是Host/Cerebro恢复或正式BT02验收。旧v21兼容测试从当前DDL修改版本及旧payload再重开，只证明该迁移路径，不是完整历史DDL fixture。

正式23/68不变。下一步将Backtrader持久manifest/投影/callback恢复接入固定提交的正常服务链，验证独立策略进程恢复及回调中断边界。证据见evidence/execution-sdk-recovery-views-20261016/root-adjudication.md。
