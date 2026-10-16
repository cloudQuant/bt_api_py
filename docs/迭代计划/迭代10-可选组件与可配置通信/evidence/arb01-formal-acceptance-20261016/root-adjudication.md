# ARB01 正式验收裁定（2026-10-16，第198轮）

裁定：ARB01 正式 PASS，总数由 31/68 增至 32/68。依据正式计划第767行的实际条款，采用已有接受证据的组合，不重复 QA。

- 第158轮三个真实进程/CURVE节点覆盖一策略双账户、另一策略共享、跨交易所及同交易所同品种、账户越权与歧义路由拒绝。固定测试 pin 0138ca016e7caf77260fcc155e3d266b0799a160a356eec639fbacaab785fc61 未变。
- 第152/190轮持久组查询隔离与 CFG06 r2 的 GROUP_POLICY_DENIED 无副作用节点覆盖组越权。第190轮确认当前第152轮节点，CFG06 r2 确认新 group-policy 接线。
- 已正式通过的 GW02 真实 100 策略/10 账户/单行情键结果中，market-provider start/stop 各一次；不能把第158轮的每账户 provider session 数误当行情订阅数。GW02 observation SHA256 b3eb34e71eae349670117e4089a00955a9b19ece31f93a9bb24a901aa9190e17。
- Root 核对审计、上述原始 result/JUnit/observation 及计划条款。第158轮四个后续变更源文件已逐项语义核对，新增迁移前置、released-run 恢复、lease 重试不改变该夹具分支；当前文件亦由 CFG06 r2/第190轮 pins 绑定。详情和原始证据定位见 clause-audit.md。

证据等级：本机真实服务/进程/CURVE/SQLite，合成 provider。正式条款允许合成 provider；此裁定不增加真实账户、双机或全笛卡尔矩阵声明。ARB02 的账户 Risk 确定拒绝负例仍待验，其他 ARB 用例状态不变。没有运行旧测试或账户调用。

仓库/分支：D:/bt_api_py dev，裁定前 HEAD e08da3c58cb3b3b040f7799642de38523bbd3229。范围仅本目录、当前状态汇总和第198轮进度记录；提交哈希见这些文件的 Git 历史。保留旧时点记录原文。
