# 迭代10：CTP ProviderObservation 接线（2026-10-18）

本轮直接修改 SDK `dev`，父提交 `2a6a322c863d8484ea0a27727483e2ffd589bb4f`；Execution 依赖为已接受的 `c8780be06eb9c7740ecbaa30503c84e35ff952a7`，Base 为 `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`。范围为 owner reconciliation port、新增回归测试和本记录/证据/两份进度文档，共六个路径。实际提交哈希由本文件 Git 历史定位。

`read_provider_observation(intent, dispatch_binding)` 从 Store 原子回调快照生成 ACKED、PARTIALLY_FILLED、FILLED、CANCELLED、REJECTED 观察。它核对持久 intent、reservation、完成派发的 queued=True binding、当前 owner 和原生身份；本地排队回执本身不作为交易所 ACK。UNKNOWN、账户 fence、缺失回报和失效 lease 保留为 NotReady，存储操作错误继续传播，读取不修改业务记录。

成交金额仍由 Store 精确累计；平均价以独立 50 位 HALF_EVEN Decimal context 派生，不受调用方精度和 traps 影响，非终止商为明确的舍入投影。手续费字段保持 None。新增有版本和长度上限的 CTP 原生订单 ID 编解码器，严格规范化并可逆还原 ExchangeID/OrderSysID。

## 验收

独立静态审查 GO。Root R2 固定源码回归 **4 passed / 1.70 秒**，exit 0、无 warning、受监测拒绝事件为空；六个加载来源准确，320 个输入和三个 HEAD 不变。检查覆盖状态及成交金额/均价、重复 TradeID 去重、伪造 binding 与失效 lease、SQLite 错误传播、UNKNOWN/fence 保持只读，以及 ID codec 的规范性和边界。

原 R1 为 4 passed / 1.89 秒，但记录一次被捕获的 socket.bind 拒绝，未作为干净 guard 验收。固定来源 import-only 诊断 R2 复现了 urllib3 在导入时对 ::1 的 IPv6 能力探测；原 QA 没有 stack，未声称恢复原事件调用栈。诊断 R1 错用环境键并加载工作区来源，保留为诊断历史。QA R2 只调整启动 guard：预加载 urllib3 后再安装拒绝钩子，并在结束时断言 DENIED 为空；源码和测试字节未改。该 guard 仅覆盖列出的网络/进程审计事件及 bt_api_ctp 导入，不是通用 OS/native 隔离。

[Root QA 证据包](evidence/iteration10-ctp-provider-observation-20261018/root-qa.zip)：172,558 字节，SHA-256 `cd8c89e42db777490a69698a4475d55f0776cc8f3a249066195b0750e8966f4c`，47 个成员逐字节读回验证。包含原始两轮结果、固定输入清单、所选源码、两次诊断和 Root 裁定；依赖按 spec 中的提交恢复，不是完整独立运行环境，不含临时数据库或账户资料。

## 边界与下一步

证据级别 `LOCAL_SQLITE_CTP_PROVIDER_OBSERVATION_COMPONENT_ONLY`。未运行 CTP native、真实账户、Host 或负载；正式进度仍 **62/68**。下一步组合单账户执行 owner，把 stage、queue、dispatch 和 callback 泵接到同一真实 TraderClient 生命周期；现有只读 Gateway owner 不增加写权限。多策略命令路由另需验收。旧 SimNow 会话处置授权、LOAD01 内存门槛和双机暂缓状态不变。
