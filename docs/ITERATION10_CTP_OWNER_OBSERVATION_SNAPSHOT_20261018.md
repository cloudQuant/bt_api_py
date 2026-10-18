# 迭代10：CTP owner 回调观察快照（2026-10-18）

本轮已在 Execution `dev` 提交 `c8780be06eb9c7740ecbaa30503c84e35ff952a7`，父提交为 `35589962e54482897ade35d9157bc6d97d287226`。范围仅为 `store.py`、`__init__.py`、`ctp_native_callbacks.py` 和新增的快照回归测试；其他已有修改保留，提交后 index 为空。SDK 父提交为 `e3200c7dc21a169957d40b4a45bf2503ffc70166`，本次父仓库提交更新对应 gitlink 和交付记录。

Store 新增同一 SQLite 读事务内的 owner 回调快照：核对原生订单身份、已应用回报及成交账本，去重累计数量并精确累加成交金额。缺失事实、UNKNOWN 和账户 fence 返回明确的 NotReady；撤单 ACK 不提升为订单 CANCELLED。查询不修改业务记录，佣金保持 `None / INCOMPLETE`。双连接 WAL 检查验证并发写入期间仍读取同一快照，并正常结束读事务。

成交 mapper 原先使用 `Decimal.normalize()`，会随调用方精度舍入价格。修复改为精确的定点格式化，仅清除小数部分尾零；现有正数、有限值和字段范围校验保留。新增检查覆盖 7 种价格、3 种精度及 Inexact/Rounded traps，完整事实保持一致。累计金额仍为精确 Decimal，此轮不派生平均价。

## 验收与保留的失败记录

Root 在固定源码副本上增量验收，共 **7 个不同检查通过**；不是一次完整 7 项重跑。每次执行的 285 项固定来源及 SDK、Execution、Base 三个 HEAD 均保持不变。

| 执行 | 原始结果 | 后续处理 |
| --- | --- | --- |
| R1 | 6 failed | 修复原生回执 outcome 位置；修正 UNKNOWN 夹具错误码。guard 把未加载的可选模块当作必加载来源，R2 改为仅在加载时核验。 |
| R2 | 1 passed / 5 failed | UNKNOWN 检查通过；修复 owner 源连接代次与活动连接代次的比较。 |
| R3 | 3 passed / 2 failed | 显式零成交、撤单 ACK/终态、双连接 WAL 通过；剩余夹具 BUY 成交价高于限价 100，修正为有效价格。未恢复被封装的原始内部异常。 |
| R4 | 1 passed / 1 failed | 缺失后补齐原生身份检查通过；剩余失败暴露 mapper 的 Decimal 精度依赖。 |
| R5 | 2 passed / 0.77 秒 | 仅补跑成交前缀/精确金额失败节点及新增 mapper 精度检查；exit 0，无 warning。 |

R2–R5 来源校验通过，受监测拒绝事件为空。guard 仅覆盖列出的进程/网络审计事件和 `bt_api_ctp` 导入，不能解释为通用 OS/native 隔离。`service_order_entry.py` 使用提交版本作依赖来源，该模块在本轮没有加载；工作区中它的未验收修改未被使用或提交。

[Root QA 原始证据](evidence/iteration10-ctp-owner-observation-20261018/root-qa.zip)：1,127,193 字节，SHA-256 `57e644bbb4aecd93d7e9813a36d6065824c6c7b9a6503504a53d0d0cfe027708`，104 个成员逐字节读回校验。包含 R1–R5 原始日志、JUnit、spec、来源 pins、所选源码和 Root 记录；不含临时测试数据库或私有账户资料。依赖按 spec 和已记录提交恢复，该 ZIP 不是独立完整运行环境。

## 证据边界与下一步

证据级别为 `LOCAL_SQLITE_CALLBACK_OBSERVATION_SNAPSHOT_ONLY`，仅支持 V2 单源回调。未执行 Host、native、账户或负载验收；正式进度仍为 **62/68**。下一步接入 ProviderObservation、真实账户写入口与多策略路由。旧 SimNow 会话处置仍待授权，LOAD01 内存门槛尚未满足；ARB05 双机部分及 DIST01/02 按用户要求暂缓。
