# Group-source terminal exposure name binding — R1

- Repository: `bt_api_py`, existing `dev` checkout; base `943b615e498d09d3ef4110e714cc552abe7be52a`.
- Scope: `service_execution_group.py` imports canonical `Side` and defines the group-source terminal set as exactly `FILLED`, `CANCELLED`, and `REJECTED`; `service_risk_order_journal.py` imports the existing `Callable` annotation; a new unit test exercises the real `_owner_signed_filled_exposure` function with typed Execution snapshots.
- Contract basis: committed `service_execution_group_proofs.py` admits source-terminal proof only for `{FILLED, REJECTED, CANCELLED}`. Generic Execution `BLOCKED` is intentionally rejected by the new test.
- Selected patch: `.codex-tmp/group-terminal-symbol-r1/selected.patch`, SHA-256 `513d5da4fbbad10e7f6075c3413a55db8c532360384322cb15e8b1b4366bdffc`; independent static review returned GO_STATIC for that patch and base.
- Source QA: one run of the two test functions (seven parameter cases) from the fixed HEAD-plus-patch snapshot. JUnit `.codex-tmp/group-terminal-symbol-r1/junit.xml` records 7 tests, 0 failures, 0 errors, 0 skips, 19.514 seconds; SHA-256 `671434b33a0a05ec9ae3a3b30daffc29825e981991646b83ebbb90857aacc279`. Imports were verified from the candidate SDK snapshot and Git-archive exports of Base `6f314ac`, Execution `43742377`, and Risk `5434184d`.
- The nested command returned a live session before completion; its session handle was not retained. The pytest process later exited and JUnit is preserved, but direct shell exit/stdout and warning count were not captured. No rerun was made.
- Evidence level: focused source-unit regression only. No Host, provider, real account, or formal Iteration 10 acceptance is claimed.
