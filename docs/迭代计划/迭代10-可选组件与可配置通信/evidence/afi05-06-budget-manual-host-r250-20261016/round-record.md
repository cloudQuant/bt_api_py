# R250：AFI05/06 预算准入链的本地合成 HTTP 验收与最小手工 scope patch

日期：2026-10-16。本轮只交付一条本地固定源码组合的认证 AFI HTTP → SDK ServiceHost → Risk admission synthetic path，以及 SDK `service_components.py` 的精确 manual-account-scope patch 候选和 AFI 测试文件。未改 Risk 源码、AFI 生产服务、共享进度文档或 live SDK 混合工作文件；没有暂存或提交。

## 验收结果与执行者

R8 仅运行此前失败的 `test_authenticated_manual_http_order_uses_confirmed_budget_risk_host` 节点：1 passed，0 failed/error/skipped，pytest exit 0，4.81 秒，2 warnings，stderr 为空；2,190 个固定 source pins 前后相同，5 个 repo HEAD guards 稳定。R4 先前 Root 运行中的 `test_budget_activation_refuses_missing_confirmation_and_incompatible_old_ledger` 通过；同轮 HTTP 节点失败。R250 没有重跑已通过的拒绝节点。

R8 `result.json` 原字节保留，其 `evidence_level` 字段误标为 ROOT_FIXED_SOURCE。实际调用者是作者 `/root/afi_policy_governance` 在本地 Windows 环境执行固定 focused runner；Root 只读取了原始输出，没有运行该节点。此包的证据级别据实记为 `AUTHOR_LOCAL_SYNTHETIC_AUTHENTICATED_AFI_HTTP_TO_SDK_RISK_HOST_BUDGET_ADMISSION_ONLY`，不改写原始结果，也不将其描述为 Root QA。

成功节点使用合成 SQLite user/policy catalog/confirmation 数据、真实 AFI app lifespan 和 authenticated manual-order HTTP route、固定 SDK Risk Host 与合成 provider。超预算请求返回 `ADMISSION_DENIED`/durable `REJECTED`，Risk increase count/notional 仍为零且没有 provider write；同一账户预算内请求得到 `OK`/`ACKED`，Risk ledger 增加一次、notional 为 20，合成 provider 有一次 write 与 order/fill 查询。响应未返回 `consumer_effective`，因此不宣称所有成员实际生效或全局原子准入。

## 代码候选

AFI 测试：`D:\source_code\ai-for-investor\src\backend\tests\test_afi_budget_manual_admission_host.py`，41,980 bytes / SHA-256 `f9cacbad0b13a7adf177977751f60ca2d9ea693412c535210e8ba0cbaaa82be3`。唯一生产 patch 是 SDK `bt_api_py/service_components.py`，候选实际从 SDK 源码提交 `2e9302f46ba8e70fec58014717955ff177a8d8a8` 的 Git blob `2915af5f369b5f26258e4a3f7c21f93707e487db` 生成；交付时当前 SDK HEAD `366eb466b147cb96b51dd1edc5d57e6510ea5d92` 的同一路径 blob 已核验相同，因此 patch 可复用，当前 HEAD 尚未对本组合重跑 QA。；base 105,851 bytes / SHA-256 `afd7f220223af7c026e8d010faa36cacc2b11f2364235fdc5a6383567a69e0f5`，patch 1,276 bytes / SHA-256 `bd0b32c1db22791357cf415d272bb9f529beb4bca7abb3f3d5e4c02e0945ac14`。它仅保留原策略 scope 分支并允许精确无 strategy 的单账户 manual-book 分支；该分支仍要求单一 `account_ref`、非空 `manual_book_ref`，且无 portfolio/group/leg。候选从 HEAD 字节生成且只替换该 validator predicate；没有覆盖 live `service_components.py` 并行 WIP。

R8 固定运行源码为 export R15：SDK parent `53f33158429f89fd9b4dce83c068056c99d6778f`、Risk `ccac8818312c1474077c00bfe9d8f12521af39c9`、Base `cc6036c89c534a17d93e6b793f930c576b4aa960`、ZMQ `a397fdb460d2deb22e67ceb4963992ef63dbbb0e`、AFI `ac52830cd66c8ca79547db5be56a971906ccef4c`，另有 export manifest 精确列出的 overlays。R8 SDK run guard HEAD 为 `9a1495ad4be0670f7f2efe7e81c3ae9650877023`；本轮 patch base 是 `366eb466b147cb96b51dd1edc5d57e6510ea5d92`。所以 R8 通过不是对当前 SDK HEAD + patch 组合的重新验收。

## 正式 AFI05/AFI06 条款映射

依据正式计划第 797–798 行。此表记录已有支持证据与缺口，不代表 AFI05 或 AFI06 通过。

| 正式条款 | 已有本地证据（不重复验收） | 尚未证明 / 正式状态 |
| --- | --- | --- |
| AFI05 草稿、审批、发布 | R219 本地固定 Risk PolicyCatalog/SQLite 草稿编译；R226 受认证 maker/checker 与 owner-side 发布/确认 Host；R228 resolver；R250 手工 HTTP 消费已确认单账户预算。 | 普通 AFI 预算管理 UI/API 的完整流程、部署与 audit authority 未闭合；R250 不是预算管理 HTTP/UI。 |
| AFI05 逐账户生效、拒绝/部分生效 | R226 owner authority 与逐成员 receipt；R228 精确 generation/member/confirmation resolver；R250 一条合成账户 admission 路径。 | 多账户逐账户 effective/partial、生产 consumer binding 读回及重叠成员核对未验收；`consumer_effective` 不置 true。 |
| AFI05 旧预留与政策回退 | R226/R228 持久 hold/refusal 边界；R4 已通过 missing-confirmation/incompatible-old-ledger 拒绝节点并保留旧占用断言。 | 活跃旧 policy 下生产回退、部分发布失败处置与受控窗口运维未闭合。 |
| AFI05 AI/普通用户不可自批增额或解冻 | R226 local maker/checker separation / unprivileged principal 服务层合成测试；R250 的 `Role.USER` 仅提交受限手工订单。 | AI/普通用户对政策审批、增额、解冻的真实 HTTP 权限矩阵未执行。 |
| AFI06 同量纲静态组合预算守恒 | R219 确定性静态编译与精确单位/金额拒绝反例；R226 持久 Risk owner；R250 单账户已确认版本的 admission 消费。 | 组合成员总额、未分配额与全部账户实际占用目录核对未闭合。 |
| AFI06 并发发布、重叠成员、占用/UNKNOWN、撤回未确认 | R226/R228 单 Risk owner durable claim 与 UNKNOWN/缺确认 hold 的固定源测试；撤回仅限未发布 proposal。 | 多 writer/重启/部分发布端到端并发证据仍缺；不宣称跨账户全局原子准入。 |
| AFI06 陈旧 FX、不同 metric 混加拒绝 | R219 静态编译对 FX/metric 不相容输入的源码拒绝测试。 | 无实时 FX 接入，不声称组合实时指标覆盖。 |

先前 canonical packet：R219 `afi05-06-static-budget-20261016`、R226 `afi05-06-budget-authority-r226-20261016`、R228 `afi05-06-budget-admission-resolver-r228-20261016`；各自 pins 见本轮 manifest，不重复 QA。

## 边界

正式计数仍为 40/68，本轮 formal pass delta 为 0。未使用真实账户、凭据、交易所 provider 或部署；provider 全是合成 fixture。R8 两个 warning 是 backtrader Quandl deprecation 与 Windows PyZMQ Proactor selector fallback。Root 可按 manifest 精确部分提交 SDK validator hunk、AFI 测试与本目录三件 canonical evidence；不得整文件提交 SDK `service_components.py`，也不得据此宣称 AFI05/06 通过。后续仍需 AFI 预算控制面认证 HTTP/UI、Risk owner authority 产品接线，并由真实 gate binding 读回确认代次后才可核对 `consumer_effective`。

Raw archive：`raw-evidence.zip`，875884 bytes / SHA-256 `64c63e89dc88114a4357e0ea4e70c88cee56fd4abc797c097cbbacc157be2290`。逐个归档成员的 bytes/SHA-256 见 `manifest.json`。
