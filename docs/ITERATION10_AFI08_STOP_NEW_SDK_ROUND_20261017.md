# Iteration 10 AFI08 STOP_NEW SDK delivery

This is a narrow STOP_NEW source-and-test round prepared from the existing SDK `dev` checkout. It does not complete AFI08 or stop a strategy process.

## Selected source

The reviewed source patch is `49ee0e9d42ee483eff43b66815f89b6950c86b97f8892837dae61b5310e2dfab` (22,096 bytes), based on `1cb885c1504fc0166c33c9a912c75c458f6d31cf`. Current SDK `dev` HEAD is `35e3879a7c3c43e38fce0bfdacd0668c1f69d6a2`; the committed blobs for all selected files are identical to the patch base. Read-only `git apply --cached --check --unidiff-zero` succeeds at that HEAD.

The patch selects only:

- `bt_api_py/service_registry.py`: HEAD blob `896f8368f0fb1ec2c66a95e69ad16c9a91d0705a` → 138,117 bytes / SHA-256 `6f4c7f23c76766048be953dd55da36d65b5f26ec9a92716130d2bf08eee9d3c8`.
- `bt_api_py/service_host.py`: HEAD blob `2ad1aea3e9d36df95d607e8036689c02ca0adc8b` → 123,726 bytes / SHA-256 `409a2d6de6f5d38aa8c6e2237d3a43aa1f302282dad6a62cc82fc6fec5873c5e`.
- `bt_api_py/service_monitor_control.py`: HEAD blob `f510090f7269cf474b7215863d0f1fea92371790` → 22,661 bytes / SHA-256 `effc611db44ff0ccb23ed69871b3d0f5ac2f8b2a93229767a35a2c65a502c741`.

These selected postimages persist an exact strategy/run STOP_NEW fence and bounded receipts in Registry, deny later submit/reservation writes before dispatch, route Monitor control through that same Registry, and have Host maintenance pass the Registry to the control owner. The current worktree contains mixed edits: its `service_registry.py` and `service_host.py` hashes are respectively `d914e8324721d89e8c62b6f90ffb0c67a074e4a3570bcfa9560af7f1798fef6f` and `9b70542c2969286df1a2cd067176eb6e350c3316e3a1cdaf579df12470363382`. Apply the selected patch to the index only; do not replace live source files.

## Focused evidence

- Root fixed-source R5 Registry selector `test_stop_new_fixed_postimage_scope_persistence_and_migration_priority` passed. The neighboring Host-maintenance selector failed in that same invocation because its fake omitted the complete method-tail relay attribute. Preserve that failure; R5 was not an all-green run. Source QA checkpoint: `.codex-tmp/iteration10-root-checkpoint-20261017-1438.md` (run `e13640`, stdout SHA-256 `0ff4c453aa307664a11428f9d4701c1e70e1ab1f37d646f865f19c11c5a43c3f`).
- The corrected R3 maintenance selector `test_fixed_postimage_host_maintenance_passes_same_registry_to_monitor_control` passed once in the author run (1 passed). It uses a real SQLite Registry and the bound `ServiceHost._maintain_components()` method on a synthetic owner object; it does not start a Host listener or child process. Output: `D:\temp\afi08-stop-new-maintenance-root-r3-20261017`; stdout SHA-256 `9b02778cb61c4d7ef3caacf0ec35ddbaafd17d7639685d1455f4c565adfd0c88`; stderr empty.
- Root fixed-source STOP_NEW HTTP selector `test_stop_new_http_fences_exact_owner_run_and_returns_persisted_receipt` passed once: 1 passed in 3.88 seconds, with two known warnings. It covers synthetic authenticated HTTP/owner fixtures, foreign-user rejection, final receipt projection, exact-run durable fencing, replay, and subsequent submit denial. Output: `D:\temp\afi08-stop-new-http-current-stopnew-root-r1-20261017`; stdout SHA-256 `06cea448aca50a4459d00741314becef27bc3207d335364710235465281a7b43`; stderr empty; 2,723 source pins and nine repo HEADs unchanged. The AFI test is already committed in the AFI checkout and is referenced here, not duplicated.
- Independent static review returned GO_STATIC for the selected source patch. Do not rerun the accepted Registry, maintenance, or HTTP selectors for delivery.

For a compact commit, the test payload contains the exact imports/helpers used by the two accepted Registry and maintenance nodes and those two functions only. AST comparison confirmed both function bodies unchanged from the R3 overlay, and the Registry test function AST-identical to R5. The reduced file parses successfully; its tests were not rerun. Delivery pin: 9,113 bytes / SHA-256 `C144C2DF9C231AC8591030631EEEBB1DF6D50DEC76DDE137D69522AF1D0CD5DC`.

The destination test path `tests/unit/test_service_registry_stop_new.py` is absent from HEAD but exists in the live worktree as different untracked WIP (13,239 bytes / SHA-256 `99fb2b795dccce04b4d5a099eb2c834ff7b9becf199c5445904e97704f7b6256`). Preserve those bytes. The prepared patch adds the reduced accepted-node test to the index only, leaving the existing untracked worktree file untouched; inspect the staged/worktree split before commit.

## Scope and status

Evidence is local synthetic owner-control acceptance; no real provider or account was used. FREEZE remains separate. STOP_NEW does not prove process termination, cancellation, reduction, or account management; DRAIN/CLOSE remain pending. AFI08 is still partial. This packet and its selected patch are prepared only: no files were staged or committed.