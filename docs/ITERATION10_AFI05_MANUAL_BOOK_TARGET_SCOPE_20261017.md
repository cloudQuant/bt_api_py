# Iteration 10 AFI05 manual-book Risk target scope slice

Date: 2026-10-17  
Repository: `D:/bt_api_py`, branch `dev`, fixed source base `d09ac77838bc4b6727a15e49c867d89c92c4d750`.

## Change

The selected `service_risk_order_entry.py` delta preserves exact strategy-target matching and adds the account-manual path only for a single target whose trusted scope has one account, a manual-book reference, no strategy or run reference, and no portfolio/group/leg scope. Manual-book targets cannot be combined with another target. Persisted journal commands are resolved through the same predicate and still require exact account scope, exchange, provider account, and trusted target lookup. Unknown/wrong manual-book references do not resolve. `WireScope` itself rejects a simultaneous strategy and manual-book reference.

Selected production patch: `.codex-tmp/manual-book-target-selected-r4.patch`, 2,585 bytes, SHA-256 `ced47d1376bb2174645db46d4715ad9de99259a7a97293b44eaddf2aa16c3a87`. It is derived from the SDK `d09ac778` blob for `bt_api_py/service_risk_order_entry.py` and contains only the constructor matcher/singleton rule, persisted matcher, and helper addition. The live service file remains a mixed WIP; do not stage it wholesale. The helper text corrects the manual scope to account + manual-book with `portfolio_ref is None`.

## Verification

The existing focused runner `.codex-tmp/iteration10-focused-qa.py` (SHA-256 `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`) ran the single focused test file against a fresh fixed source export from SDK `d09ac778`, Base `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`, and Risk `b6e4a7bce74a5ae5dee4c8f514a9d95fd68073d9`; both SDK gitlinks matched. R4 used `--noconftest`, the fixed export's explicit SDK/Base/Risk expected-origin environment values, and 399 exact source pins.

R4 result: Python/pytest exit 0, 3 passed, 0 failed, 0 errors, 0 skipped, stderr empty; source pins and SDK HEAD remained unchanged. Output is `D:/temp/iteration10-manual-book-target-nohost-d09-r4/`: `stdout.log` 188 bytes / `268d9bcf76ac00e88cfc5f08c5937605a13f64ad169f9d099cb1e469b2a3619d`, `stderr.log` empty / `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, `junit.xml` 695 bytes / `175b4fac3858b3011c8ec0287b70d052712ce4756cd55b84eb573d44f76462fa`, `result.json` 620 bytes / `b50c9089b54b61c39861cd4cdd9f0ec7628edeed85b1f147f37edcc44fdeacbd`. R4 spec `.codex-tmp/manual-book-target-fixed-source-nohost-r4.json` is 75,734 bytes / `6d44fe5d23b31671f004021eb1eebf28fb4234af18c580c6c1eba53df6bfd4bb`; manifest `.codex-tmp/manual-book-target-fixed-source-manifest-r4.json` is SHA-256 `ecea49efb73a41c3380d95a3893f27cc18befb205796b41cd5091c3d451250c3` after final result binding.

R3 is preserved as an unaccepted fixture failure: the new test attempted to construct a `WireScope` combining strategy and manual-book fields; Base rejected it before the service path ran. R4 asserts this schema rejection directly. The earlier R2 output is also preserved and superseded by the stricter matcher/origin-contract regression.

The added test exercises persisted-command lookup by a `SimpleNamespace` carrying the production wire-scope payload; it is a service lookup-contract check, not a real on-disk restart/recovery test. This round starts no Host, provider, or account process and does not establish formal AFI05/06 acceptance. No files have been staged or committed.

## Confirmation semantics

The normal account-policy component path persists owner confirmation only after the actual account `DurableRiskGate.bind_policy` call and exact ledger-binding readback succeed. That receipt attests the policy-to-account-ledger binding; it does not attest Host readiness or `consumer_effective`. A later Host startup failure must not be projected as effective consumption.
