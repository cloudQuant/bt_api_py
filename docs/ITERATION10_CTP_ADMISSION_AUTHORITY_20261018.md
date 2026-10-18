# Iteration10 CTP BasicAdmission 命令授权（2026-10-18）

SDK 在现有 `dev` 父提交 `42e5ceccb8d928e7dcc188c8b34dab15f8898de6` 上新增 `ctp_admission_authority.py` 与对应测试。依赖已提交的 Execution `e8800924`、Base `fb47f3dd`、CTP `4d0f564d`。本轮 SDK 提交 SHA 以本文件 Git 历史和 Root postcommit 记录定位。

适配器只接受 Gate 实际签发并保留的原始授权，核对持久化 DISPATCHING 订单的 BasicAdmission 引用、上下文与指纹，读取既有 OrderRef 和活动会话，按显式投资者/合约/交易所映射生成准备命令。独立 Risk permit 不冒充 BasicAdmission 引用。派发前重新校验全部请求和关联字段，由 Store 在领取命令的同一事务内消费一次性授权。复制或过期授权、错误上下文、投资者不匹配及手数篡改均被拒绝；无效准备不占用真实授权名额。

| Root 固定源码运行 | 结果 | 修复或接受范围 |
|---|---|---|
| R1 | 10 failed / 3.17s | 新代码误要求账户级 lease key 等于策略 scope key，构造阶段即失败。原结果保留。 |
| R2 | 8 passed、2 failed / 3.23s | 改用 Store 正式账户租约校验；余下成功路径引用了 correlation 不存在的 cancel_target_order_ref。 |
| R3 | 2 passed / 1.89s | 仅移除不存在的关联字段引用，保留 command 层及其余真实字段检查；只补跑两个失败项。 |

累计 **10 个不同检查通过**，R2 已通过的八项未重复。所有运行各有一条预期 CTP extension fallback warning，411 项固定来源及四个 HEAD 均未变化，12 个模块来源正确，已列出的进程/网络拒绝事件为空。最终修复获独立静态 GO。正向路径使用真实 Python TraderClient、Store、Gate 和执行控制器配合 fake API，验证一次 ReqOrderInsert 与重放不重发；本地 COMPLETED/QUEUED 不表示交易所 ACK。平今仓 offset 与 deadline 上限另有直接校验。

范围限于借用既有单 scope 活动会话的 SUBMIT 授权；支持 LIMIT/DAY、NET、同名合约映射，无 tags。当前 TraderClient 要求 InvestorID 与登录 UserID 一致，配置不符会明确拒绝。SHFE/INE 平仓须明确 TODAY/YESTERDAY，不把 CLOSE/ALL 自动推定为平昨。未提供生命周期、服务 facade 或 CANCEL 接线；未加载 CTP binary，也未运行真实 provider、账户、Host 或负载。固定 Python fallback 和 audit guard 不是通用 OS/native sandbox。

正式进度仍为 **62/68**。保留 SimNow 会话限制、LOAD01 9.875 GiB 门槛及双机暂缓；接续工作是将已验收的授权与控制器接入唯一服务写入口及明确的生命周期。

[精确 Root QA 包](evidence/iteration10-ctp-admission-authority-20261018/root-qa.zip)：50 个成员逐字节读回，SHA-256 `0fe6da445307267f22302e1c4344244d63d6942bdc438a0d74ed40711ac4d65f`。包含各次实际命令/原始结果、测试与候选源码、继承 fixture、来源 pins 和裁定；完整依赖按已记录 Git 来源与固定导出重建。
