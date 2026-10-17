# Iteration 10 Risk policy catalog read contract

Date: 2026-10-17
Repository: `bt_api/bt_api_risk`, branch `dev`
Risk commit: `ad5c693e69b66b0982b7f889e6f237ca3629e587` (parent `489ef3d9a319356e72ccab4082540a63d3692d2f`).

This round adds the two read-only service operations required by the AFI policy-control HTTP view: `policy.inspect` and `policy.confirmation.read`. Both accept exactly `{policy_ref}` and enforce owner scope. Inspection returns the canonical policy version/content. Confirmation status validates any stored document against the receipt digest and fails closed when the confirmation store is not bound. These operations do not assert account-gate effectiveness.

Risk child commit scope is exactly:

- `bt_api/bt_api_risk/src/bt_api_risk/service_component.py` — selected read-operation implementation only.
- `bt_api/bt_api_risk/tests/test_policy_catalog_readonly_service_operations.py` — fixed-source SQLite regressions.
- `bt_api/bt_api_risk/docs/ITERATION10_POLICY_CATALOG_READONLY_SERVICE_CONTRACT_R1_20261017.md` — source and evidence record.

Focused fixed-source validation passed 2/2 tests, with no skips, failures, or errors. All 146 pinned sources remained unchanged and stderr was empty. Independent static review approved the production patch and the final test cleanup delta. The evidence is local SQLite/source-contract only; no Host or AFI HTTP ran in this round, and it does not establish formal AFI05/06 acceptance.

The SDK parent change in this commit updates only the Risk gitlink and this record. Other parent worktree changes are unrelated and remain unstaged.