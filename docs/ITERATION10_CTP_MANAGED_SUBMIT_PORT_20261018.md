# 迭代10：CTP ManagedExecutionFacade 提交与回调对账接线（2026-10-18）

本轮在 SDK `dev` 父提交 `2c681881884bab0a4679c65b3d79cf46416e4cc3` 上增加真实 facade dispatch port。证据级别为 `LOCAL_MANAGED_FACADE_CTP_SUBMIT_AND_FAKE_CALLBACK_RECONCILIATION_ONLY`，正式验收仍为 **62/68**。

## 实现范围

- `CtpManagedSubmitPort` 以两阶段装配绑定同一 BasicAdmission Gate/port、Store、writer generation 和已有 ACTIVE CTP session；授权、参数、登录映射与持久 DISPATCHING 记录核查先于编号分配。
- 新订单复用 Store 的已建立 cutover 分配 OrderRef，并使用同一个 TraderClient 的 RequestID 序列。已有可信预留按 Store 原 runtime ID 精确复用。真实有界队列的成功入队/取出产生本地回执，消费后的完成计数在异常路径也归零。
- `submit_authorized` 经已提交 issuer/controller 发起一次请求；本地 QUEUED 不成为交易所 ACK。无回调时抛出 NotReady，由现有 facade 持久化 UNKNOWN；重放不再次发送。`query` 从持久 command/reservation 重新构造关联并读取已入库回调，只返回真实 callback projection。
- 通用 Facade、Store 和现有 SimNow 守卫未修改。本轮不创建或关闭 native session，不实现首次 cutover/bootstrap、CANCEL 或 account_owner 服务能力发布。

## Root 验收

Root 固定源码 R1：**2 passed / 1.99 秒**，Python/pytest exit 0，stderr 为空。唯一 warning 是有意排除 CTP 扩展后的 Python fallback 提示。413 个输入及 SDK/Execution/Base/CTP 四个 HEAD 前后不变；14 个模块 origin 正确，所列网络/进程拒绝事件为空。该审计仅覆盖列明事件和 CTP 扩展导入，不是通用操作系统隔离。

两个新节点覆盖：正常 facade 自行 admit/claim 后的一次提交、UNKNOWN 与 COMPLETED/QUEUED 区分、重放不重发、无证据查询、线程/作用域/并发 writer 拒绝；真实 Python SPI 回调进入 Store 后对账为 ACKED/FILLED；复制授权在 OrderRef/RequestID 分配前被拒绝；两个新 intent 共享递增 RequestID 且 OrderRef 不同；已有非本端口算法的 runtime ID 被精确复用；队列完成计数归零。原有已接受套件未重跑。

独立审查 `GO_STATIC`，仅静态审阅这两个固定文件，未运行 QA。最终源码 `6f287a3b344dfc4cc2c8208e6e0d03b5fac55830276364f4785c2a4b17b68068`（19,452 B）；测试 `d340ca3cc8059f74a6b03ef63be81aa79f521ca320ded3b71fe1e47c53328967`（14,469 B）。Python fallback 使用真实 TraderClient/controller/Facade/Store/Gate 与合成 API、回调叶子；没有真实交易所、账户、Host 或完整负载验收。

## 证据与交付

[Root QA 证据包](evidence/iteration10-ctp-managed-submit-port-20261018/root-qa.zip)：122087 B，SHA-256 `c270c54d0c0046ce0e3e551bde7af93e8d0b631d2efb19846dd16f0b8da790c4`，22 个成员逐字节回读匹配。包内含原始执行结果、固定来源 recipe/pins、精确候选与关键夹具；其余依赖通过记录的 Git 提交重建，不声称是脱离仓库可独立运行的完整依赖包。

相关提交：Execution `e8800924d5312cc57051a3bf10ac5fce778ff751`；Base `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`；CTP `4d0f564d0dca33d535f50f4454212cd36da33bfb`。本轮按明确六路径提交 SDK dev；最终提交哈希由 Git 历史及 Root postcommit 记录，不回写冻结 QA。保留其他已有改动。

剩余正式项仍为 ARB05 双机部分、DIST01、DIST02、VENUE01、VENUE02、LOAD01。已有 SimNow retained-session 限制、最多1手模拟授权、LOAD01 9.875 GiB 启动门槛及工程稳定性口径不变；源码/模拟通过不替代它们。
