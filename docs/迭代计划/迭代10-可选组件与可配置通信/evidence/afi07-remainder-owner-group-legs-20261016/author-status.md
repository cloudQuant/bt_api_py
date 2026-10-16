# AFI07 remainder status — GREEN (2026-10-16, final)

## Acceptance result: PASSED (standard fixed-source run r9)
- Spec (authored): D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/afi-approved-target-cross-process-20261016/qa/r9/authored-spec-r9.json
- Working copy of spec: D:/bt_api_py/.codex-tmp/iteration10-approved-target-http-qa-root-r9.json
- Output dir: D:/temp/afi_iter10_approved_target_http_root_20261016_r9 (fresh, single run)
- Runner: qa/r9/runner.py (byte-identical to qa/r8/runner.py)
- Command: `python "<...>\qa\r9\runner.py" "<...>\qa\r9\authored-spec-r9.json"` (from D:/bt_api_py)
- Exit: runner RUNNER_EXIT=0; exit.txt=0; pytest_exit=0
- junit: tests=5, failures=0, errors=0, skipped=0, time=68.334s ("5 passed, 2 warnings in 68.33s")
- result.json: sources_unchanged=true (2147 pins), heads_unchanged=true
  (ai-for-investor @ 34f59117ac9a2e73f00b6908cfeeb11fa53f5610, branch dev), stderr empty
- evidence_level: SYNTHETIC_HTTP_SDK_EXECUTION_HOST_APPROVED_TARGET_WITH_SYNTHETIC_RISK_AND_PROVIDER
- Artifacts archived: qa/r9/{authored-spec-r9.json, spec.json, runner.py, command.json, exit.txt,
  junit.xml, result.json, source-before.json, source-after.json, stdout.log, stderr.log}

## Nodes (5: 2 r8 regression + 3 AFI07 remainder)
1. test_approved_target_http_reaches_real_execution_host_and_replays_journal (17.35s)
2. test_approved_target_operation_is_durable_across_concurrent_processes_and_restart (30.98s)
3. test_afi07_strategy_and_http_orders_share_one_owner_with_exactly_once_dispatch (9.71s)
4. test_afi07_ordinary_operations_do_not_modify_group_legs (8.87s)
5. test_afi07_accepted_group_revocation_disposition_is_durable (0.06s)

## Sub-item coverage
1. 策略单: ONE host child serving one account owner; HTTP manual + HTTP approved-target + direct SDK
   strategy submit (ServiceExecutionClient role=strategy_client) all land on the same owner/journal;
   exactly-once dispatch per accepted op via submit_authorized; group-leg namespace untouched.
2. 已接受组撤销处置: SqliteOrderGroupStore real machinery — create_or_get → ACCEPTED dispatch →
   intent FAILED → revoke → REJECTED_BEFORE_DISPATCH requires GROUP_REJECTED_BEFORE_DISPATCH +
   safe pre-dispatch release (no submitted_intent; phases in {NOT_STARTED,DENIED,RELEASED}) → budget
   RELEASED; unsafe release refused with ContractValidationError. (No HTTP→SDK→host path for this
   node; store/coordinator machinery used — honest gap, accepted per mission contingency.)
3. 普通操作不修改组腿: group snapshot (legs/reservations/budget/state) byte-identical across manual,
   approved-target, strategy submit and strategy cancel; orders.cancel of the strategy group-leg
   intent id rejected by the namespace check.

## Edited files (owned only; NO production files touched; no commits anywhere)
- D:/source_code/ai-for-investor/src/backend/tests/test_service_read_authorized_http_integration.py
  (AFI07 block appended at end, ~lines 7054-8663; sha256 6612a7bb9acc5e4d0b4d025600194bba2a9a7dda9a85e55534b8c4369cba5cc3)
- D:/bt_api_py/.codex-tmp/* (spec r9, helper scripts, this status)
- D:/bt_api_py/docs/.../qa/r9/* (new evidence archive; r8 untouched)

## QA environment invariants (kept)
- Frozen extraction D:/temp/afi_iter10_approved_target_sdk_sources_20261016_r6_fixed, 6 PYTHONPATH roots.
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1, PYTHONNOUSERSITE=1; 8 env vars unset.
- pytest ini D:/bt_api_py/.codex-tmp/iteration10-approved-target-http-pytest-r3.ini; --tb=short; -q.
- Fresh output dirs only; r1-r8 spec/artifacts untouched.
