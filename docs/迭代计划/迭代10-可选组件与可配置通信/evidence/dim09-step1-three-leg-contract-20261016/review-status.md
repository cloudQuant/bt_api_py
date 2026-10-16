# DIM09 Step-1 Independent Review Status (iteration 10)

Reviewer: independent review agent. Read-only on submodule; outputs only to D:\temp\it10dim09-review-20261016-r1 and this file.
Resume point: STEP 1 (hash check + evidence extraction) IN PROGRESS.
Tool-call budget: 150.

## Checklist
- [x] 1. Hash-check 4 files vs dim09-status.md: ALL MATCH (67b50a48/81e79337/efba3cd1/4c48f71c; 1584/1720/929/1033 lines). Evidence cmd.txt/exit/stdout extracted; counts 12/9/22F/3, 13, 12/9/22F/3, 25 confirmed from evidence logs. NOTE: 3 baseline dirs lack exit.txt (only stdout.log); final runs have exit.txt (0/0/1/0/0).
- [x] 2a. git status: exactly 3 M + 1 ?? in submodule (group_coordinator, group_order_contracts, group_store, tests/test_group_coordinator_three_leg.py). diff saved to review dir (704 lines). Foreign dirty items in parents listed: M bt_api/bt_api_risk, ?? .codex-tmp/, ?? 0, ?? %SystemDrive%/, ?? bt_api_py/service_market_legs.py, ?? tests/unit/test_service_market_dim02_*.py (other agents').
- [x] 2b. Full diff read; new test file (1033 lines) fully read: 13 tests match claim inventory (11a/11b = last two).
- [x] 2c. Code inspection: v3 invariants present exactly (contracts 497-577); residual_vector emitted only when version==3 (contracts 1474-1479; ctor forbids non-None for v1/v2 1405); store diff only 3 hunks (no _SCHEMA_VERSION/DDL).
- [x] 3. Fresh reproduce complete (review dir, exit 0 each): three_leg 13, coordinator 12, budget 9, risk_integration 3, service_component 25. Deviation: 4-entry PYTHONPATH used for all 5 runs (evidence newfile2 used 2-entry; superset). Transient anaconda python appeared once at one pre-run ps check (likely own prior teardown); cleared; no interference observed.
- [x] 4. Byte-compat probe complete: compat/old_pkg = HEAD package (dc8bd7a) + worktree deps; probe_v1v2.py ran NEW vs OLD; outputs byte-identical (probe.diff EMPTY, 34=34 lines, 16 serialization surfaces incl. v2 quote, hedge_residual_unresolved snapshot+events, v1 legacy snapshot+recovery, paired events, roundtrips).
- [x] 5. residual_vector probe (probe_vector.py, exit 0): v2 payload has NO key; v2 non-None vector rejected "only a version-3 group can carry a residual vector"; v3 accept key present=None; v3 finish ["0","2.5","0"]; roundtrip equal. group_store diff = 3 hunks only; grep SCHEMA/CREATE/ALTER in submodule.diff EMPTY; _SCHEMA_VERSION=2 identical HEAD vs worktree.
- [x] 6. Foreign-failure claim: ^FAILED sets baseline vs final order_entry identical (22=22, diff empty); "paper execution requires an explicit owner domain" appears 44x in BOTH logs. Verified from preserved evidence only; worktree untouched.
- [x] 7. VERDICT: ADOPT. No defects found. Caveats: 3 baseline evidence dirs lack exit.txt (stdout only); cosmetic nits (GroupBudgetQuote legs annotation/docstring still 2/v2; v3 snapshot uses "version-2..." receipt message).

## Final evidence locations
- D:/temp/it10dim09-review-20261016-r1/: submodule.diff (704 lines), review-test_* run dirs (cmd/stdout/exit each), order_entry_failed_before/after.txt + .diff, compat/{old_pkg, probe_v1v2.py, new.out, old.out, probe.diff, probe_vector.py, vector_probe.out}.
- Read-only constraint honored: no edit/stage/commit in submodule; worktree dirty state preserved.

## Protocol log (python-runner serialization)
- T0: ps -W shows 2 python PIDs: 128016 (ai-for-investor backend .venv, since Oct 13), 132224 (Python312, since Oct 13) — long-lived services, not test runners; bounded 30s wait applied, recheck unchanged. Proceeding with recorded note.
