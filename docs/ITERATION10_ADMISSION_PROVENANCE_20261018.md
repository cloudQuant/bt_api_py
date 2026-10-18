# Iteration10 BasicAdmission 授权来源校验（2026-10-18）

Execution `dev` 提交 `e8800924d5312cc57051a3bf10ac5fce778ff751`，父提交 `c8780be06eb9c7740ecbaa30503c84e35ff952a7`。仅修改 `basic_admission.py` 和新增 `test_execution_basic_admission_provenance.py`。父 SDK 在 `65dc9605` 之后更新该 gitlink，随本记录提交；实际 SDK SHA 由本文件 Git 历史定位。

新增显式选项 `retain_claimed_authorizations=False`。启用时 Gate 留存自己实际签发的原始授权，并只读复核原始对象、可信上下文、订单、注册派发端口、当前策略及参数、写入租约与有效期。复制或重建的授权不被接受。pending、claiming、claimed 共用容量上限；Store 和策略验证不持有 Gate 锁。默认关闭，保留现有非 CTP 路径。

Root 固定源码新回归 **4 passed / 0.82 秒**，受影响默认路径兼容检查 **3 passed / 0.73 秒**，均 exit 0、无 warning、stderr 空。各次 137 项输入及 SDK/Execution/Base 三个 HEAD 未变；来源校验通过，已列出的进程/网络事件与 CTP 导入拒绝记录为空。该 guard 不是通用 OS/native sandbox。独立精确 pin 静态审查 GO；子包提交后核对两文件 blob、空 index 与其他既有改动保留。

证据为 `LOCAL_SQLITE_BASIC_ADMISSION_PROVENANCE_ONLY`，不证明 CTP 命令、native/provider 或账户验收。CTP 具体授权适配层仍在本轮之后开发；此处不宣称已经接入服务入口。正式进度保持 **62/68**。真实 SimNow 保留会话限制、LOAD01 9.875 GiB 启动门槛和双机暂缓均不变。

[Root QA 原始证据包](evidence/iteration10-admission-provenance-20261018/root-qa.zip) 包含两个实际执行、固定源码测试片段、静态审查和子包提交核对；31 个成员逐字节读回核对。ZIP SHA-256：`6f959bf2523d9f119e465b0128eef810976a004adaa7ad3b2c5ff63e5f8fcd7a`。
