# REG04 INCR-1 independent review — COMPLETE

- Reviewer: adversarial, read-only. Deliverable: D:\bt_api_py\tests\unit\test_service_reg04_worker_isolation.py
- Evidence: D:\temp\it10reg04-20261016-r1 (author) | mine: D:\temp\it10reg04-review-20261016-r1
- Verdict: **GO** (2026-10-16 ~13:05), no blockers.

## Checklist
1. [x] sha256 verified: 7c749a6810295b8f7431d6e01bf39663e3477b248c32455f588e338685910861 == expected == test-file.sha256; unchanged after my 2 runs.
2. [x] git status: round-owned change is ONLY the untracked new test file; all other dirt = other workstreams (MIG03 committed mid-review: 7dbc7b71, f0731ec2; risk submodule admission.py WIP; dim09 files; junk "0"/"%SystemDrive%/").
3. [x] Full read of all 1845 lines (4 nodes + helpers) done.
4. [x] Adversarial judgments:
   (a) REAL: workers/strategy register via ServiceClientRuntime over spawned ServiceHost CURVE wire; claim read from registry DB; both workers renew + probe registers while run-a claim held.
   (b) REAL: DUPLICATE_ACTIVE_RUN via service_registry.py:1936-1954 (_claim_strategy_write, called for STRATEGY_CLIENT at :1882); workers unaffected (renew ok).
   (c) REAL: node1 account-only denied by registry rule :1772-1774, foreign book by scope-match :1716; durable proof: exactly one claim row (run-a, released). node3: handler counter calls==[] for all four rejections; valid case calls==["reg04-group-a"].
   (d) REAL: node4 intruder holds an IDENTICAL ResourceGrant (host process code lines 1518-1527), so denial cannot be resource authorization; it is caller_bindings.get(identity) is None -> CALLER_DELEGATION_NOT_GRANTED (bt_api_monitor/service_control.py:231-233) -> SCOPE_DENIED (bt_api_py/service_components.py:1917-1932). run1 red preserved shows the FIRST attempt denied at registration (service_runtime.py:1257) — the fixed distinction.
   (e) REAL: counting wrapper replaces real gate.activate_freeze_once (used at service_monitor_control.py:204); ledger row checked by direct sqlite read; rejected ids have no row; counter 0 across denials, exactly 1 after valid freeze.
   (f) No sleeps/skips/xfails; bounded waits only (pipe 15s, zmq 5s); writes confined to tmp_path; DBs read via mode=ro.
5. [x] Fresh runs (2x, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 + -p no:asyncio): EXIT=0, 4 passed (5.04s / 4.96s). Author run2/run3/run4 all EXIT=0 4 passed; run1 red preserved.
6. [x] Zero production change: all 7 hashes in production-modules.sha256 recomputed live, ALL match (registry 7c708a43, host 07e95f92, group_reservations 70926f4b, execution_group d6b37ccf, components 2af780d1, runtime 3d7fde59, config 5d436d93). service_host.py WIP was committed mid-review, hash unchanged. service_execution_group_client.py absent from evidence list (live 6fc51a31); my live-state run passed anyway.
7. [x] Env caveat corroborated: without -p no:asyncio, pytest 8.0.0 + pytest_asyncio 0.23.0 crash ('Package' object has no attribute 'obj') on unrelated tests/unit/test_service_registry.py.
8. [x] Clause coverage (SDK/host half): node1=multi-worker no-quota + role/book separation; node2/3=reservation overreach denial + pos/neg scope; node4=monitor non-escalation + action/scope pos/neg. NOT evidenced: AFI app-process level (INCR-2, separate repo/file); "old ID mapping" replay/double-quota.

## Findings (none blocking)
- M1 (disclose): concurrent risk-submodule WIP in core/admission.py (+432/-5, additive; DurableRiskGate file). Freeze path untouched; my run green at live state.
- m1: node1 workers register/renew but never submit an order while claim held (registration-level isolation only).
- m2: node2 "adapter never constructed" is inferred from gate order (service_components.py:1367-1371 before constructor :1382; contrast case hits next gate) — not directly observable.
- m3: git tree churned during review (other agents committing) — evidence timestamps + my live run pin the verified state.

## Post-run hygiene
- ps: only pre-existing stale pids 128016/132224; no leftovers. File hash unchanged. No repo file modified by me (except this status file).
