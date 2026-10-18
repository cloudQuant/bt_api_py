# Iteration 10 CTP execution runtime focused QA

The opt-in CTP runtime installs durable callback capture before login and supplies one TraderClient, Store, writer lease, and execution owner to the managed submit/cancel service. Shared Host maintenance renews that lease without replacing its identity; lost ownership withdraws readiness. Shutdown keeps native and Store custody until the stop receipt and callback drain checks complete. The service borrows the runtime lease instead of attempting to create a second writer.

Root’s consolidated R10 run passed all 15 selected tests: seven execution-runtime lifecycle checks, one shared Host maintenance check, four callback-ingress drain-status checks, and three borrowed-writer-lease checks. JUnit reports 15 passed, zero failures/errors/skips in 8.146 seconds; console reports 8.22 seconds and one expected warning that the fixed source fixture has no CTP native extension. Stderr is empty. The guard recorded 190 module metadata entries, 14 expected origins, no guard failures or denied events, fixed-source fallback, and two expected CTP native-import rejections. The before/after snapshots match across 491 source pins and eight repository HEADs.

This run resolves the earlier incomplete runner results without rewriting them. R3 and R4 are preserved with their partial JUnit outcomes (2/10 and 8/10); their postflight also rejected the legitimate PEP 420 `bt_api_risk.containers` namespace. R5–R7 postflight rejected an unused Gateway origin, and R8’s one passing JUnit node did not complete postflight because of the PEP 420 handling. R9 was prepared but not executed while the common-runner fallback diagnostic was repaired. R10 is the first consolidated 15-node run with successful source postflight; the earlier JUnit pass counts are not treated as accepted runs.

Evidence level is `LOCAL_CTP_RUNTIME_LIFECYCLE_WITH_SYNTHETIC_NATIVE_LEAVES_ONLY`. The tests exercise the fixed Python source and synthetic native/API leaves. They do not establish live native CTP operation, an account/provider connection, full Risk integration, fresh managed settlement, venue acceptance, or any formal case pass. Formal pass increase: 0.

The archive preserves public command/result/JUnit/guard/log/spec/source-pin artifacts for R3–R8 and R10, plus selected/export manifests and source-head receipts. It excludes `pytest-tmp`, SQLite/database files, native binaries, credentials, and private payloads. Its `MANIFEST.json` lists each payload’s source, byte size, and SHA-256.

- [Root QA archive](evidence/iteration10-ctp-execution-runtime-20261018/root-qa.zip) — 788,109 bytes, SHA-256 `80861686a8901680c75fe1827e8481be1fc8c6c046abc0169672bfb488df2320`.
- [Round record](evidence/iteration10-ctp-execution-runtime-20261018/round-record.json).

No files were staged or committed as part of preparing this record.
