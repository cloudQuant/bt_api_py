# DIM09 第275轮正式裁定（2026-10-17）

DIM09 按正式迭代计划第10节第781行及13.5节裁定为正式 PASS，累计49/68。本轮是本机合成 provider 验收，实际期权账户成交仍另计。

本轮源码提交 `3cf8cadfe2a165093068c8f96d7579af95ddb02b` 包含三腿 fixture、新增三节点和短记录。固定导出 R3 的真实 coordinator、account-owner handler、Risk/Execution、生产报价/尺寸映射和 SQLite 路径通过3/3，pytest exit 0、22.43秒，一条既有未知 pytest 配置 warning，stderr为空。Root核对原命令、stdout、JUnit和result，并重新只读核对392个源码pin及提交blob；没有重复执行测试。

这三个新节点是进程内 loopback，不是 spawned ServiceHost/CURVE 测试。它们补齐第三腿零成交、部分成交后保留实际残差与预留，以及非单位比例/异质乘数得到尺寸2/2/0.48、基础暴露4/6/2.4和1240 USD预算。服务进程、重启和恢复证据沿用下列已接受轮次，不将它们误称为本轮新执行。

| 正式要求 | 已接受证据 |
|---|---|
| 三腿比例、乘数、各阶段零/部分成交、残差和预算守恒 | R174协调器合同；R187生产v3报价构建器；本轮R3三个实际状态库节点 |
| 第二腿拒绝、第三腿UNKNOWN、协调/owner重启后原ID查询不重发 | R183真实wire/Host；R229独立query注册与策略退出后恢复 |
| 非零残差补偿、策略退出和严格终态 | R8提交`20980e23`真实Host/CURVE生命周期：一次CLOSE丢回复后按原action查询；只有精确Risk CLOSE回执、零实际签名敞口、预算结算并释放预留/锁后CLOSED；R243事件持久化支持 |

独立条款审查 `resume_bt_acceptance` 逐项核对上述来源，确认本轮接纳后无额外行为缺口。新测试和fixture已接受独立静态审查；提交字节绑定及原始运行文件索引见manifest.json，原始运行文件逐字节保存于raw-evidence.zip。相关原轮次记录仍保留于既有evidence目录和`docs/ITERATION10_DIM09_OWNER_COMPENSATION_R8_20261017.md`。

R2的三个失败来自测试错误比较业务intent ID与provider client-order ID。R3仅修正该断言，按hash校验journal中的ATTEMPT_STARTED/SOURCE_OBSERVED绑定到provider写入ID，未放宽业务门禁。R2后续只读SQLite检查曾创建/更新两个原目录的-shm及零字节-wal旁文件；原main DB hash不变，但不能声明整个原目录不变。原失败和旁文件均保留；未补造不可用的loopback内存轨迹。

验收范围只覆盖DIM09要求；不接受真实venue、双机或其他未完成的迭代用例。历史轮次的来源/日志限制仍按原记录保留。
