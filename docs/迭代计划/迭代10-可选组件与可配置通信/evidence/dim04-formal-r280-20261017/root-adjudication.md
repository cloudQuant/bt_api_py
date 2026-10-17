# DIM04 第280轮正式裁定

日期：2026-10-17  
用例：DIM04，多策略共享账户平仓归属与故障处理  
依据：正式计划 `正式迭代计划.md` §10 第776行。  
裁定：**正式 PASS**，总计 **54/68**。

## 条款与证据

正式要求为“多策略同时平同一合约，净/双向/今昨仓、晚到成交、自成交冲突、策略级批量撤单”，验收目标是“真实可平量预留与归属对账；不平他人仓、不扩大账户控制权限”。本轮按互补证据裁定，不要求每条能力都在同一节点重复证明。

| 子条件 | 固定证据 | 裁定范围 |
|---|---|---|
| 多策略同合约的净仓可平量隔离 | R29 contention R5；R280 A4 | R29 两策略各平0.75，之后发生超额请求时拒绝；A4进一步验证 A 只使用自己0.75后，A 的额外0.01被拒绝、provider未派发，B仍可平自己的0.75。结算后两策略仓位归零，持久预留/命令/证明按owner分开。 |
| 双向及今昨仓归属 | R12 | 多头/空头及TODAY/YESTERDAY切片按owner拒绝借用；本地合成Host作者运行记录通过。原始JUnit/stdout未保存，保留作者运行证据等级。 |
| UNKNOWN、晚到成交、重复回报、按原业务ID恢复 | R170；R279 R2/R3后验；late-fill R3 | UNKNOWN时不重派并保持hold；恢复沿原业务ID查单/成交；重复稳定fill只进入持久source一次；late partial继续保持hold，终态receipt再解决。R279 R2/R3 pytest最终断言失败保留原状，采用已核验的只读SQLite后验与此前通过的业务断言，不将原pytest标成通过。 |
| 真实本地socket关闭/重建与取消恢复 | R279 R8后验 | 本机socket断开后重建，取消派发一次；取消receipt仍为UNKNOWN，目标订单由独立Risk证明为CANCELLED且无fill后才释放原订单hold。R8 pytest因计数oracle失败而保留失败状态；SQLite sidecar用于说明已执行的业务事实，不代替原结果。 |
| 自成交冲突 | R230 R6 | 两策略相反方向的BTC开仓均以SELF_TRADE_CONFLICT拒绝且零dispatch；无冲突的ETH订单正常派发一次，已有close permit不变。 |
| 策略级批量撤单 | 已接受 owner-cancel 固定来源节点 | 仅取消调用策略/运行中的订单，重放不重复派发，peer订单仍ACKED；Host与客户端正常关闭。 |

A4使用固定源码导出中的唯一选择器 `test_dim04_strategy_owner_close_isolation.py::test_dim04_owner_close_exhaustion_does_not_borrow_other_strategy_position`，1 passed，pytest exit 0，7.63秒，有一条既有pytest配置warning；stderr为空。固定来源的397项source pins前后相同。测试运行期间有665条导入审计记录未包含filename；另有独立post-run module-origin recheck覆盖126个file module及一个namespace且无差异，因此本裁定不声称逐模块同进程`__file__`证明。Root核对了正常Host/client关闭和4个spawn PID正常退出，无fallback cleanup。

## 证据限制与历史失败

上述均为脱敏本机合成provider、真实本机Host/SQLite/Risk执行路径；不声称真实账户、真实交易所或跨机验收。R12以及R29 crash-recovery只保留作者工具控制台结果（各1 passed及既有warning），没有原始stdout/JUnit/result文件；证据包不补造这些文件。fdd20 owner-isolation旧pytest原始输出缺失，未作为证据；本轮A4是另一个固定来源、完整通过的新节点。R279 R2/R3/R8的pytest失败、stdout/JUnit及sidecar均保留，后验结论不改变失败状态。R3的账户级group-authority dispatch holds归零后，coordinator `budget_state`仍为`HELD`；本裁定不称其为CLOSED或完整预算释放。

## A4运行来源

- SDK父仓 `dev`：`475e4db48acff29161a8216253e275507347ef91`
- Base：`2e256869bc6deadebbedef135b373c3314b20ef7`
- Risk：`beb1939dd379b519ef18d0ecb83d1f1ff217ac47`
- Execution：`4374237705478039d60760e5f68df0e6ed076907`
- Transport ZMQ：`ee4cacaee04182fda2d1b9124c2f3a1675aed07b`
- 固定测试文件：`tests/integration/test_dim04_strategy_owner_close_isolation.py`，17,746 bytes，SHA-256 `8364E2D00DA011A8D676612F0D12D5D669AE73B410413F92328D581B6F708818`。

压缩证据成员及各成员SHA-256/长度列在同目录 `manifest.json`。R279、R230及late-fill既有证据包以其原始归档字节嵌入；无原始文件的作者运行仅由既有记录映射，明确不伪造。
