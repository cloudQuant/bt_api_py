# Iteration10 AFI08 FREEZE/expiry delivery R265

This round delivers owner-authenticated FREEZE and exact status projection, including the terminal no-effect EXPIRED repair. It does not implement STOP, DRAIN, CANCEL, REDUCE, CLOSE, or account management.

At packet creation, the exact SDK partials and Monitor gitlink are staged against parent `dev` HEAD `4f5ae610b21ac635cc97099209690f8e16995d6c`. Full staged-file pins are in `manifest.json`. The four patches are reviewed R6 filtered hunks replayed onto the current parent; committed R261 `account.configuration` and `account.server_time_probe` remain present. R250, Paper, DIM04, MIG03, and other shared-file WIP are excluded. The SDK source commit ID will be finalized in a follow-up evidence-record commit because a Git commit cannot encode its own final hash.

Committed dependencies: AFI `86ed9dbbe6293b71d7279aafb0f93b1e0c36aa3e` (parent `318608a29997fe9e37db9a8053542375eabf286b`), exactly four AFI08 files; Monitor `7c407634c092ba59383bd870a0b0b19c588ae451` (parent `536a4f04747182749fabdac5e28624f80465bc53`), exactly two Monitor files. Exact committed blob pins and the R6 patch bodies are in `raw-evidence.zip`.

Author evidence is synthetic local only. Original focused suite: **2 passed, 3 warnings, exit 0**; not rerun. Added EXPIRED-only node: **1 passed, 2 warnings, exit 0**. Its preliminary failed attempt due to a synthetic clock preceding `issued_at` is retained in the author record. The test uses local SQLite Monitor ledger, HTTP status projection, ServiceHost owner maintenance, and synthetic DurableRiskGate. It verifies expired unclaimed command no-effect, issuer sequence release only after persisted EXPIRED status, and a later higher sequence applying FREEZE. Two independent reviewers returned GO_STATIC and ran no tests.

Evidence limits: no Root QA, real account/provider operation, process-restart/failure acceptance, or formal AFI08 PASS. UNKNOWN and claimed work remain unresolved. DRAIN/CLOSE remain unsupported; CLOSE awaits DIM04 coordination. Alert acknowledgment does not unfreeze RiskGate.

Source-lineage caveat: the R2 author record pins all four AFI working-tree files. Three match the AFI commit blobs; `monitoring.py` differs (author execution 23,050 B / `79c097...`, commit blob 22,365 B / `84c25...`). The discrepancy is retained; no claim is made that the author run loaded the exact final commit tree. Full source trees are not archived.

Raw archive SHA256: `d1b536d3cd34201c3f359117846bf4026cdf1d7bc964cf2162cb8e14cc262149` (11811 bytes). The implementation source commit is pending postcommit pin finalization in this round record.
