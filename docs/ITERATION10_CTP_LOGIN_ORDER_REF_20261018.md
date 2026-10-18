# Iteration10：当前 CTP 登录的 OrderRef 读回

本轮直接在既有 dev 完成。Execution 提交 `a1ad9aab194e94bd80f790bb78f87fe048fcc4e4`，父提交 `5827ea771f159ca7c69a1306b562ac057005a444`；范围仅 Store、新 DTO 导出及一个聚焦测试文件。SDK 父提交 `dcde0d846fc47bd44acaf56d3618ea4e6b9aa7c7`，交付该 gitlink、本记录、QA ZIP 和两份进度记录。未新建 worktree，其他修改保持。

新增 `read_ctp_callback_session_login_order_ref_facts(owner_handle)`：从当前活动 owner 已持久化的成功登录回调读回原始 MaxOrderRef、完整 session binding、序号和摘要。它检查精确 Store 发出的 owner、当前来源/代数、规范化原始 JSON 和摘要、登录绑定时已有的 AUDIT_ONLY 摘要，以及成功终端响应。MaxOrderRef 必须为非空的 1–12 位 ASCII 数字，补零属性仅转换表示。接口不写账本、不创建 cutover，也不授予下单权限。

Root R2 聚焦 **5 passed / 1.35 秒**，一条预期 CTP Python fallback 警告、stderr 为空；427 个输入与五仓库 HEAD 不变，5 个模块来源正确，所列拒绝事件为空。正常 Python TraderClient/SPI 与 Store 配合模拟 native API，覆盖成功只读/无初始化、其他 owner/断连拒绝、空值与超长值拒绝、重新计算原始记录摘要仍无法替换登录绑定摘要。各场景要求完整 stop receipt。最终源码与测试增量均独立静态 GO。

R1 原始结果为 **3 failed / 2 passed，2.34 秒**，保留原样。生产代码把序列化后的 `argument_slot` 误认成对象属性 `slot`；两个宽泛异常断言也在该提前失败点显示通过，故 R1 不接受任何用例。另一个测试误预期断连码为 disconnect，实际既有 LIFECYCLE_POISON 分支写 lifecycle_transition。R2 修正字段名、收紧 MaxOrderRef 特定错误断言并对齐断连码，重新验证全部受影响的五项；没有重跑此前已接受的 QA。

证据级别 `LOCAL_CTP_LOGIN_ORDER_REF_READBACK_WITH_SYNTHETIC_NATIVE_ONLY`。故意篡改只发生在本测试新建的临时合成 SQLite：修改时暂移该库的 UPDATE trigger，随后恢复其原始 SQL，再验证读回拒绝；不改生产 guard 或任何已有数据库。本轮没有真实 CTP、账户、ServiceHost、历史账本导入或账户外部写入隔离证据。

后续首次下单仍需真实同代登录/订单清单、完整旧账本或明确的新账户接入条件、账户独占条件及执行 profile。已提交 Backtrader 原型只保留内存 OrderRef 序列，没有可直接导入的完整持久映射；相关缺失输入已向用户询问。可独立继续的代码为延迟订单终态到达后，将受信撤单观察交给原 cancellation facade 完成 Risk claim 对账。

正式 **62/68** 不变。ARB05 双机部分及 DIST01/02 按用户要求暂缓；VENUE01/02、LOAD01 待验收。LOAD01 使用已确认工程稳定性门槛和 p50/p95/p99 报告，保留 9.875 GiB 内存门槛，不作 HFT 结论。旧 SimNow 保留会话限制保持。

[Root QA ZIP](evidence/iteration10-ctp-login-order-ref-20261018/root-qa.zip)：763607 bytes，SHA-256 `64f809900da04871d60dda254783e928439d79a8550b0a563139c8bb0d343d50`；50 个成员逐字节回读匹配，包含两轮原始结果、精确候选与导出 recipe，不包含完整依赖环境。
