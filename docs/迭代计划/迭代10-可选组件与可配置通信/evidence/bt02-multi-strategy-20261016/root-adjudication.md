# BT02 多策略绑定小轮

Backtrader dev 815a834c3db185ee51c8927e6eea9cdb9fe32330，父 01fc254cfd4c81f659be7c4051b2d53a79b9fe6a，精确六路径。独立静态审查
backtrader_entry_review 对生产/API/ownership 修复及最终 synthetic 输入合同均 GO。
Root R1 的 ownership 节点通过；同次 positive 因 Strategy.start 尚无价格 bar 失败。
R2 positive 的 synthetic ExecutionScopeDirectory 使用不存在参数，返回 UNAVAILABLE。
最终 R3 仅重跑修正后的 positive：1 passed / 8.15秒 / exit0，808项来源前后不变。
R1 ownership 后续未重跑；后两次修改仅在 positive fixture（下单时机和 directory kwargs）。
原始失败日志和结果均保留，不将失败运行改写为通过。

真实 Cerebro/Store/Broker、SDK、CURVE ServiceHost 和 SQLite；两种 owner view 的数量
0.25/0.75保持分离，共享 Broker getters 保持账户语义。额外 duplicate Strategy 绑定拒绝，
错误 writer scope 在调用前拒绝，submit/cancel provider 计数保持零。关闭路径要求 closed
receipt、exit0、无 forced cleanup；borrowed runtime 不被 Session 关闭，owned runtime 释放。
来源是固定 SDK 7e1ad28429eb9b71c1be079402d80fbde8eb072f git blob export 和 Execution9943/Baseb4a/Transport29ab，
不能推定当前未提交 SDK group Host/Registry 改动已经接受。
一条既有 pytest 配置 warning 保留；早期运行有 Windows PyZMQ selector warning。

本轮仅接受上述接线及生命周期能力。策略 cash/value/cost、恢复完整矩阵和真正账户
不在本轮证据内，BT01/BT02 完整验收未完成；正式计数仍 22/68。接受的 QA 不重复。
