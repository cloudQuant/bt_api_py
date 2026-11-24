# R249 — BT P5A C/P/F local ServiceHost integration evidence

Date: 2026-10-16. Candidate scope is two Backtrader paths, prepared for Root's scoped commit. No SDK files are modified by this round.

## Candidate source

Backtrader `dev` at the R28 run baseline `57c9fc613f263877b08da11eacadcb5a8eaccaad`. Exact overlays:

- `backtrader/stores/service_execution_group.py` — 35792 bytes, SHA-256 `d46a4925534e999b8aa514f2f3e93f042ddf1684cbaf6e87a112cac2c07aa6c0`.
- `tests/integration/test_btapi_execution_group_cerebro_v3_host.py` — 68021 bytes, SHA-256 `46cc9e83306117a8c7dc26f41fa51c9ca30c96d12cf0b1aad0a3b22bf269bd1f`.

Only these two paths belong to the candidate. Other dirty Backtrader files are excluded and preserved. The accepted SDK runtime is immutable source archive `77b86e2139708455722a3f11955cce18b14647fe` plus its exact gitlinks in `manifest.json`; current repository HEADs are guards only, and no dirty SDK checkout bytes were exported.

## R28 result

The fixed-source author run selected one node, `test_v3_cerebro_paired_three_leg_snapshot_over_real_curve_hosts`, and passed **1/1**, pytest exit 0, 35.91 seconds. It emitted one existing Quandl deprecation warning; stderr was empty. The successful run uses local Account/GroupCoordinator/Monitor ServiceHost processes with authenticated ZMQ/CURVE and a synthetic SQLite provider leaf. It proves the happy-path C/P/F adapter integration at that evidence level; it does not establish a real venue, private account, credential, or live-provider result. Monitor Host startup/registration/shutdown is checked, but `monitor.read_after` RPC is not part of this slice.

Assertions cover distinct full Call/Put/Future keys, 1/0.5/2 ratios, 10/5/2 native quantities, 0.01/0.01/0.1 base-unit multipliers, quote-derived gross notionals 80/60/20,000 USD, actual persisted provider-request facts, three SQLite order writes/order queries/fills, PAIRED snapshot, zero residual vector, claims/resolutions, shared Broker economic isolation, and bounded normal shutdown. R28 reads native unit from persisted `ATTEMPT_STARTED.provider_request`, not from an unsupported DTO field or a test-created expected receipt.

The R28 spec retains its creation-time status `UNEXECUTED_PENDING_AUTHOR_QA`; that is not the executed outcome. Use the exact author `result.json`/JUnit in the raw archive. Its result reports 1,440 unchanged source pins and seven unchanged repository HEADs. The top-level R28 source-manifest hash and corresponding source pin equal the actual exported manifest. A nested historical `accepted_source_binding` hash is stale metadata; the runner and independent review found no blocker.

## Inherited and failure history

R23–R25 three-leg Host runs failed the PAIRED snapshot assertion and remain preserved as historical references; R25 traced the quantity-unit fixture mismatch, later confirmed precisely by R26 static review. R14’s adapter node PASS is reused without rerun. R14 itself was a mixed two-node Root run with a failed Host node (`START_FAILED` at Monitor startup and a monitor cleanup assertion); it is not reported as an overall pass. R17 is preserved as another Monitor Host startup failure. R26’s fixed SDK unit-contract rejection and R27’s DTO-attribute assertion failure are kept unchanged in the raw evidence/history references. R27 already reached PAIRED and produced three provider writes, queries and fills before its test-only assertion failed. R28 corrects the unit fixture to SDK-supported `native` and asserts the actual persisted provider request.

The independent reviewer returned `GO_STATIC_R28`, did not run QA/Host, and recommends committing the adapter and integration test together. This round adds **zero formal case PASS**. DIM09 remains partial pending staged zero/partial fills, rejection/UNKNOWN, owner/coordinator restart and same-ID recovery, duplicate-dispatch prevention, nonzero-residual compensation, and related budget release/cost policy. SCN02 has one C/P/F normal path, not all nine directories. BT03 is not a separate formal case row. Current tracker count is 40/68; Root owns updates.

## Packet

`raw-evidence.zip` contains both exact candidate source files, the R28 focused spec/source manifest/fixed source archives/runner inputs, full raw R14 and R17 Root outputs, and full R26/R27/R28 author outputs. `manifest.json` lists every ZIP member with size and SHA-256 and separately records the source, SDK, run, and review pins. No tests or Host were run while assembling this archive.
