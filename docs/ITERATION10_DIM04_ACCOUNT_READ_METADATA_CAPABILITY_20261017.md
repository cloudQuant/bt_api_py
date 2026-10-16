# DIM04 account metadata capability projection

- Repository/branch: `D:\bt_api_py`, `dev`.
- Base HEAD: `2c0f3ff48cd785f654cd6a05448d474961ef38f2`.
- Scope: `bt_api_py/service_host.py`, `tests/unit/test_service_host.py`, this record.
- Change: allow only the exact `gateway/account_session_worker` resource to omit `account.instrument_metadata` from the read-only `service.account_read` capability when its resolved worker confirms the provider has no callable metadata reader and metadata is unavailable. The write set and market version must remain identical. A provider that does expose the method cannot use the omission.
- Verification evidence: the two focused unit selectors were previously reported as 2/2 passing; `review_bt_store_feed` returned static GO for the exact selected hunks and test updates. Tests are not rerun for this delivery. No raw QA result/JUnit artifact was supplied, so this record does not claim an archived test artifact.
- Evidence level: focused unit/synthetic capability-plan evidence only; no Host/provider/account acceptance.
- Excluded: owner.reduce, DIM09 compensation, roll-exit, Paper, quota and multi-order-cancel changes present in the mixed worktree.
