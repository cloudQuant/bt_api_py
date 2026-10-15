# 第108轮：订单组终态 authority 修复

证据级别：合成 provider、真实 ExecutionStore/Registry/持久 authority；不是 ServiceHost/CURVE 两腿集成或正式 ARB PASS。

原问题是在 provider 结果已经保存为 FILLED 后，结果回调仍要求 DISPATCHING。修复保留派发前严格校验，另验证 FILLED/有 provider ID 的 REJECTED 的精确 intent、binding、hold、当前 writer lease/generation，以及同一 outbox incarnation 的四事件完整顺序。回调后记录变动或不确定状态保留 hold。

Root r1 四例在 admit 前失败：fixture target 未包含 portfolio，而 Execution 9943 按完整 scope 查找。修复新例及三个受影响旧例的 target 后，Root r2 七例通过，pytest exit 0，3.04秒，一条已有 pytest 配置 warning。402个公共输入及四仓HEAD前后不变。Execution采用9943的23文件固定Git源码导出；没有使用正在开发的资金账store。

execution_recovery_review 对最终 authority 5d2de5ab… 与 test 54c8f850… 静态 GO；未运行测试。Root未为归档重复已接受QA。r1失败原始输出保留。完整两腿普通服务链、no-fill证明、跨Host与UNKNOWN恢复仍待后续验收，正式总数保持22/68。

提交范围仅authority源/测试、两份进度记录及本目录证据；子包和其它SDK在研修改不在本轮。基线SDK dev 0833ef579915ad1a501d6561fa185e799db0f4c5；实际提交号由本文件所在Git提交给出。
