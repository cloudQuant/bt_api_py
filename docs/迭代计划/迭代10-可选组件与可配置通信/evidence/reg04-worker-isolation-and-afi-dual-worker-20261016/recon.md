# REG04 acceptance mapping (read-only recon, 2026-10-16)

Scope: map clause 正式迭代计划.md line 747 (section 5.6 lines 416–538) to concrete increments.
Status source: 迭代10验收状态汇总.md line 57 = 未开始 ("AFI 分析 worker 归属与越权预留未实现；第39轮只读HTTP线程不是该分析用例").
This file is the only write. No repo file, commit, or test was touched.

## 0. Clause decomposition

REG04 = "AFI 多 worker 不占策略单活名额；应用/service actor 与策略/人工 book 业务归属分离，Monitor 转交不扩权，服务间越权预留拒绝".
Evidence type required: "实际代理/服务，动作和 scope 正反例；策略/人工 book/旧 ID 映射不产生双份额度".
Required positive combo (line 538): "AFI 双 worker + 人工 book" must be registered and verified as real behavior, not YAML-only.

## 1. Sub-requirement table

### (a) AFI multi-worker does not consume strategy single-active-run slots (DUPLICATE_ACTIVE_RUN only for strategy runs)

Machinery today
- Write-claim creation is gated to `WireRole.STRATEGY_CLIENT` only: `bt_api_py/service_registry.py:1875-1882`; claim key = (execution_domain_ref, strategy_ref, account_ref), run mismatch or live `active_registration_id` → `DUPLICATE_ACTIVE_RUN`: `service_registry.py:1930-1975`.
- Dispatch-time recheck (strategy writes only) and separate service group-write run check: `service_registry.py:2137-2159`. Group coordinator single-active is a *separate* constraint via `TrustedGroupWritePolicy`/run binding (5.6 line 443 requirement), `service_registry.py:270-299`, `tests/unit/test_service_group_registration_lifecycle.py:202-360`.
- Per-worker session: `ServiceClientRuntime.client_session_id = client_session_id or uuid4` (`bt_api_py/service_runtime.py:319-337`); registration_key = sha256(client_session_id + binding digest + port) (`service_runtime.py:1238-1240`), so N workers of one principal coexist without CONFLICT.
- AFI side: one runtime per subject/worker (`D:\source_code\ai-for-investor\src\backend\app\services\service_read\deployment.py:264-271`, no explicit client_session_id → uuid per worker), per-subject fixed owner thread (`worker.py:1-135`).

Evidence today
- `tests/unit/test_service_registry.py:771-907` `test_app_workers_do_not_consume_strategy_writer_claims`: two APPLICATION_CLIENT workers (distinct client_session_id) register OK with manual_book scope; strategy client registers separately; application impersonating a strategy write → SCOPE_DENIED; account-only write → SCOPE_DENIED. Committed by `e7fdefa6` (early round; NOT REG04-level, no real AFI/strategy processes).
- Round 39 (progress §39): two JWT subjects → two independent owner-thread SDK workers against real CURVE Execution Host — read-only, explicitly not this analysis case (status file line 57).
- Round 151: two AFI app processes, approved-target path only (`evidence/afi-approved-target-cross-process-20261016/root-adjudication.md`).

Gap
No proxy/service-level test where >=2 application workers and a real strategy run are live on the SAME account owner at once, proving: workers never trigger/consume strategy claims; strategy run keeps ITS single-active claim; a second strategy run is still rejected while workers continue. AFI workers today never hold a strategy write, so the positive direction is structurally guaranteed but unasserted.

### (b) application/service actor vs strategy/manual-book attribution separation; no new quota from transient UI session / user name / AFI worker ID

Machinery today
- Identity vs business-attribution split: `WireIdentity(role, principal_ref, actor_ref, delegate_ref)` vs `WireScope(strategy_ref / manual_book_ref / portfolio_ref)` in `bt_api/bt_api_base` `service_protocol.py`; registry persists both per session (`service_registry.py:1884-1918`) and rechecks each call (`2097-2131`).
- Write admission requires a business owner: strategy+run for strategy role; else account-only writes (no manual_book/portfolio) → `SCOPE_DENIED` (`service_registry.py:1737-1770`); application with `strategy_ref` write denied without service group policy (`1743-1763`).
- Manual book is an exact trusted resource (scope equality incl. book + domain): `service_registry.py:1702-1717`, `2245-2320`.
- AFI: manual/approved bindings come only from the trusted directory (`deployment.py:696-708, 945-965`), session re-validates scope book/account (`session.py:256, 346-377`); durable operation key binds subject/principal/actor/delegate/scope fingerprint (`app/models/service_order_operation.py:47`, `service_order_operations.py:169-176`).

Evidence today
- `tests/unit/test_service_registry.py:974-1051`: other manual book and paper→live domain reuse → SCOPE_DENIED.
- Rounds 124/129/149/151: real AFI HTTP→SDK→Execution Host; client-supplied actor/approval rejected (422), cross-subject 403, journal identity assertions incl. manual book (round 129).

Gap
No test that two different worker IDs / sessions / user names of the same principal resolve to the SAME stable manual_book attribution (i.e. cannot mint a new book/quota). Also the "额度" (quota-slot) side is asserted only indirectly (E/R attribution + UNALLOCATED residuals, EXE03 accepted) — no manual-book-specific budget no-double-count assertion exists.

### (c) Monitor 转交不扩权 (handoff keeps original actor/scope)

Machinery today
- Control role exposes only `monitor.control`; owner claim/ack are in-process, never public: `bt_api_py/service_components.py:469-478`, `tests/unit/test_service_monitor_control_wiring.py:202-219`.
- Handoff targets are fixed at construction: caller allowlist keyed by FULL authenticated APPLICATION_CLIENT identity (delegate mandatory), each binding fixes issuer/account/mode/scope/actions; owner identity is SERVICE and cannot be a caller: `bt_api/bt_api_monitor/src/bt_api_monitor/service_control.py:83-135, 199-231`.
- Owner calls run under the owner's exact identity+scope; caller scope selects the route (`bt_api_py/service_monitor_control.py:323-330, 387-390`); signed command carries the original issuer (`control.py:277-320`).
- Query vs control authorized separately through registry grants (`service_runtime.py:95`; wiring tests 222-333).

Evidence today
- MON02 PASS (rounds 58/60): real spawn/CURVE chain, HMAC v2 signed control bound to exact RESUME cause, v1 rejected, 8 pre-auth denial classes → SCOPE_DENIED, delayed/duplicate control has no effect.

Gap
No REG04-specific positive/negative through actual proxy/service: a control-capable application client (e.g. AFI operator) submitting for another route/account/scope is rejected, and the original issuer/actor is preserved in the ledger. Machinery appears complete; recorder did not count MON02 toward REG04.

### (d) Inter-service unauthorized reservation rejection

Machinery today
- `upstream.account_reservations` exists ONLY in the service-role mount set (`bt_api_py/service_config.py:53-70, 596`): application clients cannot even declare it; group_reservations role fixed-port matrix (`service_config.py:186-196`); YAML authorization-bypass fields rejected (`tests/unit/test_service_group_role_config.py:103-147`).
- Registry: `TrustedGroupReservationPolicy` exact operator policy (instance/principal/identity/scope/run/account, scalar account only): `service_registry.py:308-359`; register-time rejection without policy `1718-1735`; dispatch-time recheck `2245-2273`. `service.group_reservations` stays internal for the risk component (`service_components.py:402-414`); Host guard `service_host.py:1194`.

Evidence today
- `tests/unit/test_service_group_registration_lifecycle.py:517-718`: same principal, wrong account / multi-account scope / query-only seeding → SCOPE_DENIED / group policy error. Unit level.
- Round 121: real 3-Host/CURVE positive group chain (policy present). Round 105: package-level over-privilege zero-side-effect.

Gap
No proxy/service-level negative: an analysis/other registered service (or principal) requests reserve on `upstream.account_reservations`/`service.group_reservations` WITHOUT grant → SCOPE_DENIED at register and at call, handler/provider count zero.

### (e) Strategy / manual book / old-ID mapping must not produce double quota

Machinery today
- Attribution keys: strategy claims + manual-book exact resource (above); E account-book projection keeps UNALLOCATED residuals separate from strategy/manual owners (`bt_api_py/service_execution_account_books.py:703-706, 1242`); EXE03 accepted (rounds 67/76) strategy/manual/account projections with residual reconciliation.

Evidence today
- Registry/unit negatives (a)(b) above; EXE03 accepted; ARB02 rounds 144/152 budget-hold semantics (group budget, not manual book).

Gap
- "旧 strategy_id → 新 strategy_ref/book/账户键显式迁移映射" (plan line 426, line 854) DOES NOT EXIST in the SDK (grep for migration/alias mapping in `bt_api_py/` finds nothing service-related; AFI hits are unrelated AI-research fields). MIG01/MIG03 remain 未开始. Without it the mapping half of (e) cannot be tested; it needs implementation (SDK service directory + conflict-checked persistence) or an explicit recorded scope decision.
- No test that the same manual book / same strategy ref reused by two workers or replays double-counts nothing (partly implied by idempotency evidence but not asserted as quota semantics).

## 2. Closeable by NEW test vs requires implementation

New test on existing machinery (no production change):
- (c) Monitor handoff no-escalation positive/negative (wiring + monitor control + registry grants all exist).
- (d) Unauthorized reservation negative (config gating + TrustedGroupReservationPolicy + internal-port rules all exist; round-121 host chain as base).
- (a) component/host-level combination worker(s) + strategy claim (registry + SDK runtime exist; a strategy write client can be exercised via SDK `strategy_client` role without new code).
- (b) negative half (unknown/other book, account-only write, forged actor) at AFI/proxy level on existing machinery.

Requires implementation:
- (e) old-ID explicit migration mapping (new module in SDK service directory area; overlaps MIG01/MIG03 scope; not started).
- (a) AFI-side realization with real AFI workers + real strategy submission on one owner IN THE AFI REPO — depends on the in-flight AFI07 remainder work (see §3), not on new SDK production code.
- (b) manual-book quota no-double-count assertion still needs a decisions/test fixture binding manual book to an E/R budget slot (E-side machinery exists; may be test-only).

## 3. Minimal ordered increments (avoiding AFI07 duplication)

Overlap stated explicitly: `.codex-tmp/afi07-remainder-status.md` shows a live successor agent is adding AFI07 r9 nodes in the AFI repo that will (i) submit a strategy order through the same Execution account-owner via SDK `strategy_client`, (ii) prove ordinary ops (manual/approved-target/strategy) do not touch group legs, (iii) dispose a group. Item (i) is exactly the strategy-side counterpart REG04(a) needs, in the same fixture (two AFI app processes + manual book already exist from rounds 129/151). Therefore REG04's realization is largely a SECOND-CONSUMER test of AFI07's machinery: do not re-implement strategy submission or group-leg isolation; schedule REG04's AFI increment AFTER AFI07 r9 lands, reusing its fixture, and ask the successor (or a follow-up round) to add only the REG04-specific assertions.

Ordered plan:
1. INCR-1 (SDK repo, test-only, one round): host-level test — 2 application workers (distinct client_session_id, manual book scope) + 1 strategy run on the same account owner: workers and strategy coexist; strategy claim held; second strategy run → DUPLICATE_ACTIVE_RUN; workers unaffected; account-only write and unknown book → SCOPE_DENIED; a service registering `service.group_reservations` without `TrustedGroupReservationPolicy` → SCOPE_DENIED with zero handler calls; monitor-control wrong-scope/wrong-actor handoff rejected. Closes (c), (d), (a)-machinery, (b)-negatives at proxy/service level. No production change expected; if any gap appears (e.g. monitor handoff of an actor), report rather than patch mid-round.
2. INCR-2 (AFI repo, follow-up acceptance round reusing AFI07 r9 fixture, after AFI07 r9): two AFI workers (2 subjects / 2 app processes) + manual book + strategy run on one owner: no DUPLICATE_ACTIVE_RUN for workers, strategy single-active intact, exactly-once dispatch, actor/delegate attribution per intent, manual book stable across workers (no second book/quota). Closes (a) AFI half + (b) with real AFI HTTP; the only new code should be test nodes.
3. INCR-3 (implementation decision, before claiming REG04 PASS): old-ID mapping — either implement the explicit conflict-checked migration mapping (SDK service directory; align with MIG01/MIG03, plan lines 426/854) or obtain an explicit recorded scope decision deferring that half to MIG03 while REG04 rests on rename-cannot-mint-a-slot negatives. REG04 cannot be declared PASS with the mapping clause untested unless so recorded.
4. Acceptance round: fixed-source QA (standard runner, one-shot), real proxy/service positive+negative matrix incl. the required "AFI 双 worker + 人工 book" combo (plan line 538), evidence bundle + root adjudication, only then update 迭代10验收状态汇总 line 57 and the progress doc.

## 4. Key file references

- SDK: `bt_api_py/service_registry.py`, `bt_api_py/service_runtime.py`, `bt_api_py/service_config.py`, `bt_api_py/service_components.py`, `bt_api_py/service_monitor_control.py`, `bt_api_py/service_host.py`, `bt_api_py/service_group_reservations.py`, `bt_api_py/service_execution_account_books.py`.
- Subpackages: `bt_api/bt_api_base` (`service_protocol.py`), `bt_api/bt_api_monitor/src/bt_api_monitor/service_control.py` + `control.py`.
- Tests: `tests/unit/test_service_registry.py` (771, 974), `tests/unit/test_service_group_registration_lifecycle.py` (202, 517), `tests/unit/test_service_group_role_config.py`, `tests/unit/test_service_monitor_control_wiring.py`, `tests/unit/test_service_monitor_control_integration.py`.
- AFI repo `D:\source_code\ai-for-investor` (head 34f59117): `app/services/service_read/{deployment,session,worker}.py`, `app/services/service_order_operations.py`, `app/models/service_order_operation.py`, `tests/test_service_read_authorized_http_integration.py`.
- Evidence: `evidence/afi-approved-target-cross-process-20261016/root-adjudication.md` (round 151), rounds 39/121/124/129/144/152 in 开发与验收进度.md.
