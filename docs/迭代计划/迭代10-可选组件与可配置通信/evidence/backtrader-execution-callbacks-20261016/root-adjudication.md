## 第122轮：Backtrader真实成交回调与重复投影

新增一个Cerebro/ServiceExecutionSession集成节点，通过真实ServiceHost、CURVE和SQLite派发一个稳定intent并接收完整成交；策略收到一次Completed和一次开仓trade回调。随后在回调中读取同revision的受认证服务投影并再次传入Broker更新入口，检查仓位、成交数量、平均价、手续费和回调次数不重复增长。回调与关闭后的观察使用公开无I/O缓存报告，避免假provider空持仓刷新污染本地成交观察。持久dispatch_attempts、reserve与settle均为一次；Session、runtime和Host正常关闭，无强制回收。

作者在最终冻结test pin 4cf0f713…46e70ea0上执行原r3：1passed、exit0、8.72秒，一条既有pytest配置warning，stderr空。独立静态review GO；Root核对原command/JUnit/exit/log及当前test字节后接受，不重跑通过节点。该直接runner没有全依赖源/HEAD前后快照，故不宣称其具备该证据。固定SDK为6e38导出；Execution为743a的换行归一化导出，不冒称Root原始Git-blob导出。原r1节点名错误和r2假持仓刷新失败保留。

Backtrader/dev仅新增测试提交470b425d75fae37f873cd85f00ff56b150753240，父1fc58a3d，Root提交后验证精确单文件、empty index和四项无关tracked变化保留。本SDK轮只归档证据和进度。该节点使用合成provider/admission，不含新策略进程恢复、完整回调崩溃边界或真实账户；BT01/BT02仍部分，正式22/68不变。

证据见[evidence/backtrader-execution-callbacks-20261016/root-adjudication.md](evidence/backtrader-execution-callbacks-20261016/root-adjudication.md)。
