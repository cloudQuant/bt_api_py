# Iteration 10 CTP callback drain-status evidence

This record covers the local SQLite callback-ingress drain-status Store API round. The Execution `dev` commit is `5c43f438aec4c40406fd651625d6fae0d0b02684` (parent `1abd6166a7394d714acb74382eed97080b92c187`); its post-commit receipt confirms the three-path scope, matching committed and checkout pins, empty index, and preservation of unrelated changes. The SDK parent was `ff602cd98e996f1c3c21e022142907cb8f8c2bdb` when this evidence packet was prepared.

R3 selected 10 tests: 2 Store checks passed and 8 runtime checks failed. R4 selected 10 tests: 8 passed and 2 runtime checks failed. The four distinct Store checks passed across these runs cover PREPARED/pending/ACTIVE drain status, foreign-handle and stale-lease rejection, reporting only the exact dual `owner_stop` poison, and rejection of other first-poison states. The R4 runtime failures remain `test_runtime_installs_callback_owner_before_init_and_shares_real_managed_owner` and `test_runtime_incomplete_native_join_retains_owner_until_bounded_close_retry`; therefore the SDK runtime round is still partial. R3/R4 source-pin snapshots report 490 inputs and unchanged sources and HEADs.

Evidence is local Store/runtime behavior with synthetic native leaves only. It is not evidence of native CTP operation, an account/provider connection, venue behavior, or a formal acceptance pass; the formal pass increase is 0. The archive contains public QA summaries, JUnit, guard reports, logs, specifications, source-pin snapshots, selected/export manifests, and the Execution post-commit receipt. It excludes `pytest-tmp`, SQLite/database files, native binaries, credentials, and private payloads. `MANIFEST.json` inside the archive records the size and SHA-256 of every included payload.

Archive: [root-qa.zip](evidence/iteration10-ctp-callback-drain-status-20261018/root-qa.zip) — 225,596 bytes, SHA-256 `1ad93bf4fb26c02c87bdb9c4a1e343f98fe7f7c6549b03a90495a407002d9842` (21 public payloads plus the archive manifest).

The two SDK parent files are prepared for scoped delivery; this record does not claim they have been staged or committed.
