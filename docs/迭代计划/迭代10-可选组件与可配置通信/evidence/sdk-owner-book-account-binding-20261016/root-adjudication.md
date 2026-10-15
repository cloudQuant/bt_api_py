# 第118轮：策略账户簿显式账户绑定

OWNER_BOOK 的旧 coverage 投影裁剪了 canonical account_key，而新增 financial 子对象有自己的 ledger_cut.account_key。服务端原本已把两条读取绑定到同一可信 Execution 账户；没有观察到跨账户输出。本轮把 reconciliation.coverage.account_key 显式保留到 OWNER_BOOK coverage，让 Backtrader 等消费者能独立核对两条投影的账户一致性。Wire account_ref 与 canonical account_key 仍是不同标识，不能直接互比。

唯一生产变化是一行字段投影；新回归走真实 ExecutionOrderEntryService.handle 和持久 SQLite financial projection，验证 target account_key、legacy coverage 与 financial cut 三者一致，且查询前后数据库 total_changes 不变。Root 954fce 运行该新节点：1 passed、exit0、1.49秒，一条已知 pytest 配置 warning，stderr 空；369个来源pin及7个仓库HEAD前后不变。Execution 使用743a已提交源码的精确 Git blob 导出，排除未提交paper代码。独立源码审查GO，未重复第115轮已接受节点。

本轮在 SDK/dev 父91e3662837514a30b5cb0e1f25c79fe219af6124提交两个明确源码/测试路径及本记录，Risk/group/paper等其它变化保留。证据是本地真实handler/SQLite、合成账户事实，不包含新的ServiceHost/Cerebro或真实账户验收；正式22/68不变。下一步在固定已提交SDK上验证Backtrader账户绑定、成本完整性与同run重开。
