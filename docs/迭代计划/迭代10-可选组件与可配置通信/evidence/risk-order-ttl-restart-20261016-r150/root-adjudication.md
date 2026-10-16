# Root adjudication — RISK02 permit TTL / intent deadline / UNKNOWN restart（第150轮）

## 裁定

- 接受 `it10riskttl-root-20261016-r5`（spec `iteration10-risk-ttl-root-spec-r5.json`，sha256 `ccdf572ee326cf6bd9201eeba53816848fc28defb3828a75c28ca73997b1f686`，2094 pins）作为本轮 RISK02 固定来源验收证据：exit 0、1 passed in 20.26s、stderr 为空、2094 source pins 前后不变、heads 不变。
- 提交范围（父 `833b101c`）：`bt_api_py/service_risk_order_entry.py`（accepted bytes `89b2eade…`，与固定候选同字节）、`bt_api_py/service_risk_order_journal.py`（accepted-only partial blob `f10c2e89…` = HEAD + 仅 `RESERVED→ADMISSION_DENIED` 过渡；checkout 中 DIM01 改动块不纳入）、`tests/unit/test_service_risk_order_ttl_restart_integration.py`（`466e42e8…`，新增固定来源测试）。

## 覆盖与语义

- 场景1：RESERVED 后在 dispatch claim 前 permit TTL 过期——permit 时钟第 9 次读取触发 LIVE_GROSS 终检清扫，命令落 `ADMISSION_DENIED`(PERMIT_NOT_ACTIVE)；`gate.release` 因 permit 未激活且未认领返回 PERMIT_NOT_ACTIVE，entry 据此确信安全并记账拒绝；证明零 provider 写、零增额、无 claim/dispatch 事件。
- 场景2：parse 通过后、claim 前绝对 intent deadline 到期——deadline 持久源为 `canonical_payload`（journal PREPARED 行即持久化）；第二次时钟读取到期即拒绝（DEADLINE_EXPIRED），无 validate/claim；provider wire payload（`order_request`）保持不含 deadline，派发路径 `OrderRequest.from_dict` 语义不变。
- 场景3：provider 已写、返回不确定——UNKNOWN + USD150 冻结；TTL 过期后重启保持 UNKNOWN 与冻结，不再次派发。
- journal 过渡修复理由：HEAD 的过渡表不允许 `RESERVED→ADMISSION_DENIED`，但 HEAD entry 在 reserve 成功后任何 RiskAdmissionDeniedError 都会写 ADMISSION_DENIED——潜在缺陷会令 handler 崩溃并向客户端误报 UNKNOWN；本轮修复该过渡并纳入验收。

## 失败史与不可复用性

- r1（fixture 精确类型判定失败）、r2（fixture poll 默认永阻塞致注册失败）、r3（journal 状态冲突）、r4（deadline 源误读恒为空）输出目录均原样保留于 `D:/temp/it10riskttl-root-20261016-r{1,2,3,4}` 与候选 `it10riskttl-candidate-r{2..6}`；本目录仅收录 r5 及其固定来源，不重复历史运行。

## 边界

- 证据等级 `LOCAL_SPAWNED_SERVICEHOST_CURVE_SQLITE_SYNTHETIC_PROVIDER_PERMIT_TTL_INTENT_DEADLINE_UNKNOWN_RESTART_NOT_REAL_ACCOUNT`：本机真实 ServiceHost 子进程/Spine 组件 + 合成 provider/账户，非真实账户、无外部 venue。
- RISK02 正式条款仍部分（真实账户/外部链路未验收），正式累计维持 25/68，不因本轮更新。
