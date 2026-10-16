# it10regfix-20261016-r1 — Repair of two pre-existing red unit-test nodes on dev HEAD

Repo: D:/bt_api_py @ dev HEAD ed09b3cab8a6ae9d16d726a61d49bf223f736e31 (parent of d85ec636 visible in log chain).
Python: C:/anaconda3/python.exe. Shell: git-bash. Dates: 2026-10-16.

Common env for every run (matching reg32 R2 artifacts at D:/temp/it10reg32-20261016-r2/):
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1
- `-p no:cacheprovider --noconftest --import-mode=importlib --basetemp=<fresh per run> --junitxml=<step>/...xml`
- Normal PYTHONPATH (from cwd D:/bt_api_py or an export):
  `<root>;<root>/bt_api/bt_api_base/src;<root>/bt_api/bt_api_execution/src;<root>/bt_api/bt_api_risk/src;<root>/bt_api/bt_api_transport_zmq/src`
- Exact commands per step in each step's cmd.txt.

## Step 00 — 00-baseline-head (pristine HEAD export; proves pre-existing red)

Export: `git -C D:/bt_api_py archive HEAD | tar -x -C 00-baseline-head/export`, then each of the 20
gitlinks from `git ls-tree HEAD -- bt_api/` archived from its submodule repo at the recorded SHA
(all 20 SHAs available locally; extraction succeeded). PYTHONPATH points only at the export.

| run | file | result | junit |
|---|---|---|---|
| red1 | tests/unit/test_service_config_scoped_execution_routes.py | exit 1; 1 failed, 20 passed | 00-baseline-head/red1-junit.xml |
| red2 | tests/unit/test_service_host_integration.py | exit 1; 1 failed | 00-baseline-head/red2-junit.xml |

Signatures (red1-stdout.log / red2-stdout.log):
- `TypeError: ...close_clients() got an unexpected keyword argument 'allowed_call_errors'`
- `bt_api_py.service_host.ServiceHostError: RESOURCE_KEYS_MISMATCH` (assert `('error','RESOURCE_KEYS_MISMATCH') == 'ready'`)

Conclusion: both nodes were already red on clean HEAD, independent of the reg32 dirty files.

## Step 10 — 10-attribution (optional; done)

Exports at `b63000b2^` (prev) and `b63000b2` (at), root+all 20 submodules at those commits' gitlinks
(all SHAs available). Ran only
`tests/unit/test_service_host_integration.py::test_spawned_curve_host_serves_four_sqlite_read_adapters_and_closes_normally`.

| export | result | exit | junit |
|---|---|---|---|
| prev (b63000b2^) | 1 passed | 0 | 10-attribution/prev/host-node-junit.xml |
| at (b63000b2) | 1 failed, RESOURCE_KEYS_MISMATCH | 1 | 10-attribution/at/host-node-junit.xml |

`git diff b63000b2^ b63000b2 -- tests/unit/test_service_host_integration.py` is empty: b63000b2 did not
change the test, so the regression is caused by the builtin key-set change in bt_api_py/service_components.py
(("execution","account_owner") resource_keys = {store,targets,dispatch,admission}).

## Fixes (only the two permitted test files touched; no product file edits; no git add/commit/stash)

Red #1 — tests/unit/test_service_config_scoped_execution_routes.py (line 407):
-`    def close_clients(*_args) -> None:`
+`    def close_clients(*_args, **_kwargs) -> None:`
Helper `_cleanup_group_hosts` (tests/integration/test_arb02_group_budget_service.py:279) now calls
close_client_threads with `allowed_call_errors=...`; the stub must accept arbitrary kwargs.

Red #2 — tests/unit/test_service_host_integration.py (execution branch of `_resource_provider`, ~line 270):
supply the full four-key trusted resource set required for account_owner whenever a public port is bound:
`{"store": store, "targets": {scope: _EXECUTION_SCOPE}, "dispatch": _UnusedDispatch(), "admission": {"allow_unprotected": True}}`
plus a module-level `_UnusedDispatch` stub (submit/cancel raise; never called in this query-only test),
mirroring the passing pattern in tests/unit/test_service_execution_account_books_wiring.py (`_UnusedDispatch` +
`{"allow_unprotected": True}`). `account_books` deliberately NOT added (it is only appended to expected_keys
when present). `_close_resource("execution", store.close, closed)` retained, so lifecycle assertions
(`shutdown[1] == ("execution","risk")`) stay intact. Dispatch type is not PaperMatchingPort and
_EXECUTION_SCOPE provider is "SYNTHETIC", so no paper_owner_domain is required by
bt_api_execution.service_order_entry. Admission mapping is validated by service_components.py:1617-1634
(keys subset of {order_gate,cancel_gate,basic_admission_gate,allow_unprotected});
allow_unprotected=True satisfies ADMISSION_POLICY_REQUIRED. Validator bt_api_execution.service_component
accepts role account_owner on bind port service.order_query.

Diffs: see two-files.diff (git diff limited to the two files).

## Verification runs after fixes (cwd D:/bt_api_py, normal PYTHONPATH)

| step | file | result | exit | junit |
|---|---|---|---|---|
| 20-red1 | test_service_config_scoped_execution_routes.py | 21 passed | 0 | 20-red1/red1-junit.xml |
| 30-red2 | test_service_host_integration.py | 1 passed | 0 | 30-red2/red2-junit.xml |
| 40-final red1 | test_service_config_scoped_execution_routes.py (fresh basetemp) | 21 passed | 0 | 40-final/red1-junit.xml |
| 40-final red2 | test_service_host_integration.py (fresh basetemp) | 1 passed | 0 | 40-final/red2-junit.xml |

## sha256 (sha256.txt)

| file | BEFORE | AFTER |
|---|---|---|
| tests/unit/test_service_config_scoped_execution_routes.py | a110d471…359d9d4 | 306258ae…3358b093 |
| tests/unit/test_service_host_integration.py | 2d3eddba…45b5078 | 97330020…5fb33db7d |

## Sibling-stub findings

`grep -rn "def close_clients(" tests/` → exactly two hits:
- tests/unit/test_service_config_scoped_execution_routes.py:407 — the fixed stub (was `(*_args)` without kwargs).
- tests/integration/test_arb02_group_budget_service.py:278 — the real helper `def close_clients() -> None:` that
  forwards kwargs to close_client_threads (not a broken stub; not modified).
No other file uses the `def close_clients(*_args...` stub shape, and no other test currently fails from this
defect (reg32 run2 flagged only this node). No second one-line fix applied anywhere.

## Scope / residual uncertainty

- Only the two permitted test files were modified. `git status` shows no other newly modified files from this
  session (pre-existing dirty items remain untouched: bt_api_py/service_registry.py, bt_api_py/service_runtime.py,
  tests/unit/test_load01_hundred_strategy_30min.py, untracked files). No git add/commit/stash performed.
- Residual uncertainty: none observed for the two nodes; both files pass in full with the documented env.
  The two nodes were not re-run under the full repo conftest (--noconftest replicating reg32 R2); per instructions
  this matches the original red invocations.
