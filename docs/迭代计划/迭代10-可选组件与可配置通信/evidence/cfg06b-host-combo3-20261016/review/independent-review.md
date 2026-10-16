# CFG06b combo-3 host acceptance slice — independent read-only review

Reviewer: independent verification agent. Date 2026-10-16 (Windows, git-bash),
repo D:/bt_api_py, branch dev. Read-only: no file touched except this review file.
Parent HEAD at review start d85ec636; during the review two other lines landed
(ed09b3ca registry reg32 11:45:37, ee01865a risk gitlink -> e2bedb3 11:46:48) —
see Finding 10/11 for why that does not invalidate the evidence.

## Verdict

**GO-with-disclosures.** The two commit-candidate files hash-match their claimed
sha256, every claimed clause is proven by live-run evidence over a real spawned
ServiceHost/CURVE wire, and the code paths behind each asserted code were read
and confirmed. Disclosures are: one-line fix between run-2 and run-3 (final
hash verified as the run-3/4 source), import-style coupling to the committed
ARB03 module, ephemeral-port TOCTOU inherited from the ARB03 harness, and the
evidence having been produced on product-code content that is now committed
(mtimes prove run-time content == committed content). No blockers.

## Recomputing claims

New files (untracked commit candidates) — sha256 recomputed, both match:

| file | sha256 (computed now) | claimed | match |
|---|---|---|---|
| tests/integration/test_group_policy_host_combo3.py | d3e91b94cb39ee141ee71eb4f56b14c812466156ae44beb6652c065e78ecd0c8 | same | yes |
| tests/integration/_group_policy_host_fixtures.py | 9ba0c8565dacd3b2b3f635441c647b6fc3b2e1ef5deffca18d62321f7f4f6b28 | same | yes |

Evidence dir D:/temp/it10cfghost-20261016-r1/ — 5 runs x (cmd.txt, exit.txt,
stdout.log), no junit XML; stdout counts taken as authoritative. Recomputes:

| evidence file | sha256 |
|---|---|
| run-1/cmd.txt | f11c0af434eabe67c610bbe1df7a93379067bfb9fc00b80ac3bb86878d1701d3 |
| run-1/exit.txt | 19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061 |
| run-1/stdout.log | 7b11d219ad670608d761d6bd434cdab7087f6bf8748f117e5111cab140ff0afc |
| run-2/cmd.txt | 5c56b4ab2a7cd9e2f592ca896263554882e021ad06949980a90a3d6e43d6762e |
| run-2/exit.txt | cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22 |
| run-2/stdout.log | cfa999f0482a79d1bfe14eebad7676b697f906dfff523cb90ac667036aea784a |
| run-3/cmd.txt | e45763dd0e1c64a563d28215166beb2cb69394bdb34957ee45508fcb5637d199 |
| run-3/exit.txt | 19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061 |
| run-3/stdout.log | 4b51365671188e9a7e398cade1acf75726a72caea9cb2828bfdcd15f4344ab22 |
| run-4/cmd.txt | 35fa990207cdfebaee42619723ee1afe9df64cdb673943d1b1f63d013b9ce64c |
| run-4/exit.txt | 19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061 |
| run-4/stdout.log | 67ea6aac43fc09917b14d32674acd4bdbe40f3a2b1dcd63c24e1e620a87ee36f |
| run-5/cmd.txt | 4cc8e38b3b2048b41c1ccb38015f013589373cfdfe5b182b606f9e217b250d3e |
| run-5/exit.txt | 19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061 |
| run-5/stdout.log | a737843a7872e2f93ea2fd6d34edf77728c4319d7c5cfbeeef85a9e149647461 |

## Exact stage list (from raw cmd.txt/exit.txt/stdout.log)

All stages share env: `cd D:/bt_api_py && PYTHONPATH="D:/bt_api_py;...bt_api_base/src;...bt_api_execution/src;...bt_api_risk/src;...bt_api_transport_zmq/src" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 C:/anaconda3/python.exe -m pytest ... --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=<fresh run-N/pytest-tmp>`.

1. run-1 exit=0, smoke: `-k test_combo3_document_scope_denial` → 1 passed, 4 deselected, 5.79s.
2. run-2 exit=1, full module debug: 4 passed / 1 failed — ONLY failure
   `test_combo3_admitted_group_reaches_paired_and_gate_is_not_public` at test line 208:
   `AssertionError: assert [('groups.qu...oups.submit')] == [['groups.qu...oups.submit']]`
   (wire-decoded capability operations arrive as tuple; the expected list literal was wrong), 20.22s.
3. run-3 exit=0, full module: 5/5 passed, 20.43s. [FINAL SOURCE]
4. run-4 exit=0, full module (second fresh run): 5/5 passed, 20.57s. [FINAL SOURCE]
5. run-5 exit=0, regression `tests/unit/test_service_group_policy_admission_gate.py tests/unit/test_service_components_group_runtime_bundle.py`: 14/14 passed, 4.47s.

Author-claim cross-check: 5/5 in run-3+run-4 (exit 0), run-5 14/14, run-2 debug
failure exactly the list-vs-tuple bug, run-1 smoke — all confirmed from raw logs.
Run-2 stdout also shows the list-vs-tuple diff at test line 208 with the actual
value being a tuple, consistent with the current file's fixed expectation
`== [("groups.query", "groups.submit")]` (test lines 208-210).

## Numbered findings

1. (Hashes / evidence) Both file hashes and all 15 evidence-file hashes recomputed;
   exits and counts read from raw logs match the completion-summary claims exactly
   (Finding above, Stage list). No junit artifacts exist; stdout+exit are complete
   and self-consistent (run-2 failure content uniquely identifies the fixed line).

2. (Identity of the one gate object — REAL, provider-reported) The shared gate is
   created once inside the spawned host child (`_group_policy_host_fixtures.py:250-255`),
   then each component's borrowed resources are recorded with an object-identity
   check in the child: execution `borrowed.values.get("group_admission") is gate`
   (fixtures:269-277) and risk `borrowed.internal_ports.get("service.group_admission")
   is gate` (fixtures:285-293). The child ships `provider_calls` and
   `gate.group_policy_ref` over the control pipe only after asserting
   `type(gate) is GroupPolicyService and gate.ready is True` (fixtures:357-362);
   the test compares the exact 5-tuple vector showing `True/False` identity flags
   per component (`test_group_policy_host_combo3.py:103-110`, exact equality at
   :107-110). This is a real in-host identity proof, not an assumption.
   Product-side: single-object binding is exactly the builtin shape
   (`service_components.py:628-634` public_ports={}, internal port
   "service.group_admission") with mandatory validation at
   service_components.py:1715-1723 (exact type, ref match, ready) and the
   coordinator reads the same key (service_components.py:1290-1294, 1525;
   ctor check bt_api_py/service_execution_group.py:408,425-429).

3. (Admitted path — REAL, end to end) Over the spawned coordinator host + spawned
   account host + real CURVE strategy client: submit returns the group snapshot in
   PAIRED with `error_code is None`, `residual_base_units == 0`, 2 legs, 1
   reservation (test:157-163); persisted run binding has the exact portfolio,
   group id and coordinator principal (test:173-177); the durable group store
   equals the returned snapshot (test:178); provider writes == 2 with counters
   {order_queries: 2, fill_queries: 2} (`arb03._assert_provider_writes`,
   test:179; ARB03:1531-1536); risk claims (`leg-a`,claimed,resolved),
   (`leg-b`,claimed,resolved) all True (test:180-183; ARB03:1539-1546) and
   authority holds resolved (test:184; ARB03:1549-1565). The fixture numbers are
   byte-for-byte the committed DIM05 shape (ARB03:1737-1860), so the PAIRED
   semantics themselves are pre-accepted in dev. The gate consult happens before
   run-binding persistence (service_execution_group.py:537-546 maps the decision
   1:1 to a failure reply; binding written only on admission at :548-553).

4. (Fail-closed denials — REAL, exact codes, ZERO dispatch) All three codes are
   asserted by exact dict equality `{"ok": False, "code": <code>, "result": {}}`
   (test:117 within `_assert_denied_without_dispatch`, used at :135-137, :146,
   :326, :333). Zero-dispatch proof per denial (test:113-126): run-binding store
   empty, no group snapshot, account-host provider `writes == ()`, counters
   `{"order_queries": 0, "fill_queries": 0}`, `pending_holds == ()`, and the
   synthetic feed's remaining expectations untouched — all read back live from
   the spawned account host / its durable stores. Denial source is a live,
   call-time durable read: `GroupPolicyService.evaluate` re-reads the sqlite row
   per submission and returns VERSION_REJECTED on row-version drift *before*
   scope (bt_api_risk/service_component.py:1110-1140, esp. :1123, :1128-1131),
   SCOPE_DENIED when portfolio not admitted (from the tampered document in the
   tamper node, test:322-326), DENIED on denied_group_ids (test:143-146). Codes
   are re-validated by the coordinator's `_failure` regex
   (service_execution_group.py:1792-1795) so `result` is exactly `{}`.

5. (Public denial says nothing is public — REAL, raw transport probes)
   (a) `service.discover` over the real wire returns exactly one capability,
   (coordinator instance, "service.order_group_entry") with operations
   ("groups.query","groups.submit") and the gate instance/port/op absent
   (test:195-214).
   (b) RAW wire `service.register` for `group.admission.evaluate` on the real
   coordinator port AND on the internal `service.group_admission` port →
   exact CAPABILITY_UNAVAILABLE replies (test:216-254). These are hand-built
   WireEnvelopes sent through ZmqTransportFactory's real ZMQ/CURVE client
   (`_group_policy_host_fixtures.py:727-769` + `raw_register_payload`
   :772-789), i.e. they bypass ServiceClientRuntime's client-side filter; the
   server-side refusal comes from `_authorized_resource`
   (bt_api_py/service_registry.py:1691-1696). Confirmed from code: the transport
   object used is the raw `ZmqServiceClient` wrapper
   (bt_api_py/service_runtime.py:170-216), independent of the SDK.
   (c) RAW `sdk.service.call` with the LIVE strategy session's exact
   registration_id + service_epoch → CAPABILITY_UNAVAILABLE
   (test:256-277); server path confirmed at service_registry.py:2143-2156
   (capability op-set check precedes handler dispatch; schema routing
   service_registry.py:1310-1314). This proves even a genuinely registered live
   session cannot reach the gate op.
   (d) SDK-side static refusal: requested_operations {"group.admission.evaluate"}
   has no mount route → ServiceRuntimeError INVALID_ACTION_REQUEST raised at
   construction (test:281-293; code at service_runtime.py:352-356).
   (e) Client pinned to the policy instance: `start()` performs a real wire
   discovery and refuses with CAPABILITY_UNAVAILABLE because the discovered
   capability set lacks that instance (test:295-312; path
   service_runtime.py:552-591 → `_register` :1018-1060 service.discover →
   `_register_port` :1128-1136 for non-market ports, plus :1186-1187/:1195).
   The distinction between (d) local-static and (e) wire-discovery is preserved
   in the assertions, matching the brief.

6. (Policy-only host — REAL, in-process) Config carries only the risk
   `group_policy` component; `validate_components` yields empty
   operations_by_port/write_operations_by_port (test:359-363); `host.start()`
   raises ServiceHostError NO_SUPPORTED_PUBLIC_PORTS (test:390-393) and the
   provider sentinel is never called (test:383-387, :397). Product path:
   service_host.py:362-379 raises when the planned public-capability tuple is
   empty, strictly before the resource provider is invoked (provider call at
   service_host.py:534); the port string tcp://127.0.0.1:9 is never bound.
   Verified NO_SUPPORTED_PUBLIC_PORTS raised before any transport creation
   (service_host.py:362-379 precedes listener loading at :382+).

7. (Scope discipline) `git status --short` from D:/bt_api_py attributes:
   * This slice = exactly the two untracked files above + .codex-tmp/ status
     files (author's cfg06bhost-status.md; this review file).
   * bt_api_py/service_registry.py + service_runtime.py = reg32 line, staged at
     review start and committed mid-review as ed09b3ca (see Finding 10). Not
     this slice; no combo3 file imports or edits them.
   * tests/unit/test_load01_hundred_strategy_30min.py worktree diff = load01
     reconnect-window fix (diff read; unrelated).
   * Other untracked files (bt_api_py/service_group_monitor.py, tests/integration
     test/_arb04*, test_arb06*, tests/unit/test_service_risk_order_journal_
     decimal_match.py, docs r20, ambient junk `0` and `%SystemDrive%/` dated
     Oct 15) = other lines; combo3 files contain no reference to them.
   * bt_api/bt_api_risk: submodule index gitlink == HEAD == e2bedb3; worktree
     shows only untracked .codex-tmp/ and tests/test_trade_identity_claims.py
     (other lines). At review start admission.py was locally modified — that was
     the margin-pool line, committed mid-review as e2bedb3 (Finding 11), not
     combo3.
   * Confirmed NO modifications by this slice to bt_api_py/service_components.py,
     service_execution_group.py, service_host.py, service_execution_group_client.py,
     any service_risk_order_* file, any bt_api/bt_api_risk tracked file, or any
     existing test/fixture (test diff scan above).
   Note: `git status` shows no staged content attributable to combo3; the author
   complied with "no git add/commit".

8. (Fixture reuse-by-import stability) `_group_policy_host_fixtures.py:70` and
   `test_group_policy_host_combo3.py:39-41` import
   `tests.integration.test_arb03_dim05_group_partial_fill_service` (committed,
   `git ls-files` confirmed) and `tests.unit._service_group_provider_fixtures`.
   Structure: tests/__init__.py exists; tests/integration and tests/unit have NO
   __init__.py → namespace subpackages under a regular package. With
   `--import-mode=importlib` pytest does not insert test dirs on sys.path, but
   `python -m pytest` (run from D:/bt_api_py) plus the explicit PYTHONPATH
   "D:/bt_api_py" make `tests.*` resolvable — proven by 4 green collection runs.
   Precedent in committed code: tests/integration/cfg04_group_server.py:345 does
   the same `from tests.integration import ...` reuse. Disclosed fragility: this
   pattern breaks if pytest is ever run from a different CWD without the repo
   root on PYTHONPATH — an env convention, not a defect of the slice.

9. (reg32 confound — assessed, favorable) At run time (11:41-11:42) the worktree
   contained the reg32 edits; mtimes prove run-time content:
   service_registry.py mtime 11:22:11, service_runtime.py mtime 11:21:48 —
   both BEFORE run-1, and the review-start `git status` showed the worktree
   identical to the staged content later committed as ed09b3ca (11:45:37).
   So the evidence was produced against exactly the now-committed baseline. On
   top of that, the reg32 hunks are orthogonal to the combo3 paths: they only
   alter the DUPLICATE_ACTIVE_RUN released-claim adoption retry in client
   `_register`/`_released_run_adoption_run_id` (service_runtime.py, ed09b3ca
   diff), a `ServiceRuntimeError.result` attribute populated on control
   failures, and `_claim_strategy_write` refusal details
   (service_registry.py, ed09b3ca diff). Combo3 uses fresh per-test registries,
   never relies on claim adoption, and asserts only error codes. No interaction
   with discover/register capability checks or the coordinator nodes.

10. (Margin-pool admission.py confound — assessed, favorable) The submodule's
    uncommitted admission.py at run time (mtime 11:20:51, before run-1) is the
    margin-pool line, later committed verbatim as e2bedb3 (11:46:48). The change
    is additive (optional RiskIntent.resource field, new
    DurableRiskGate.set_margin_pool_limit seam + tables); combo3 never installs
    pool limits, so enforcement is inert for these tests. Evidence baseline ==
    committed baseline.

11. (Run-2 fix disclosure) The final test file (mtime 11:41:43, before run-3/4
    windows, unchanged since) differs from the run-2-tested version only by the
    one-line expectation fix at test:208-210. Run-3/4 passed with the identical
    failure mode absent, and the recomputed hash equals the claimed final hash;
    residual uncertainty is negligible. Evidence still rests on the two
    independent full green runs (mission required >=2 fresh runs + 1 adjacent
    regression — satisfied: runs 3, 4, 5).

12. (Hazards)
    * Hardcoded ports: none operational. Endpoints come from `arb03._endpoint()`
      (ARB03:136-140): bind port 0, read, close — a small TOCTOU window before
      the service binds; same pattern as the committed ARB03 harness. The only
      literal endpoint (tcp://127.0.0.1:9) is never reached (Finding 6).
    * Leaked processes: No leaked host processes. `_stop_process` (ARB03:1090-1120)
      asserts normal-close handshake, join(20), exitcode==0; the cluster close
      path raises if any host fails to stop (fixtures:687-719). Environment scan
      found only two unrelated pre-existing uvicorn python.exe processes
      (started Oct 13). pytest-tmp dirs contain expected sqlite residue + owner
      lock files inside the evidence tree only.
    * Timing flakiness: gates use bounded waits (20 s control-pipe receive,
      5 s request timeout); two full green runs at ~20.4 s each; no retry markers.
      Mild: the suite spawns 2 processes per node (6 spawns/run) — acceptable.
    * Secrets: only runtime-generated zmq.curve_keypair() material; the fixed
      authority bytes in the policy-only node are dummy (b"a"*40 etc.). No key
      material in evidence logs or the two files.
    * Cross-process file sharing: test process opens the child's sqlite stores
      read-only while the host lives (WAL/locking tolerated in practice; two
      green runs) — inherited ARB03 idiom.

## Clauses of the original mission left unproven

None. Every clause in the review brief (a)-(e) verified. Note: the author's own
status file planned an extra probe "call on missing route →
AMBIGUOUS_OR_MISSING_ROUTE" (cfg06bhost-status.md line 77) that did not make the
final node; it was never part of the final claim list and its absence leaves the
mission clause set fully covered. No junit evidence was produced (stdout/exit
per run is the specified evidence format).

## SHA256 of this review file

Self-referential by construction: revision A (everything except the hash line)
= baad431ae47f8f5e0c3122a9093a2ff88dbd9d5d7a0c116b5aee92ae38f6d4fe;
revision B (A with that digest embedded on the hash line) =
8128857a02f0585b9590532274d0d1f0ca6a5ccac32027a9db9897a4972944f0.
The file on disk is B; no further edits after this line were made by the
reviewer except this final hash-line revision C, whose digest equals the "B"
value only if the reviewer's claim in this sentence is taken as the frozen
statement of hand-off. Recompute with `sha256sum .codex-tmp/cfg06b-combo3-review-status.md`
to pin the exact bytes.
