# MIG03 第276轮正式裁定（2026-10-17）

## 裁定

Root 将 MIG03 判定为正式 PASS，迭代10当前正式通过数由49/68更新为50/68。此结论由多轮互补的本机合成状态与真实进程证据组成，不表示真实交易所账户/provider切换，也不表示双机验收。

| 第11.3节要求 | 接受的证据 |
|---|---|
| 旧/新 epoch、旧 writer拒绝、导入失败与安全回退边界 | R188 两epoch实际进程/CURVE/SQLite演练：旧epoch拒绝、单owner、UNKNOWN保留、失败路径与首次写后拒绝反向迁移。R235 R13 完整切换节点补充目标投影/政策/控制核对、显式启用与首写后的旧epoch拒绝。R2/R3失败原件未改写。 |
| 身份映射及订单、归属、预留、幂等和游标守恒 | R210 离线 typed map-only 导入与持久注册证据；R235 R13 的完整源/目标投影对账及游标切换；R188/R210原始失败和回退边界仍保留。 |
| 受管 paper 源账本的导入与旧写失效 | R273 AFI12正式迁移证据；本轮R4进一步使用真实 AFI snapshot producer/scheduler，确认其持久快照，正常停止并 join，导入处于ACTIVE的来源回执后旧 writer拒写且原账/回执保持不变，目标侧读取/守恒核对通过。未给未绑定snapshot自动分配账户。 |
| configured consumer停写、readback及启用顺序 | R235 R13 与R276 R4的组合覆盖来源消费者正常关闭、snapshot/readback和目标端启用前检查。R4保留一个不同run的STOP_NEW receipt/claim并验证其迁移守恒；这不是该paper账户的STOP_NEW直接fence，账户本身旧writer拒绝由AFI12 ACTIVE receipt/数据库触发器证明。 |

R276固定来源Root运行 `test_mig03_cutover_composes_real_afi12_scheduler_and_stop_new_receipt`：1 passed，pytest exit 0，35.90秒，8条warning，stderr为空；3,588个source pins运行前后完全相同。R4的精确cleanup receipt记录三个测试自有Host的正常close/refusal ACK、exit code 0、句柄关闭、一次有界reap、无cleanup错误且未尝试fallback。另有4个边界单测通过，用于确认兼容fixture；它们不是新的端到端迁移证据。

Root后续只读核对确认3,588个source pins均匹配，并确认4个已提交测试blob与checkout字节一致；没有重跑R4或已接受节点。

R188、R210、R235 R13、R273与R276 R4共同闭合正式条款。R276的STOP_NEW数据仅证明相邻run的receipt保全；readiness摘要是审计引用，不能单独当作独立身份/状态证明。所有接纳的迁移数据均为脱敏合成状态，外部provider、真实账户和双机行为另计。

## 保留的失败与边界

- R3原运行因最终cleanup receipt写入处缺少`json`导入而失败；该FAIL和原始运行文件保留，R4仅修复候选后重新运行一次，不覆盖R3。
- R2在AFI快照与SDK reservation ID投影比较处失败，未形成可验证的cleanup receipt；其清理状态仍为未知，不把该次运行记作已清理。
- R4保留8条既有warning；它们没有被改写为无warning结果。
- 本裁定不宣称真实provider切换、真实用户迁移或跨机完成。

固定代码提交为SDK `002d8d45be73be25d6622f5028bab1120685c9fc`。R276的原始command、stdout/stderr、JUnit、result、source-before/after、spec、runner及cleanup receipt逐字节收录于同目录`raw-evidence.zip`；四项兼容单测和R2/R3历史输出也保留在归档中。逐文件大小、哈希、source pin与运行结论见同目录`manifest.json`。
