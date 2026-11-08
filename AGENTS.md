# AGENTS.md

当前按用户授权推进迭代 63（Gateway 统一转发与多策略共享会话），计划与分轮证据位于 Backtrader 的 docs/_internal/opts/requirements/迭代63-Gateway统一转发与多策略共享会话/。整期 IN_PROGRESS / 未验收，局部 SQLite、线程、CURVE 与合成 provider 检查不是账户或完整 Gateway 验收。Gateway 独立服务进程，策略按 config.yaml 连接；SDK/Gateway 允许必要架构调整，优先复用框架。迭代 53 已由用户于 2026-11-02 确认废弃，不再要求按该历史迭代暂停。逐项迁移与未验收事项仍以职责规范和当前计划为准。

## 先读：项目职责

分析、审查、提出迁移建议或修改本仓库及 SDK 子包前，先读本仓
[SDK 家族职责与边界](README.md#sdk-family-and-responsibility-boundaries)，以及
Backtrader 现有 checkout 的 `D:/source_code/backtrader/docs/TRADING_SYSTEM_ARCHITECTURE.md`
和 `D:/source_code/backtrader/docs/source/developer-guide/repository-responsibilities_zh.md`；
涉及迭代 53 时还要读取其当前计划。跨仓库工作分别阅读目标仓适用的 AGENTS.md 和 README。
若本地 Backtrader checkout 不存在，使用 SDK README 链接的公开架构文档并标注版本；不可假定远程 dev 与本地源码一致。
区分既定职责、当前源码、规划目标和证据级别；与源码不一致时标出差异，不将源码或模拟检查表述为账户验收。

## SDK 家族职责摘要

- 当前工作树的直连重构范围是 `bt_api_base`、`bt_api_ctp`、`bt_api_okx`、`bt_api_binance`；其他子仓原样保存在 `bt_api/deperated/`，未删除，也未据此卸载已安装发行包。以下职责摘要仍是家族及历史目标，并不表示归档组件在本轮启用或验收；旧来源证据和 CI 门禁需按新布局单独审阅。

- `bt_api_py`：统一 facade、provider 路由/发现及跨包会话与服务装配；CTP gate 的 SDK 审批与绑定检查属于 SDK，provider 原生检查及旧 runtime 来源迁移仍未完成；中立行情连接/订阅代际分类不持有 Feed 状态，也不证明来源，源码未行为验收。
- `bt_api_base`：中立 DTO、协议、接口和共享基础组件；订单来源与身份中立字段在此，13 家 provider 各自声明订单来源原生别名并选用 v5/v6，Risk/group 仅用 v6，v1–v4 旧合同已移除；Bybit/dYdX/Kraken 成交原生别名为空、覆盖待查；未声明的已知身份别名拒绝，动态字段与行为未验收；tick 交易日/行动日字段由 provider 显式声明：CTP 选择 `TradingDay`/`ActionDay` 及中立字段，其余 12 家只选中立字段，Base 默认 None；旧十家的大写候选已移除，动态覆盖待验证。订单状态字段候选由 13 家 provider 显式声明，Base 旧策略不再声明；其余十家使用 `status`、`order_status`、`state`，不证明原生状态覆盖。上述源码未行为验收，不自行提供账户或授权事实。
- `bt_api_ctp`、`bt_api_okx`、`bt_api_binance` 等 provider：各自交易所的 native/session、认证、字段、请求、回报、规则与中立转换；SimNow 原生订单和回报投影属 CTP，SDK 保留会话装配，源码未行为验收。
- `bt_api_execution`：显式启用的执行管理、持久派发、journal、对账和恢复；多策略聚合、拆单及算法为目标能力，不默认称已实现。
- `bt_api_risk`：显式启用的业务风险政策、准入、额度和计算；CTP 预算 journal 值、行状态投影与转换规则也归 Risk，SDK 保留会话与持久化权威，源码未行为验收；选中后失败不绕过。
- `bt_api_gateway`：可选账户范围命令/读取路由和本地服务合同；本包不提供 provider client、凭据加载器或 socket server。
- `bt_api_monitor`：可选遥测、指标、事件读取和健康观察；观察不自动证明交易/账户状态。
- `bt_api_runtime` 与 `bt_api_transport_zmq`：分别承担可选服务装配和传输，不能隐式成为普通 direct 前置。
- Backtrader 拥有框架对象与生命周期，并经 `bt_api_py` 使用中立 SDK 合同；不将交易所原生 adapter 放到 BT。
- 普通 direct 必需的身份、协议安全、请求关联和 UNKNOWN 处理不能因可选模块未启用而消失。

## 用户长期开发原则（2026-10-08）

- `bt_api_py` 和 `bt_api/` 下各子包直接在各自现有的 `dev` 工作区修改；用户未要求时，不另建开发 worktree。
- 每轮按“修复或优化 → 完成本轮约定的验收 → 提交到对应 `dev` → 下一轮”推进，不等整个大项目全部完成才提交。
- 验收失败则继续修复本轮；源码或模拟测试不能替代真实账户验收。
- 仅提交本轮已验收的明确文件或改动块，保留其他已有修改。子包先提交，再更新并提交父仓库 gitlink。
- 逐项登记、核对并归并历史工作区中的相关修改，避免遗漏提交或重复开发。
- 每轮记录范围、验收结果、仓库/分支及提交哈希。
- 跨 Backtrader、`bt_api_py` 及相关子项目的长期策略设计目标：同一份适用的策略代码在回测、`replay`、`shadow`、`paper`（外部模拟账户）和 `live`（生产账户）之间无需修改，使用配置与相应 Feed、Broker、Store 及其装配切换环境；交易语义、数据时钟、订单和账户回报应有明确合同。不支持的能力必须明确拒绝，不得静默回退或伪造成交；模式名称不授予写权限。此为架构目标，不宣称现有所有策略和模式已兼容。迭代 52 只实现/使用/验收 `paper` 和 `live`；`replay`、`shadow` 及旧本地模拟能力先盘点并研究后续归属，不在该迭代运行或本地验收。
- 中文文档编码：在 Windows 新建或修复供常用编辑器及 Windows PowerShell 5.1 使用的中文 Markdown 时，默认使用 UTF-8 带 BOM；已有文件先检查并尊重仓库规定、既有格式和冻结证据，不得为统一编码改写受保护文件。读取和脚本输出要显式指定 UTF-8。交付前同时验证 UTF-8 严格解码、中文无替换符，并用 PowerShell 5.1 默认读取或实际目标工具确认中文可读，不能只看终端是否显示乱码。

## Subagent model preference

When spawning a collaboration subagent, explicitly use `model="gpt-6.1-terra"`
and `reasoning_effort="xhigh"`, with `fork_turns="none"` or a supported positive
integer and the context needed for the delegated task. Preserve the main
agent's model unless the user explicitly requests a change.
