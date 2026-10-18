# 迭代10：CTP 借用会话提交控制器（2026-10-18）

本轮直接修改 SDK 现有 `dev`，父提交 `4ac21aac2efe628c6d113d899385d5cdd7a33403`。依赖已提交的 Execution `c8780be06eb9c7740ecbaa30503c84e35ff952a7`、CTP `4d0f564d0dca33d535f50f4454212cd36da33bfb` 和 Base `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`。范围为新控制器、三个 focused checks、本记录、QA 包和两份既有进度文档，共六路径；实际提交可由本文件 Git 历史定位。

`CtpExecutionOwner` 借用同线程已有 ACTIVE TraderClient、Store owner 和 callback adapter，先持久化队列回执，再通过 Store 签发的 session claim/native lease 发送一次 SUBMIT。V2 capability 的载荷从规范 JSON 解码并校验摘要和持久命令。订单状态只能从已应用回调的 Store snapshot 获取；本地 QUEUED 不表示交易所 ACK。非 READY 命令只读重放，不重新发送。此控制器不创建、启动或关闭 TraderClient，也不支持 CANCEL。

原生结果不确定时，CTP client 会封锁 session/family。Store 仍保留 CLAIMED/inflight=1，禁止写完成回执。控制器仅在 UNKNOWN 分支遇到 WriterLeaseUnavailable 后只读核对原命令及空回执，返回真实未决状态；不伪造 UNKNOWN 记录、不解除封锁。其他错误继续传播。

## 验收

三个不同检查分轮通过，未重复运行已有通过项：

- R1：1 passed / 2 failed，2.00 秒；错误范围、线程及 CANCEL 提前拒绝通过。两处真实接线问题保留为失败证据。
- R2：修正 V2 JSON 字段读取后，1 passed / 1 failed，2.39 秒；真实 Python TraderClient 与模拟 API 的单次提交、local QUEUED 与 ACK 区分、回调 ACK 和只读重放通过。非零原生返回触发账户封锁，之后完成回执被 writer fence 拒绝。
- R3：仅补跑该失败节点，1 passed / 1.67 秒；验证持久 CLAIMED/inflight、空回执、session/family POISONED、ACCOUNT_FENCED，以及重放/查询不写入、不重发。R2 失败后的合成 SQLite 只读检查作为诊断保留，不冒充原调用中的实时观察。

每轮各有一个预期 CTP extension fallback RuntimeWarning；408 个固定输入、四个仓库 HEAD 均不变，十个模块来源匹配，所列受监控进程/网络拒绝事件为空。固定导出只含 CTP Python 源码，扩展导入明确拒绝，测试使用 fallback 和模拟 API；该 guard 不是通用 OS/native 隔离。独立静态审查对最终源码和测试返回 GO_STATIC，审查者未执行 QA。

[Root QA 证据包](evidence/iteration10-ctp-borrowed-session-controller-20261018/root-qa.zip)：244,612 B，SHA-256 `b484faadf178b23ca01a451b3df0a36f92b854ed4ecc0aff946e7361e0b4ea46`，52 个成员均逐字节读回核对。包含各轮原始输出、固定输入清单、精确候选源码/测试、guard 与裁决；其余依赖可按 spec 中的提交恢复。未包含测试数据库、凭据或账户资料。

## 边界与下一步

证据级别为 `LOCAL_SQLITE_BORROWED_CTP_SESSION_SUBMIT_CONTROLLER_ONLY`，正式进度仍为 **62/68**。未运行真实 CTP/native/SimNow 账户、Host 或负载。下一轮把现有 facade 的 submit/submit_authorized 接到此控制器，保留 Basic/Risk admission，并补同一账户 session 的生命周期和回调泵；缺少 runtime_action_id 的 CANCEL 继续拒绝。旧 SimNow 会话处置授权、LOAD01 内存门槛和暂缓双机项保持原状。
