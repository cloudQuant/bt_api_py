# REG04 scope-decision memo — independent adversarial audit (read-only, 2026-10-16)

Auditor: independent. No repo file modified, no python executed; all facts below from file
reads, `git show/--stat/diff` and `sha256sum`.

Object under audit:
`D:\bt_api_py\docs\迭代计划\迭代10-可选组件与可配置通信\evidence\reg04-worker-isolation-and-afi-dual-worker-20261016\scope-decision-old-id-mapping.md` ("memo", 54 lines).

Corroborating archive in the same folder: `root-adjudication.md`, `review-incr1-status.md`,
`review-incr2-status.md`, `recon.md`. Recon sanctioning the path: `D:\bt_api_py\.codex-tmp\reg04-recon.md`
(§1(e), §3 INCR-3). Ledger to be updated: `迭代10验收状态汇总.md:57` (currently "部分…28/68 不变").

Repos/branches at audit time: bt_api_py `dev` head c8fef96b; ai-for-investor `dev` head e9c7ca1d.

---

## Q1 — Does the memo's quote/citation of the plan match the actual file text?

Actual text re-read at `正式迭代计划.md` :426, :538, :747, :784, :854, :865.

| Memo quote | Plan location | Verdict |
|---|---|---|
| 「SDK 目录负责旧 strategy_id 到新 strategy_ref/book/账户键的显式迁移映射，不能改名后生成第二份订单分区」 | :426 (second sentence) | Verbatim (final period omitted). MATCH |
| 「旧 strategy_id、裸 symbol/order_id、进程内 run 与账户配置 → 建立有冲突检查的持久映射及历史关联；无法判定归属保留未分配/待对账，不改名后重新授信或重新下单」 | :854 (table row; `→` renders the cell separator) | Verbatim across the two cells. MATCH |
| 「按第 11.3 节冻结旧客户端、确认旧 writer 失效、迁移订单/归属/预留/幂等/游标、建立新 epoch 并重登记……首次新写之后无已验收反向迁移则拒绝回旧版」 | :784 (requirement cell + evidence cell) | Quoted substrings verbatim, but the bare `……` elides the text between the two cells: `；导入失败/切换前后回退明确 \| 受控迁移和调用计数；无双 owner、无删除 UNKNOWN，`. Faithful in content; the cell boundary is not marked. MINOR CITATION HYGIENE |
| Fragment table (a)–(e) (memo :43–49) | :747 clause + evidence cell | (a),(c),(d),(e) verbatim; **(b) is written「应用/service actor 与策略/人工 book 归属分离」and drops「业务」** from「业务归属分离」. Trivial paraphrase, not a content change. |

Line-number citations (:426, :854, :784) are correct. The memo does not itself quote :747
line numbers, but its fragment decomposition matches the clause except the (b) wording above.
The recon-required combo sentence「AFI 双 worker + 人工 book」(plan :538, "必须登记…并由 P1/P2 验证，不能只证明 YAML 能解析") was also confirmed present.

No material mismatch found. Nothing quoted exists only in paraphrase that changes meaning.

---

## Q2 — Is the split defensible AGAINST THE PLAN TEXT? (my own reading)

Plan text, independently read:
- :747 REG04 evidence cell: 「实际代理/服务，动作和 scope 正反例；策略/人工 book/旧 ID 映射不产生双份额度」.
- :426 (inside §5.6, the section REG04 annotates): 「SDK 目录负责旧 strategy_id 到新 strategy_ref/book/账户键的显式迁移映射，不能改名后生成第二份订单分区」.
- §11.3 :854 (migration contract table, "旧输入或入口 → 目标发行版行为"): 「旧 strategy_id、裸 symbol/order_id、进程内 run 与账户配置 → 建立有冲突检查的持久映射及历史关联…」.
- :784 MIG03: 「…迁移订单/归属/预留/幂等/游标、建立新 epoch 并重登记…首次新写之后无已验收反向迁移则拒绝回旧版」.

My reading: the evidence cell names "旧 ID 映射不产生双份额度" as one of three attribution regimes whose
no-double-quota property must be evidenced by REG04. It does not say "REG04 must build the mapping".
The plan's own division of labour puts the **construction** of the conflict-checked persistent mapping in the
migration line (§11.3 :854 row, MIG03 :784) and the **SDK directory responsibility** in :426. Therefore
deferring the mapping's construction to MIG03 is legitimate under the plan. What is NOT self-evidently
legitimate is deferring the *evidence* — the cell is REG04's. Strictly literal reading: until either the
mapping exists with its conflict check or an explicit recorded scope decision defers that half, REG04's
evidence cell is not fully exercised. That is exactly the conditional the recon recorded
(`REG04 cannot be declared PASS with the mapping clause untested unless so recorded`, recon.md :5–6),
and the memo is the recording, taking the recon's INCR-3 path 2.

Falsification attempts against the memo's premise "负向由第 3 节闸门承担" (memo :49):
- Today no old-ID mapping exists (recon §1(e); grep-level claim not re-run here, but confirmed by the absence of any module in the commit archives and by the memo/reviews stating it).
- A *renamed* strategy_id is a **different** scope key `(execution_domain_ref, strategy_ref, account_ref)`; the accepted negatives reject a second run on the **same** ref (DUPLICATE_ACTIVE_RUN) and deny foreign/account-only scopes — none of them exercises a renamed ref. So the rename→second-partition path is **not** directly blocked or tested by the accepted negatives.
- The MIG03 cutover gate (below) closes the *rollback* path after the first new-epoch write, not the rename path.
- Therefore the memo's negative承接 is real for same-ref/same-principal duplication but overstates reach for the rename scenario; the memo, however, explicitly discloses this in its boundary section (:31–33).

Conclusion Q2: the split is defensible **only** as a recorded scope decision with disclosure — which
is what the memo is — and not on a strict literal reading of the evidence cell alone. PASS may be
declared on the remaining fragments provided the disclosure text in the Verdict below is carried.

---

## Q3 — Verification of each memo §3 claim

### (i) MIG03 increment A (`f0731ec2`) really refuses post-first-write reverse migration, fail-closed

- `git show --stat f0731ec2`: `bt_api_py/service_migration_gate.py` +443, `bt_api_py/service_host.py` +24 (exactly as memo states), `tests/unit/test_service_migration_gate.py` +438, evidence dir `evidence/mig03a-first-write-cutover-gate-20261016/` (dir name confirmed on disk and in commit tree).
- Module public semantics (read from commit tree): marker lifecycle COMMITTED → SEALED via `record_migration_first_write` (one-way, idempotent; `enforce_migration_start` :199–227 returns for no marker/COMMITTED, and on SEALED allows only `destination_epoch` or an explicitly accepted reverse epoch, else raises `MigrationGateError(REVERSE_MIGRATION_REFUSED)`); `rollback_migration_cutover` :230–258 removes the marker pre-seal, and post-seal refuses unless `accept_reverse_migration` recorded an accepted epoch; `accept_reverse_migration` is an operator decision "absent by default".
- Host wiring (`git diff f0731ec2^ f0731ec2 -- bt_api_py/service_host.py`): `_enforce_migration_gate()` is invoked in `ServiceHost.start()` **before** authority/binding validation and before any start side effect (registry open would write a new epoch/release claims), converting the gate error to `ServiceHostError(exc.code)` — refusal of the old deployment, i.e. fail-closed at the host boundary.
- Gate tests in the same commit include `test_first_business_write_seals_and_old_epoch_start_is_refused`, `test_accepted_reverse_migration_is_required_to_restore_old_epoch`, `test_corrupt_or_foreign_marker_fails_closed`, `test_service_host_start_refuses_sealed_old_epoch_with_specific_code`. The docstring's quote of plan **:865** ("首次新派发、预算/归属等状态写入后…没有该方案则止增并向前修复") was verified verbatim against the plan.
- Ledger row `迭代10验收状态汇总.md:81` confirms incr-A landed at round 178 and **MIG03 remains 部分** (so the memo's "该义务不影响 MIG03 当前「部分」状态" is consistent; this is incr-A only, not full MIG03).

Claim (i): VERIFIED.

### (ii) INCR-1 (SDK, `tests/unit/test_service_reg04_worker_isolation.py`, 4 nodes; commit 6448381f; hash matches)

- File hash re-computed: `7c749a68…910861` == root-adjudication/review expected. Git stat of 6448381f includes this file (+1845) and the whole evidence archive (133 files). Branch dev.
- Node 1 `test_spawned_execution_owner_isolates_application_workers_from_strategy_claim` (:577–796): real spawned ServiceHost CURVE owner + two `ServiceClientRuntime` workers with distinct `client_session_id` on one account owner + strategy run. Asserts: workers register with exact manual-book scope (:640–641), strategy holds the exact claim (:673–676), **second strategy run → `ServiceRuntimeError.code == "DUPLICATE_ACTIVE_RUN"`** with claim undisturbed (:687–694), raw authenticated probes: **account-only write → SCOPE_DENIED** (:718–719), **foreign book → SCOPE_DENIED** (:730–731), exact book accepted (:742), renewals of both workers and strategy stay green (:746–753), durable sqlite read: exactly one claim row `(("run-a", None))` — workers minted none (:779).
- Node 2 `test_host_start_refuses_group_reservations_without_trusted_reservation_policy` (:943–1003): real `ServiceHost` start without `TrustedGroupReservationPolicy` → `ServiceHostError GROUP_RESERVATION_POLICY_REQUIRED`, state failed, no endpoint (:971–977); contrast start with the policy passes the policy gate and fails later at bundle validation, attributing the refusal to the missing policy alone (:994–1003). Note: the component resource provider is invoked once for resolution (:974) — that is not a request handler.
- Node 3 `test_reservation_route_without_trusted_policy_is_denied_with_zero_handler_calls` (:1083–1185): register without trusted policy → `SCOPE_DENIED` and **handler calls `[]`** (:1105–1107); with policy: dispatch before trusted run binding → `DUPLICATE_ACTIVE_RUN`, `[]` (:1130–1131); wrong account scope → `SCOPE_DENIED`, `[]` (:1156–1157); forged actor on same principal → `DENIED`, `[]` (:1165–1166); unknown registration → `NOT_REGISTERED`, `[]` (:1175–1176); valid dispatch → `ok`, calls exactly `["reg04-group-a"]` (:1181–1183). Level: registry `handle()` in-process with real handlers + durable DB (not ZMQ wire; the wire-level group positive is round 121 per recon §1(d)).
- Node 4 `test_spawned_monitor_control_rejects_wrong_scope_route_and_actor` (:1663–1845): spawned monitor-control service host; wrong wire scope (other account) → `SCOPE_DENIED` (:1747–1748); internal owner op `monitor.claim` over the wire → `CAPABILITY_UNAVAILABLE` (:1757–1758); signed cross-scope and cross-account commands → `SCOPE_DENIED` via runtime (:1766–1768); fully authenticated intruder with an **identical ResourceGrant** (deliberately wrong-actor setup :1514–1528) → `SCOPE_DENIED` (:1781–1787); freeze counter is 0 across all rejections and 1 after the one legitimate freeze (:1790, :1820); rejected command ids have **no ledger row**, the accepted one is `("acknowledged", executor, "applied", cause)` (:1823–1830).
- Skips/xfails/marks: case-insensitive `skip|xfail` count = 0; `pytest.mark` = 0 in this file.

Claim (ii): VERIFIED (with the noted level nuances: node 2/3 host/registry-level; node 4 negative-heavy).

### (iii) INCR-2 (AFI `src/backend/tests/test_service_read_reg04_workers.py`, 2 nodes; commit e9c7ca1d)

- `git log --oneline -2 -- <file>` → single commit `e9c7ca1d` ("test(service-orders): prove dual-worker isolation beside the strategy claim"), which `git show --stat` confirms contains the file (+1297). Hash re-computed: `e8c35811…f87277a` == archive record. File lives in AFI dev; the test spawns real uvicorn+production-lifespan app processes (:210–213 docstring) via the AFI07 fixture.
- Node 1 (`:995` → helper `_run_reg04_dual_worker_one_owner` :628–927): two app processes, distinct pids ≠ parent (:675–687); 4 application sessions + later 1 strategy session; **claims == () before the strategy starts** (:698) and **second strategy run → DUPLICATE_ACTIVE_RUN** (:729–731) with claims/sessions tuples unchanged and no ghost session (:736–741); workers keep operating under the claim and after rejection (:722–744); exactly-once dispatch: 8 expected unique intents, one dispatch each, **8 unique reservations**, no replay re-reserve (:796–805, `_reg04_assert_owner_close_facts` :531–555); durable manual rows: 5 keys, all with `manual_book_ref == _MANUAL_BOOK_A`, `account_ref` A, distinct intents, asserted per-row owner/principal/actor/delegate/scope (:558–581, :854–863) — i.e. cross-session / cross-worker / two-subject convergence to ONE manual book; approved row attributed to subject A + strategy_ref A (:584–600); journal ACKED per intent and both group-leg intent ids absent (:603–625); group budget single `RESERVED`, legs untouched (:782–789).
- Node 2 (`:1292` → helper `_run_reg04_late_worker_under_claim` :1003–1268): fresh app process spawned **after** the strategy run holds the claim; registration under claim adds sessions to 3 without touching claims (:1074–1090); worker manual+approved operations succeed under claim (:1092); second strategy run refused again (:1095–1104); same-key manual replay after rejection returns the same intent and provider id (:1106–1116); exactly-once owner close facts including the strategy intents (:1157–1205); durable rows/journal re-asserted (:1209–1228).
- Skips/xfails/marks: case-insensitive `skip|xfail` count = 0; `pytest.mark` = 0.
- Independent review `review-incr2-status.md` corroborates real processes, pin recompute 2148/2148, and discloses the quota-projection gap (below).

Claim (iii): VERIFIED.

### (iv) EXE03 UNALLOCATED residual separation accepted

- Round 67 (`开发与验收进度.md:2099–2116`) built the account-book residual component: "累计部分成交…按strategy/manual book归属；未解释事实保留UNALLOCATED和残差" — and explicitly **withheld** EXE03 PASS ("不推定EXE03正式PASS").
- Round 76 (`:2285–2317`) accepted EXE03 formally: "manual为0.75，UNALLOCATED为-0.50"; "人工外部订单/成交只进入对应book，未知外部归属不分摊到策略"; plan-clause :757 anchor; "**EXE03正式PASS**".
- Nuance: the memo's phrasing "EXE03（轮 67/76 已接受）" is loose — acceptance is round **76**; round 67 is the component round that expressly did not accept. Substance of the claim is correct and the underlying property (UNALLOCATED separate from strategy/manual owners) is real.

Claim (iv): VERIFIED (wording imprecision noted).

---

## Q4 — UNPROVEN FRAGMENTS (REG04 clause :747 + evidence cell, re-read independently)

Coverage table:

| Fragment | Accepted evidence | Status |
|---|---|---|
| AFI 多 worker 不占策略单活名额 | INCR-2 N1/N2 (real dual app processes + strategy run same owner; claims == () with 4 app sessions; DUPLICATE_ACTIVE_RUN for 2nd strategy run); INCR-1 node 1 (host level) | Covered |
| 应用/service actor 与策略/人工 book 业务归属分离 | INCR-1 node 1 raw probes (account-only / foreign book → SCOPE_DENIED); INCR-2 durable per-intent attribution rows + single manual book across 4 sessions/2 subjects | Covered |
| Monitor 转交不扩权 | INCR-1 node 4 (wrong scope/route/actor rejected; internal ops unavailable; counter 0/1; ledger rows) | Covered (negative side strong; positive "issuer preserved" only implied) |
| 服务间越权预留拒绝 | INCR-1 node 2 (host start refusal) + node 3 (register + dispatch denials, zero handler calls) | Covered at host/registry level |
| 实际代理/服务, 动作和 scope 正反例 | Spawned ServiceHost CURVE (INCR-1), two real uvicorn app processes (INCR-2); positives and negatives both present in nodes 1/3/4 and both AFI nodes | Covered |
| 策略 不产生双份额度 | same-ref 2nd run rejected (both files); 8 dispatches / 8 unique reservations, replay never re-reserves | Covered |
| 人工 book 不产生双份额度 | single book A across all durable rows/sessions/users; same-key replay+concurrent pair → 1 row; reservation exactly-once | Covered (quota side indirect — see fragment 3 below) |
| 旧 ID 映射 不产生双份额度 | none direct; MIG03 incr-A gate (rollback refusal) + memo scope decision | **Deferred / disclosed** |

Unproven fragments other than the old-ID mapping half:

1. **Rename-specific double-partition negative**: no node exercises a renamed strategy_id at all (memo §3.4 discloses this; review-incr1 §8 and review-incr2 §7 also record it). This is arguably part of the old-ID mapping half rather than a separate fragment, but it should be named explicitly: the accepted negatives cover same-ref and same-principal duplication, NOT the rename-different-ref path, which remains unguarded until MIG03 builds the mapping.
2. **Manual-book "额度" (quota-slot) no-double-count on a mounted E/R budget projection**: the owner fixture does not mount the account-book E/R residual projection; the assertion is the strongest available machinery (one reservation per accepted op, single manual book scope, journal/provider exactly-once, EXE03 residuals). Disclosed in root-adjudication disclosure 2 and judged adequate in review-incr2 §7. Not hidden, but it is indirect evidence for the word "额度".
3. **Monitor handoff positive preservation of the original issuer/actor**: covered negatively (wrong actor/route/scope all refused, internal ops unavailable) and the legitimate command executes under the owner identity; the durable ledger row asserts owner executor/outcome, not the original issuer string. Minor; the negative (no escalation) is what the clause names.
4. **Adjacent, not in the clause**: plan :538 requires the P0 sample set to *register* "AFI 双 worker + 人工 book" (and P1/P2 to verify real behavior). Real behavior is verified by INCR-2; the registration bookkeeping is not evidenced in the audited memo/archive. The acceptance round should confirm it (or list it as still pending); it does not by itself block REG04.

Materiality: fragment 1/2 above are the only residual weak points; both are explicitly disclosed in committed documents and neither contradicts any tested behavior. They require disclosure, not a NO-GO.

---

## Q5 — Honesty assessment of the memo

- **Disclosure of the missing rename test**: YES, explicit and un-euphemised (:31–33): "本仓无「旧 strategy_id 改名后订单分区/额度」的直接节点；该问题恰是 MIG03 映射（含冲突检查）落地时必须解决的". The memo does not bury it inside a success claim.
- **MIG03 follow-up obligation**: the obligation section (:35–39) binds, verbatim, "当 MIG03 落地持久映射（或其验收轮）时，其测试必须包含负向断言：旧 ID→新 ref/book/账户键 的映射/未映射行为均不产生第二份订单分区或双份额度；无法判定归属时保留未分配/待对账（§11.3 :854）". This is an adequate binding of the future mapping tests to the REG04 negative, matching `收留未分配/待对账` of :854. Caveat: it binds only inside the REG04 evidence folder; for full effect it should be mirrored into the MIG03 line's records (e.g. the MIG03 row of the acceptance summary or MIG03 evidence), and the memo itself is currently **untracked** (`git status` = `?? … scope-decision-old-id-mapping.md`), so it must be committed with the acceptance round for the PASS to rest on a durable record.
- **Overclaiming flags (all minor, none hide a falsifier)**:
  1. "负向由第 3 节闸门承担" (:49) reads more broadly than the evidence supports for the rename path — the gates cover same-ref duplication and post-first-write rollback refusal. Mitigated by the explicit boundary disclosure in the same memo.
  2. Fragment (b) drops「业务」(:46 vs plan :747) — paraphrase hygiene only.
  3. "EXE03（轮 67/76 已接受）" (:30) — acceptance was round 76 (round 67 materially contributed and withheld PASS).
  4. The memo states the four fragments + book half were covered by "作者轮 + 两轮独立复核 GO" — consistent with root-adjudication (both increments GO and committed; REG04 held partial solely for the mapping half), not an inflation.
- The memo's own precondition claim ("REG04 满足 PASS 前置", :53) is precisely the recon's INCR-3 path-2 condition; no stronger claim is made (it does not itself declare PASS or update the ledger).

---

## VERDICT: GO-with-disclosures

Disclosure text to carry into the adjudication and the acceptance ledger:

> REG04 PASS 认证范围披露（随本条 PASS 记入裁定与证据汇编）：
> 1. 本条 PASS 不认证「旧 strategy_id → 新 strategy_ref/book/账户键」显式迁移映射本身，也不直接证明「改名不产生第二订单分区/双份额度」：映射的构建与冲突检查负向验证归属 MIG03 线（计划 :426/:854/:784；scope-decision-old-id-mapping.md）。在 MIG03 落地前，改名不同 ref 的路径无直接测试；现有保障仅为精确 scope 键（同 ref 第二 run → DUPLICATE_ACTIVE_RUN）与 MIG03 incr-A 的首写后拒绝回退闸门（f0731ec2，fail-closed）。
> 2. 人工 book「额度」无双计的最强可得证据为：每单恰一次派发/预留、跨 4 会话/2 主体收敛单一人 book、E/R 侧以 EXE03 已接受的 UNALLOCATED 残差分离佐证；owner fixture 未挂载 E/R 预算投影（root-adjudication 披露 2、review-incr2 §7 判 adequate）。
> 3. 本 scope 决定文件须随验收轮提交（当前 untracked），且其 MIG03 后续义务（映射测试必须包含不产生第二份订单分区/双份额度的负向断言，无法判定归属保留未分配/待对账）应同步记入 MIG03 线记录；MIG03 本身仍为 部分。
> 4. 附记：引用细节——REG04 片段(b)省略「业务」；:784 引文以「……」跨两表格单元；「轮 67/76 已接受」中正式接受为轮 76（轮 67 为组件轮、明确未 PASS）；P0 组合「AFI 双 worker + 人工 book」的真实行为已由增量2 验证，其 P0 登记簿记应在验收轮一并核对。

Rationale (why not clean GO, why not NO-GO):

1. Every behavioral fragment of REG04 except the old-ID mapping property is directly exercised by committed, hash-matching tests (6448381f SDK 4 nodes; e9c7ca1d AFI 2 nodes) with two independent reviews and a root adjudication, and the MIG03 incr-A gate is real fail-closed machinery present in `ServiceHost.start()` before any side effect; EXE03's UNALLOCATED separation is a formally accepted foundation.
2. The one genuinely untested element — the rename/old-ID path — is explicitly disclosed and the recon's own sanctioned path 2 condition ("an explicit recorded scope decision") is satisfied by the memo's boundary section and MIG03 obligation, so a PASS on this basis is permissible and not a concealment.
3. However the memo's negative-transfer claim overstates reach for a renamed different ref (today only same-ref duplication and rollback-after-first-write are blocked), the memo is still untracked, and two evidence-level nuances (E/R quota projection not mounted; reservation negatives at host/registry level rather than ZMQ wire) must travel with the PASS; hence GO-with-disclosures, not GO.
4. No falsifier was found that invalidates the cited commits, hashes, test assertions, gate semantics, or EXE03 acceptance; a NO-GO would require either a hidden overclaim or a broken evidence chain, and neither exists.
