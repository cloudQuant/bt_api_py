# IT10 MIG03 incr-B — evidence index (controlled migration drill, test-only round)

- Evidence root: D:/temp/it10mig03b-20261016-r1 (first-use r1, created 2026-10-16 13:29; frozen)
- Worktree: D:/bt_api_py @ dev; interpreter C:/anaconda3/python.exe (Python 3.11.5, pytest 8.0.0)
- Index produced: 2026-10-16 13:46 (+0800) by the author-side finisher (documentation-only pass; no runs, no git).
- All sha256 values below are of the frozen files at indexing time. This index does not list itself (self-referential hash is impossible); its own sha256 is recorded in `.codex-tmp/mig03b-report.md`.

## File index (28 frozen artifact files)

| # | relative path | sha256 | description |
|---|---|---|---|
| 1 | pins-after.txt | 0a4d062ff85009430f8d36aa9455663f92cc38c0d1ce1d8e93980861ccde228f | sha256+size pins of drill test + 3 source files taken after all runs; byte-identical to pins-before (before==after). |
| 2 | pins-before.txt | 0a4d062ff85009430f8d36aa9455663f92cc38c0d1ce1d8e93980861ccde228f | sha256+size pins taken before the drill/replay runs (same content as pins-after.txt). |
| 3 | regressions/execution-integration-junit.xml | 58218e22c4860fda82bd9174b95e5d4c9c9db57a7830b0bb506fbb4c0763b33c | JUnit: tests/unit/test_service_execution_integration.py — 1 test, 0 failures, 0 errors (3.804s). |
| 4 | regressions/execution-integration-stdout.txt | b0b57f5b6fb6514744e9ad8aa68cc6a0ef56b1df7a210ae6607cce80649c6946 | pytest console output "1 passed ... 3.79s" + appended exit:0 (command line not embedded in file). |
| 5 | regressions/host-integration-junit.xml | 11c7ac7757f083e31b4f80222c9b675a793be5ecb926b15547e03e2d298f98ab | JUnit: tests/unit/test_service_host_integration.py — 1 test, 0 failures, 0 errors (3.114s). |
| 6 | regressions/host-integration-stdout.txt | 243ab8640a4280c9e94fa8344eba4c4c8664d9a835fb0c1af07938dc8085b116 | pytest console output "1 passed ... 3.10s" + appended exit:0. |
| 7 | regressions/host-junit.xml | 8b402c4127afef534f30370c70e7d81b98a2badda312f273baa529b9aa935528 | JUnit: tests/unit/test_service_host.py — 32 tests, 0 failures, 0 errors (4.359s). |
| 8 | regressions/host-stdout.txt | 548bfa69fc6da3f1283f88227acb5db79281cc941f2e9ee4ad708d8897014994 | pytest console output "32 passed ... 4.35s" + appended exit:0. |
| 9 | regressions/migration-gate-junit.xml | 6c4e5892b10b0d1474d54f2c87aee3bd10e11cb259fda04bda2721a6c2525d0b | JUnit: tests/unit/test_service_migration_gate.py (incr-A gate) — 12 tests, 0 failures, 0 errors (2.535s). |
| 10 | regressions/migration-gate-stdout.txt | 772d3d156425dd04618c178a6bf452b7d80224a84337f73a3ab4ba0c2bdd1a74 | pytest console output "12 passed ... 2.52s" + appended exit:0. |
| 11 | regressions/reg03-junit.xml | 51656e93bf7a5a0a21136825981fde87aaf932bd8638ca7086455d267a81b75d | JUnit: tests/unit/test_service_registration_durable_business_lifecycle.py (REG03) — 1 test, 0 failures, 0 errors (5.932s). |
| 12 | regressions/reg03-stdout.txt | 142218bea10c5d40fc50eda0ade88c02d42225aeec448aff4d95b545c4660115 | pytest console output "1 passed ... 5.92s" + appended exit:0. |
| 13 | regressions/registry-junit.xml | ee886be67cfefdcb60a44b5a045c26b3bbc8c89efaae959bf48590518b2473ec | JUnit: tests/unit/test_service_registry.py — 21 tests, 0 failures, 0 errors (1.838s). |
| 14 | regressions/registry-stdout.txt | d9685ae828bc16a67c6108a9ef103238c7775b487991150fec5a8b836ece7467 | pytest console output "21 passed ... 1.82s" + appended exit:0. |
| 15 | regressions/ruff.txt | 738f155d511632ee6513029524fe4eb5a7906043f73aed38d5b49bcc1420e5cb | Exact ruff command on the drill test + "All checks passed!" + exit:0 (13:44:32). |
| 16 | run-00-selfwitness/junit.xml | b03fd027803c9e7d89a896371c3aec6f8d279c16e4a1ef8bd3d0c33a33c70eac | JUnit for self-witness attempt 0; FAILED pre-fix (per status run log: both nodes ServiceConfigError CLIENT_ALIAS_MOUNT_MISMATCH). |
| 17 | run-00-selfwitness/stdout.txt | 7ffda03f652c8208eda6639bd350976615bc297b99b4cc33d403ffa3cdc54fed | stdout of self-witness attempt 0 (pre-fix diagnostic; no command.txt in this folder). |
| 18 | run-01-fix1/junit.xml | 24dc27e2fc8d2f56890a8d6ff9a42ed169a4ca5e9297f10f4b96abf3564fa11a | JUnit for attempt 1 after config fix; 1 of 2 failed pre-fix (test1 expected UNKNOWN, got REJECTED — see report finding). |
| 19 | run-01-fix1/stdout.txt | aec6d0694727f4a337a373c51f1b791a4c71846e98f13efb8629df4974e73e1f | stdout of attempt 1 (pre-fix diagnostic; test2 PASS per status run log). |
| 20 | run-02-fix2/junit.xml | be475eac78533172112c322807878c342d7361abc0f8ad4f416cbd7adf6f2091 | JUnit for attempt 2 after P6 rework: 2 tests passed (14.76s). |
| 21 | run-02-fix2/mig03b-drill-observation.json | 230d27708178d9811740251b9e49cd5b0421a487991523dc3015f8ebcf086eed | Drill observation JSON attempt 2; status PASS. |
| 22 | run-02-fix2/mig03b-rollback-observation.json | 11b2d9f46254d08a574d94feb570346cb33e0e3245c3378ffe614cd376f0edcf | Rollback observation JSON attempt 2; status PASS. |
| 23 | run-02-fix2/stdout.txt | ce36dce64de4380672670e8f220ecf476c3fbf24819609c4d2e6943b3459c164 | stdout of attempt 2 ("2 passed, 1 warning in 14.76s"). |
| 24 | run-03-final/command.txt | 16770d7124c27864436b1ae0d36b9ac67704ce73d8d3ba6cb1bf33097e4af878 | Exact final command + CWD + timestamp (13:43:01) + pytest-exit:0. |
| 25 | run-03-final/junit.xml | 6593d9f24ed5d9aa1dbea52a266b2a7fc43184c2d37b09df48c3b56deafa4e9c | FINAL drill JUnit: 2 tests, 0 failures, 0 errors, 0 skipped (15.003s; nodes 8.052s + 5.973s). |
| 26 | run-03-final/mig03b-drill-observation.json | 522b32961f01f8ba010d08c68f105bd1e47d7576aeba98cce4b60169cebd663b | FINAL drill observation; status PASS (UNKNOWN preserved, REJECTED first write, double-owner + reverse-migration negatives, epochs). |
| 27 | run-03-final/mig03b-rollback-observation.json | c4050b03ae90042f050ee530a5184da274c98d926f37767554fd36cfcf4bdd0a | FINAL rollback observation; status PASS (pre-seal window, marker removed, CUTOVER_MARKER_CONFLICT, facts intact). |
| 28 | run-03-final/stdout.txt | 83bafd61f05d7f5b9b76e59c915c54a9e1a1cccf3c96d5702595f6da888f632f | FINAL stdout: "2 passed, 1 warning in 14.99s". |

Note: the frozen tree contained exactly these 28 files at indexing time (the 28 rows above cover every one). This index (00-INDEX.md) was added afterwards and does not list itself.

## Final numbers

### Drill run (run-03-final)

| artifact | tests | failures | errors | skipped | exit | notes |
|---|---|---|---|---|---|---|
| run-03-final/junit.xml | 2 | 0 | 0 | 0 | 0 (pytest-exit) | 15.003s; stdout: "2 passed, 1 warning in 14.99s" |
| mig03b-drill-observation.json | — | — | — | — | — | status PASS |
| mig03b-rollback-observation.json | — | — | — | — | — | status PASS |

### Regression suites (serialized 13:43:34–13:44:26)

| suite | target file | tests | failures | errors | skipped | exit | runtime |
|---|---|---|---|---|---|---|---|
| migration-gate | tests/unit/test_service_migration_gate.py | 12 | 0 | 0 | 0 | 0 | 2.535s |
| reg03 | tests/unit/test_service_registration_durable_business_lifecycle.py | 1 | 0 | 0 | 0 | 0 | 5.932s |
| host-integration | tests/unit/test_service_host_integration.py | 1 | 0 | 0 | 0 | 0 | 3.114s |
| execution-integration | tests/unit/test_service_execution_integration.py | 1 | 0 | 0 | 0 | 0 | 3.804s |
| host | tests/unit/test_service_host.py | 32 | 0 | 0 | 0 | 0 | 4.359s |
| registry | tests/unit/test_service_registry.py | 21 | 0 | 0 | 0 | 0 | 1.838s |
| **total** | | **68** | **0** | **0** | **0** | **0** | |

Honest note: NO regression suite had non-zero failures or errors; every suite exited 0.

### Lint

| tool | target | outcome | exit |
|---|---|---|---|
| ruff check | tests/unit/test_service_migration_drill.py | "All checks passed!" | 0 |

## Pinned source hashes — before == after

pins-before.txt and pins-after.txt are byte-identical (both sha256 0a4d062f…de228f), listing:

| sha256 | size (bytes) | path |
|---|---|---|
| f5cb66102575abe22217d6caba7ea952552fafe6875c9caa7f0e000af2c91d8f | 87816 | tests/unit/test_service_migration_drill.py (NEW, test-only) |
| 70700cd648da85d9dcca4ff0e55022cd44f2f5e12090a0734a852b5f3f0cc39d | 16721 | bt_api_py/service_migration_gate.py |
| 07e95f92b5782dd18412b0397ab1c50e3ecf8482c720392314d93f4cbf9d0be1 | 66489 | bt_api_py/service_host.py |
| afaca97a071d5f7069203d3a2f4aaa907c2d71607bd440d76b2cf3b58eb263cb | 17406 | tests/unit/test_service_migration_gate.py |

The three production-side pins (service_migration_gate.py, service_host.py, test_service_migration_gate.py) are identical before and after — zero production changes. The drill test hash was additionally re-verified live by the finisher at 13:46 and matches f5cb6610….
