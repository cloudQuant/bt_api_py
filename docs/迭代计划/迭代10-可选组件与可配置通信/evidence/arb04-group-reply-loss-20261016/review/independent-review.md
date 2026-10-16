# ARB04 increment — independent pre-commit review (read-only)

Reviewer run: 2026-10-16, after evidence window (11:36:45–11:38:13) and after two
concurrent, unrelated commits landed in the repo (see F8). No repository file was
modified by this review; this file is the only artifact written.

**Verdict: GO-with-disclosures.** No NO-GO blocker. One commit-round action is
recommended (re-run the two tests at the staging HEAD, F8); all disclosures are
listed as numbered findings.

## 1. Artifacts reviewed

- New (untracked, commit candidates):
  - `D:\bt_api_py\tests\integration\test_arb04_group_reply_loss_service.py` (538 lines)
  - `D:\bt_api_py\tests\integration\_arb04_group_reply_loss_support.py` (678 lines)
- Author status: `D:\bt_api_py\.codex-tmp\arb04-status.md`
- Evidence: `D:\temp\it10arb04-20261016-r1\` (20 files)
- Plan anchor: `D:\bt_api_py\docs\迭代计划\迭代10-可选项与可配置通信\正式迭代计划.md:770`

## 2. Recomputed sha256 (baseline for the commit round)

New test files (recomputed now; no run-time hash was recorded in evidence — see F7b):

| file | sha256 |
|---|---|
| tests/integration/test_arb04_group_reply_loss_service.py | `40138c3099117d08024a5b40e1e9978c9272e72df394489dc919f25783311dbe` |
| tests/integration/_arb04_group_reply_loss_support.py | `b508086d6b16d3cca66069cc66b28dae3f116b3e9248859b209c7cec4c86586c` |

Evidence files (recomputed, all present):

| file | sha256 |
|---|---|
| 00-before-pins.txt | `0a4ce0bfd9da1a4d0bf6034f5fbe6c9ed9ec8a06022a7e4247a45a71f73f9cb1` |
| 00-after-pins.txt | `90f3023fd8150ba5699738c2f7a4b3fa93d04e7a97bd27608fb1436403d5b6f7` |
| 01-command.json | `41ba4466190d4deafd4c4c5a828267f328e483e551f87d1495bb9aec156578d9` |
| 02-results.txt | `53f8efbc5463ae0cc320a7e8cd635b1f8fd7f50dfc5f2678011698b7aa8f72fb` |
| run1-junit.xml | `4859b7a319e0ca0ece26cd7c410ddbc6790eadbc5e770419df4d76f6dffe40a6` |
| run2-junit.xml | `8ccb9f32c4146f8caaf6c3a6ee1bb7f972421a0b2abfce7f067c73cafa63076f` |
| regression-arb03-junit.xml | `938007c27df96e06cf2f166f7658a035a794fbfe81ec576b6cc2c3c3da245660` |
| regression-r121-junit.xml | `aedcc59f978c3e6e098c69190d227b04608972509df089c2a43f599fdb4ba5e3` |
| run1-stdout.txt | `a89fe8d7198de5b69c800d721ff296c7035f4d3a8ce6910e485301834cbbe91e` |
| run2-stdout.txt | `53993f4d5c876e916486d65b93b666196de17cfccc404ba5a012e01ebf82174e` |
| regression-arb03-stdout.txt | `8a2da86416704e51590a5586ca586124d22a44b2b6e7973e760941bef5eb0098` |
| regression-r121-stdout.txt | `fd1ee7a766322c4427db1260106ad34ebbecabc95b93dd791d31ddc390a63d89` |
| run1-exit.txt / run2-exit.txt / regression-arb03-exit.txt / regression-r121-exit.txt | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` (all four identical, content "0") |
| run1/run2/regression-arb03/regression-r121 stderr.txt | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (all empty) |

Reused committed test modules (for the self-containment pin):

| file | sha256 | last commit |
|---|---|---|
| tests/integration/test_arb03_dim05_group_partial_fill_service.py | `a37f42a96424d663a9d5eb71d838d5c52538e0e800cc2e0f49de57c1a1b19afe` | `f308c8d8` |
| tests/unit/_service_group_provider_fixtures.py | `9bcf021649104243a2b16a93ef159ceca8cacce6ffad9c22afd94eed4ee3b1e2` | `f308c8d8` |

## 3. Claimed numbers — verified

- run1: exit 0, junit `tests=2 failures=0 errors=0`, stdout "2 passed"; run2: same.
  ARB03 regression: exit 0, `tests=2 failures=0 errors=0`. R121 regression: exit 0,
  `tests=1 failures=0 errors=0` (single test `test_spawned_curve_group_hosts_complete_two_legs_and_preserve_unknown_hold`).
- Test IDs in junit match the two new test functions; junit failure/error counts all 0.
- Timeline is coherent: before-pins 11:36:45 → run1 junit ts 11:36:56 (17.8s) → run2
  11:37:17 (18.2s) → ARB03 11:37:41 (14.8s) → R121 11:37:59 (9.9s) → after-pins 11:38:13.
  (Author quotes 15.1s/10.2s from stdout vs 14.8s/9.9s junit — traceable, cosmetic only.)
- Pins before==after within the window: root `d85ec636`, bt_api_base `b4aabd1e`,
  bt_api_execution `dc8bd7a3`, bt_api_risk `c7fc809b`, bt_api_transport_zmq `a397fdb4`.
  True. NOTE (F8): the repo advanced afterwards — current root is `ee01865a`
  (ed09b3ca registry/runtime, then ee01865a risk gitlink → `e2bedb3`), so these pins
  no longer describe the staging HEAD.
- Determinism: run1/run2 junit are byte-identical after stripping `time`/`timestamp`/
  `hostname` attributes (`diff` clean); only durations differ. Spot-check passed.

## 4. Findings (numbered, with evidence)

**F1 (GO) — Fault injection is genuine, not a fake error.**
`_arb04_group_reply_loss_support.py:111-133` wraps the coordinator host's real
`ZmqTransportFactory` (`:210-217`) and, for the armed op, calls
`self._inner.request(request, timeout=timeout)` FIRST — the encoded request travels the
real CURVE/ROUTER socket to the account host, which fully executes against real SQLite
stores — then discards the returned reply and raises the transport's own
`RequestOutcomeUnknownError(request.header.message_id, "response_timeout")`. That class
and cause code are exactly what the real transport raises after a lost reply
(`bt_api/bt_api_transport_zmq/src/bt_api_transport_zmq/service_v2.py:123-129, 471`).
The coordinator's submit path treats any exception as outcome-unknown
(`bt_api/bt_api_execution/src/bt_api_execution/group_coordinator.py:526-529`), so it
observes the real `ORDER_SUBMIT_OUTCOME_UNKNOWN` semantics (proof that the backend work
REALLY happened: Test A finds the durable account-a record CANCELLED/filled 1.5 with
`dispatch_attempts == 1`, test file `:283-300`).

**F2 (GO) — Wire proof of "no re-send" is sound.**
Counts come from a process-local `_WireFaultState` (`support:88-108`) shared by every
transport the coordinator host creates (the sole upstream client path, `support:210-229`),
exposed via the child control pipe (`support:351-353, 636-643`). `orders.submit_group_leg`
counts are asserted exactly: account-a == 1 after call 1 (`test:179`), after call 3
(`test:223`), and in Test B after the late-fill replay (`test:429`); account-b never
submitted (`:180, 224, 430`). Independently triangulated by the account side:
`dispatch_attempts == 1` (`test:288, 310, 504`), provider writes exactly 1 per account
(`test:331`), and the provider fake refuses unexpected writes
(`test_arb03...:441-443`). A hypothetical resend would be counted (shared state) and
would also break the exact provider-write counters.

**F3 (GO) — Assertions match every author claim and are non-vacuous.**
Call 1 → `RECOVERY_REQUIRED`, `error_code None`, budget `HELD`, leg-a `UNKNOWN` with
`submitted_intent` qty 2, leg-b `READY` (`test:160-176`). Call 2 → `PAIRED`,
`residual_base_units == 0`, leg-a child CANCELLED 1.5 with
`TERMINAL_EXPOSURE_RESOLVED`, leg-b FILLED 3 (`test:199-217`). Recovery by the ORIGINAL
intent id: the coordinator recovery path queries
`progress.submitted_intent.intent_id` (`group_coordinator.py:484-488`; hedge `:747-749`)
via `orders.query_group_leg(intent_id)` (`bt_api_py/service_execution_group.py:349-360`);
a foreign/absent id returns None → `FIRST_LEG_STILL_UNKNOWN`, never PAIRED — so the
observed PAIRED + CANCELLED fact can only come from intent_a
(`group_leg_intent_id(GROUP,"leg-a")`, `test:241-247`). Exactly one `HEDGE_LEG_SIZED`
with `"3.000"` and one `GROUP_PAIRED`, zero `GROUP_RECOVERY_REQUIRED`
(`test:272-276`; sizing math `group_coordinator.py:697-733`; format from step precision).
Duplicate replay `replayed == paired` and persisted == replayed (`test:221, 247`).
Query wire counts exactly 2/1 (`test:225-226`). No `reservations.release` ever on the
wire (`test:229-230, 434-435`); budget HELD at UNKNOWN time is durably proven by the
persisted event payload amended with the then-current budget state
(`test:252-263`; `bt_api/bt_api_execution/src/bt_api_execution/group_store.py:585`).

**F4 (GO) — Late-fill test genuinely freezes, with durable proof on every axis.**
The late fill is injected directly into the synthetic provider ledger AFTER the UNKNOWN
(`test:87-125, 401-404`, asserting the pre-state open/0-fill/rev1), then the replay stays
`RECOVERY_REQUIRED` with `error_code == "order_outcome_unknown"`
(`group_coordinator.py:607-626, 1305+`), child UNKNOWN 0-fill, no resolution receipt
(`test:408-425`). Frozen wire/durable facts: submits a=1/b=0, queries a=2, no resolve, no
release (`test:427-437`); provider counters frozen {order_queries:1, fill_queries:1}
(`test:441, 445`; exact-equality assertion also via `_assert_provider_writes`,
`test:534-537`); record_a outbox exactly
[intent_recorded, intent_admitted, dispatch_claimed, dispatch_unknown] — 1 submit intent,
1 dispatch_unknown (`test:507-513`); intent_b has no record/outbox (`test:517-523`);
`_pending_hold_intents` a == (intent_a,) i.e. 1, b == () i.e. 0 (`test:532-533`);
account-b never did order work (writes (), counters 0, expectations unconsumed,
`test:443-447`). No blind reverse: no compensation/reverse code exists in the
coordinator (grep `reverse|compensat|UNWIND` in group_coordinator.py: no matches);
hash comment log: grep found nothing.
And the author's residual-gap claim is real: `_read_receipt_locked` is a journal/record
read (`bt_api_py/service_execution_group_proofs.py:1594-1640`), and the allowed proof
sequences (`:1646-1653`) plus the ATTEMPT_UNKNOWN early return (`:1675-1689`) leave no
commit path for a fact that first appears after ATTEMPT_UNKNOWN.

**F5 (GO) — Self-containment; no reliance on other agents' in-flight files.**
Local imports: test → `tests.integration._arb04_group_reply_loss_support`,
`tests.integration.test_arb03_dim05_group_partial_fill_service` (helpers), `tests.unit._service_group_provider_fixtures`
(`_V2_INSTRUMENT`), plus committed product modules (`bt_api_execution`,
`bt_api_py.service_execution_group_authority`). Support → committed product modules only
(`bt_api_base`, `bt_api_execution`, `bt_api_risk`, `bt_api_transport_zmq`,
`bt_api_py.service_{components,config,execution_group,execution_group_client,host,registry,runtime}`)
plus the ARB03 module. All are tracked/committed (ARB03 module + fixtures at `f308c8d8`;
`service_registry.py`/`service_runtime.py` now committed at `ed09b3ca`). Grep confirms
nothing else imports the ARB04 support; ARB04 does not import
`_group_policy_host_fixtures.py` / `test_group_policy_host_combo3.py` /
`test_arb06_...` (other agents' untracked pairs). Neither file is git-ignored.

**F6 (GO) — Assertions cannot silently vanish; code parses.**
Both files pass `ast.parse` (no execution). No `time.sleep` anywhere in either file.
Exact-equality assertions on counters, event sequences and payload values (F3/F4) are
order- and count-pinning, so a behavior regression fails loudly.

**F7 (disclosures, non-blocking) — Hazards and residual fidelity nuances.**
a) The wrapper raises the genuine exception AFTER the inner transport successfully
decrypted/validated the reply, so the inner `ZmqTransport` does not enter its real
post-timeout state (no `_last_uncertain_message_id`, no socket retire,
`service_v2.py:518-525`). The coordinator-visible contract (exception class + cause code
+ message_id) is identical; the socket-level fidelity gap is inherent to reply-discard
injection. No observed behavioral consequence (counts stay 1; no message-id reuse
errors), but it is a fidelity limit of this fault model.
b) No run-time sha256 of the two untracked test files was recorded in the evidence dir;
`00-before/after-pins.txt` only prove they were untracked and unchanged as git-state.
My recomputed hashes above (section 2) should be used as the commit-round baseline.
c) Cleanup: cluster close is bounded (20s handshakes, `test_arb03...:1090-1120`) but has
no kill fallback — a hung host would leak a process (pre-existing ARB03 pattern; not
observed). `--basetemp` dirs `D:/temp/it10arb04-bas1`, `D:/temp/it10arb04-bas2` remain on
disk (pytest never removes them) — outside the repo, cosmetic.
d) Timing: no sleeps; wall-clock only for deadlines with wide margins (group request
deadline +120s, group max lifetime 300s, request timeout 5s vs observed per-test ~8-9s).
Ports are ephemeral (`bind(127.0.0.1:0)` probe, `test_arb03...:136-140`) — small TOCTOU
window, standard for this suite. No hardcoded paths/ports in either file. No secrets:
CURVE keys are generated at runtime in-process; temp SQLite files hold synthetic data.

**F8 (disclosure, commit-round action) — Evidence pin drift after the window.**
During this review the repo advanced past the evidence pins: `ed09b3ca` (11:45:37,
committed the previously-dirty `service_registry.py`+`service_runtime.py`, 36+108 lines —
same diffstat as observed at review start) and `ee01865a` (11:46:48, risk gitlink
`c7fc809b → e2bedb3`). Current: root `ee01865a`, risk `e2bedb3`; other three submodule
HEADs unchanged. ARB04 itself committed nothing (its two files are still untracked and
the only ARB04-caused repo entries). The tested content is, with high probability,
exactly what is now committed (verbatim diffstat match), but byte-identity across the
window is not proven by the artifacts. Recommended commit-round action (cheap, ~40s):
re-run the two ARB04 tests (plus optionally the two regressions) at the staging HEAD and
record the new pins, then stage. This mirrors the project rule that acceptance evidence
must describe the committed state.

**F9 (scope fit vs plan line 770) — legitimate slice; several sub-clauses explicitly
left open.**
Plan clause: "任一腿提交/撤单超时、晚到成交、重复回报及网络恢复；UNKNOWN 不重发、不提前
释放，不把补偿当作成功回滚 | socket 故障和持久 journal；原业务 ID 查询，确认事实前不得
盲目发反向单".
Proven by this increment: submit-reply loss/socket fault on a leg (F1-F3) with durable
journals; UNKNOWN ⇒ no resend, no early budget release, no resolve/release/reverse
(F2-F4); recovery by the original business ID (F3).
NOT proven / left open (all disclosed by the author, none silently claimed):
- 撤单超时 (cancel-under-reply-loss): design-only section in `arb04-status.md:46-92`; no
  implementation. This is the largest unproven half of the clause.
- 网络恢复 (true disconnect/reconnect): the fault is a discarded reply over a healthy
  socket; genuine link loss/recovery is not simulated (overlaps TRN01).
- 重复回报 (duplicate provider fill report): only duplicate `groups.submit` replay
  idempotence is exercised (F3); duplicate FILL reporting is not directly driven.
- 晚到成交 adoption: Test B honestly freezes (F4); the plan's late-fill handling is
  proven only in its refusal half — no re-observation/adoption path exists yet
  (documented as the residual gap, `arb04-status.md:31-42`).

## 5. Process faithfulness statement

The evidence is process-faithful: two spawned account hosts and one spawned coordinator
host in separate OS processes (`multiprocessing spawn`), real ZMQ ROUTER/DEALER over
CURVE encryption on loopback, real SQLite databases for the group store, execution
store/outbox, risk order journal, group authority and risk gate; the only synthetic
element is the provider leaf behind the real DirectBackend at the outer account boundary
(documented fixture design). The fault is injected at the coordinator's transport
wrapping layer: the request reaches the real account host and fully executes (durable
records prove it), only the reply is discarded and the genuine
`RequestOutcomeUnknownError` surface is raised — subject to the socket-state nuance F7a.

## 6. Exact stage list for the commit round

```
git add tests/integration/test_arb04_group_reply_loss_service.py
git add tests/integration/_arb04_group_reply_loss_support.py
```

Nothing else. All other working-tree entries (`bt_api/bt_api_risk` untracked content,
`tests/unit/test_load01_hundred_strategy_30min.py`, `bt_api_py/service_group_monitor.py`,
`tests/integration/_group_policy_host_fixtures.py`, `tests/integration/test_group_policy_host_combo3.py`,
`tests/integration/test_arb06_group_monitor_relay_service.py`,
`tests/unit/test_service_risk_order_journal_decimal_match.py`,
`docs/.../risk-order-exposure-sdk-round-r20.md`, `.codex-tmp/`, the stray `0` and
`%SystemDrive%/` directories) belong to other workstreams or pre-existed and must NOT be
staged. No ARB04-caused edits to any product file exist (no `service_*.py`,
no submodule file differs because of ARB04; verified via `git status --short` and
per-file `git diff`).

## 7. Verdict

GO-with-disclosures. The increment is genuine, self-contained, non-vacuous, and its
evidence verifies. Ship after (a) re-running the two tests at the staging HEAD and
recording the fresh pins (F8), and (b) the commit message/notes stating the unproven
sub-clauses (F9: cancel timeout design-only; true network recovery and duplicate
fill-report not exercised; late-fill adoption deliberately unimplemented) so the ARB04
plan item is not represented as fully closed.
