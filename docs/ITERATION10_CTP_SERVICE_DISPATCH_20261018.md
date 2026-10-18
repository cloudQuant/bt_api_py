# 迭代10：Execution 独立撤单端口与账户服务接线（2026-10-18）

本轮在现有 dev 修改；Execution 子包提交 `e34d5aa425f9169a28fc0692daf29ed6df2df626`，SDK 父提交为 `1363b9556f131edab67e8e3f0b9072a482db91da`。正式进度仍 **62/68**，新增正式 PASS 为 0。

## 实现范围

- ExecutionOrderEntryService 接受可选 cancel_dispatch 和 trusted cancel_before_dispatch；缺省沿用既有 dispatch。
- 普通及 owner-batch 撤单均走选定端口；批量保持配额→目标准备→单次 Risk claim→带精确 receipt 的派发。重复请求只读回，不重新准备或发送。
- SDK account_owner/account_group_owner 资源和账户服务包装层传递上述资源；未改变 RPC 授权或 Basic/Risk 校验。Paper 域继续要求同一个精确 Paper 端口。
- CtpManagedSubmitPort 提供绑定后、同线程的只读 execution_owner，未绑定时拒绝；不创建第二会话。

## 验收

共 **10 个不同本地检查**：owner 接口 1 项通过（1.86 秒，一条预期 CTP Python fallback 警告）；服务检查 R2 为 7 项通过、2 项失败；R3 仅补跑失败的两个组件装配项并通过（1.70 秒，无警告）。R2 两次 socket.bind 拒绝均由配置夹具申请临时端口引起，JUnit 保存精确路径；运行退出 1，未记为整套通过。R3 改用固定配置 endpoint，不启动 Host；拒绝事件为空。

各轮固定源码及五仓库 HEAD 前后未变化。证据是 SQLite/真实 Risk claim、服务及组件构造配合合成 provider；没有 CTP native、真实账户或 ServiceHost 生命周期验收。R2 的七项包括 Risk claim 的原始 receipt 交接、准备失败零 claim/零发送、UNKNOWN 回读不重发及 Paper 兼容保护。

最终仅调整新测试的导入排序及包装层仅用于注解的 TYPE_CHECKING 导入。原测试字节保留在 QA 包；导入绑定和其余 AST、包装层类/函数 AST 保持，独立静态复核 GO，未重复已通过检查。三个新测试 Ruff 通过，生产源码的既有 lint 问题未新增。三个原有混合修改文件均只暂存 HEAD 派生的本轮改动，保留其他 WIP。

[Root QA 原始记录](evidence/iteration10-ctp-service-dispatch-20261018/root-qa.zip) SHA-256：`123194c92a1fdbe5df165f61705fb3b4bf9e6e63a4d60648ca253ab3d56f0b1d`。

## 后续缺口

CTP 运行时装配仍须验证两个端口共享同一 owner，完成单一 TraderClient 的启动、登录、回调和正常停止，并把同一订单接入 Risk 派发前账本与终态证据。有成交订单仍缺逐笔手续费和估值等完整证据；不得由不完整数据解除 Risk 锁定。

本轮只读进程元数据确认保留的 SimNow 父/子进程 PID 69712/65956 与原创建时间匹配且仍在运行；未发送命令或推断正常关闭。旧会话限制、LOAD01 内存门槛和双机暂缓条件保持。
