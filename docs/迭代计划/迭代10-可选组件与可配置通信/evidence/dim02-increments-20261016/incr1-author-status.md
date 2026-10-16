# DIM02 increment-1 status (multi-key nodes) — 2026-10-16

Status: **COMPLETE — 2 fresh green runs at final bytes + GW01 regression run done** (details below).

Delivery file (ONLY new file): `D:\bt_api_py\tests\unit\test_service_market_dim02_integration.py` — sha256 `938125294243acaeee6bb168d0c7f12c661c34ae91032ba599510327839e5e46`.
No commits/staging. No edits to existing files (only this status file + evidence dir).

## Repo state (BEFORE)

- parent HEAD: ee189896902f0f2bb403f4cec99958980b7e2acc (branch dev, `git status` clean except ` M bt_api/bt_api_risk` (foreign DIM04 workstream) and untracked `.codex-tmp/`, `0`, `%SystemDrive%/`).
- bt_api/bt_api_execution: dc8bd7a3682d00cfaef4f15261607043c6d24fca
- bt_api/bt_api_risk: e2bedb32cc7aefc084e9353af3fbef743eea60c8
- bt_api/bt_api_base: b4aabd1e17b9c7252fa7b4d266932087d99b5517
- bt_api/bt_api_transport_zmq: a397fdb460d2deb22e67ceb4963992ef63dbbb0e

## Repo state (AFTER — foreign changes occurred during the session; none made by this agent)

- parent HEAD: **a023e4edcbcb4a6204e2f7d5d2ac7b463660c808** (foreign commit `a023e4ed docs(scn02): adopt 014_2 empty-exchange livefix r2 evidence and round adjudication`, yunjinqi, 2026-10-16 12:10:14 +0800; reflog HEAD@{0}). **All four gitlinks recorded in a023e4ed are IDENTICAL to ee189896** (`git diff ee189896..a023e4ed -- bt_api/` empty), so no submodule commit moved.
- Submodule HEADs (all unchanged): execution dc8bd7a3682d00cfaef4f15261607043c6d24fca; risk e2bedb32cc7aefc084e9353af3fbef743eea60c8; base b4aabd1e17b9c7252fa7b4d266932087d99b5517; transport a397fdb460d2deb22e67ceb4963992ef63dbbb0e.
- Foreign working-tree dirt appeared during the session (NOT mine, NOT touched): `bt_api/bt_api_execution` now has ` M src/bt_api_execution/group_coordinator.py`, ` M .../group_order_contracts.py`, ` M .../group_store.py` (another workstream); `bt_api/bt_api_risk` unchanged dirt (` M core/admission.py`, untracked `.codex-tmp/`, `tests/test_trade_identity_claims.py`). Parent `git status --porcelain` now also shows ` M bt_api/bt_api_execution` and ` M bt_api/bt_api_risk`; my only repo additions are the untracked new test file and this status file.
- This agent ran NO git mutation commands (no add/commit/stash). Two lingering `python.exe` processes are unrelated Oct-13 uvicorn servers (`D:\source_code\ai-for-investor` backend), not test leftovers; all spawned test processes exited cleanly (both green runs end in `_assert_no_fallback_cleanup` passing and `_join_normally` asserts).


## Load01 protocol checks

- 12:06 +0800 check: `NO-LOAD01-PROCESS` (ps1 script `D:/temp/it10dim02-20261016-r1/check-load01.ps1`); `D:/temp/it10load01-20261016-r2/` last log mtime 11:51:56 (>5 min old); `D:/temp/iteration10-load01-pilot-d85ec636-20261016-r2/` does not exist. No waits needed so far. Script re-run before each test run.

## Probe run (environment + invocation verification)

- Evidence: `D:/temp/it10dim02-20261016-r1/run1p-probe/` (cmd.txt, stdout.log, stderr.log, exit.txt).
- Command (verbatim): from `D:/bt_api_py`, env `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src"`:
  `C:/anaconda3/python.exe -m pytest tests/unit/test_service_market_integration.py::test_market_epoch_change_requires_explicit_incomplete_resync --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=D:/temp/it10dim02-20261016-r1/run1p-probe/basetemp -q`
- Exit 1 (`1 failed, 1 warning in 2.68s`). Benign `PytestConfigWarning: Unknown config option: asyncio_default_fixture_loop_scope` as expected under plugin-autoload-disabled.
- **FOREIGN/PRE-EXISTING FAILURE (not ours, recorded, do not edit):** the GW01 epoch node (`test_market_epoch_change_requires_explicit_incomplete_resync`) fails at its own line 903 `assert ready[0] == "ready" and ready[2:5] == ("latest_event", False, False)` — the client ready tuple is `("ready", registration_id, account_ref, snapshot_kind, complete, baseline, cursor_epoch, cursor_sequence)`, so `ready[2:5]` is `(account_ref, "latest_event", False)`. Provenance: commit 4d5cebf5 changed the ready tuple (added account_ref) and only fixed the main node (`ready[2:5]` -> `ready[3:6]`), leaving the epoch node stale. File is `git diff HEAD`-clean; failure reproduced before any local modification by this agent (no new file existed). This node is NOT GW01 delivery evidence (recon: delivery node is the ten-client node).
- Spawned-process machinery WORKS under this exact invocation (the epoch node's spawned client replied `ready` before the assertion mismatch), so the new multi-key file will use the same flags.

## Modeling decisions

1. **Adapter-level merge group (recon §6.2 coordinator decision, hub unchanged):** the hub keeps subscribing per key (production untouched). The test-declared adapter contract says fully compatible subscriptions — same instrument + same `bar` channel with different timeframes (`bar/1m` + `bar/5m`) — share ONE underlying connection; the first member opens it, the last release closes it. Groups declared: spot-trades singleton; spot-bars {1m,5m}; each option strike singleton (different `instrument_id` = not fully compatible, so not merged; conservative). Dual-level counting asserted: hub-facing per-key acquisitions (6 logical upstreams) vs adapter connections (5 physical with all keys live).
2. **Sparse option chain projection is implemented TEST-SIDE** as a join across per-key authoritative hub heads (`_project_option_chain`), with a comment; production has no chain aggregation and none is claimed. `head==0 and observed is None` -> explicit `no_data` (never a zero quote/bar); inconsistent head/observed pairs raise.
3. **Deterministic observed_at:** `_observed_at(key_index, key_sequence)` = fixed 2026-01-02T03:04:05Z origin + `key_index` hours + `key_sequence` seconds; returned by the provider publish command and compared byte-for-byte on hub heads and SDK events (no local-clock fabrication; distinct hour bands per key prove no cross-key bleed).
4. Keys (same source/entitlement): (0) spot `trades`; (1) spot `bar/1m`; (2) spot `bar/5m`; (3) option call 105 `quotes` (has data); (4) option 110 `quotes` (subscribed, silent); (5) option 115 `quotes` (subscribed, silent). 3 strategies: S0=(0,1,2,3,4), S1=(0,1,2,3,5), S2=(0,1,2).

## Deliverable file

`D:\bt_api_py\tests\unit\test_service_market_dim02_integration.py` (NEW, self-contained, adapted/generalized from the GW01 fixture; no existing file edited).

- sha256 (final bytes, identical before/after both green runs): `938125294243acaeee6bb168d0c7f12c661c34ae91032ba599510327839e5e46`
- Nodes: (1) `test_multi_key_spawned_strategies_share_one_upstream_per_key_and_isolated_projections` — real spawn chain: 1 ServiceHost + 3 strategy client processes × {5,5,3} per-key SDK facades over 6 keys {spot trades, spot bar/1m, spot bar/5m, option C105 (data), option C110 (silent), option C115 (silent)}; full lifecycle. (2) `test_sparse_option_chain_join_marks_silent_strikes_as_no_data` — deterministic (no processes) contract check of the test-side chain join.
- Invocation (verbatim; used for run1q/run2a/run2b/run2c/run3, from `D:/bt_api_py`):
  `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src" C:/anaconda3/python.exe -m pytest <target> --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=<fresh dir> -q`
  (Python 3.11.5, pytest 8.0.0; benign `Unknown config option: asyncio_default_fixture_loop_scope` warning expected.)

## Run log

- run1p-probe (GW01 epoch node, target invocation; BEFORE my file existed): exit 1, `1 failed, 1 warning in 2.68s` — pre-existing epoch-node defect (see Probe section above).
- run1q-collect (`--collect-only` on the new file): exit 0, `2 tests collected in 0.73s`.
- run2a (first full run of new file): **exit 1**, `1 failed, 1 passed in 10.71s`. Failure preserved in `D:/temp/it10dim02-20261016-r1/run2a/`: my own test bug — the post-strategy-1-release status check reused the stale `expected_heads` (key0=2) after key0 had advanced to 3 via the `spot-trade-2` round. Fixed by introducing `expected_heads_after_trade2` and using it for the G/H/I phases.
- run2b (after fix, sha 93812529...): **exit 0**, `2 passed, 1 warning in 10.91s`.
- run2c (fresh rerun at same final bytes, sha verified identical before and after): **exit 0**, `2 passed, 1 warning in 10.76s`.
- run3-gw01-regression (full existing `tests/unit/test_service_market_integration.py`): **exit 1** — `1 failed, 1 passed in 40.92s`. PASSED: `test_ten_spawned_clients_share_market_stream_and_expired_lease_releases_only_its_ref` (GW01 delivery node, undisturbed). FAILED (pre-existing, FOREIGN, reproduced identically by run1p before my file existed): `test_market_epoch_change_requires_explicit_incomplete_resync` at its own line 903 `ready[2:5]` mismatch (commit 4d5cebf5 left this node stale; file is `git diff HEAD`-clean; NOT edited).
- Evidence dir: `D:/temp/it10dim02-20261016-r1/{run1p-probe, run1q-collect, run2a, run2b, run2c, run3-gw01-regression}` — each has cmd.txt, stdout.log, stderr.log, exit.txt. Failures preserved as-is (no overwrite/retry-overwrite).

## Assertion coverage -> clause mapping (node 1 unless noted)

- Per-key provider counts: `key_starts == {each of 6 keys: 1}` with 3/3/3/2/1/1 subscriber strategies; `hub.readiness.active_upstreams == 6 == number of distinct keys`; per-key stops 0 while held (same key + N strategies -> still 1 upstream).
- Dual-level merge counting: `connection_starts/connection_stops` per declared group; with all keys live: 6 hub-level logical upstreams vs 5 adapter connections (`open_connections` == 5 = `_KEY_COUNT - 1`); bars 1m+5m share one connection; final close: every group started once and stopped once (bar group closes only on the last member release).
- Projection independence: every bootstrap `stream_id == key.stream_id`; 6 distinct epochs, each subscriber of one key sees the SAME epoch; hub head epoch per key equals the SDK bootstrap epoch; per-key sequences (key0 1/2/3 vs key3 1) and per-key observed_at bands (`_observed_at(key, seq)`) never cross.
- Cross-key zero cross-stream: after each publish, a bounded sweep (0.6-0.8 s pump) on every other key/strategy must return zero matching events and zero remaining queue depth.
- Sparse chain / silent keys: strikes 110/115 remain head 0, observed None after publishing strike 105 (hub heads asserted exactly); their SDK bootstraps stay empty (snapshot None, sequence 0) and sweeps return no events — no fabricated events or freshness.
- Sparse-chain join (TEST-SIDE, documented in `_project_option_chain`): fed with real hub heads — exactly 1 of 3 strikes live, other two explicit `no_data` with no price fields; node 2 pins the edge semantics incl. hard failure on inconsistent head/observed pairs.
- Per-key last-release: release cascade S0 -> S1 -> S2 with exact `active_upstreams` (6->5->3->0) and registration-ref counts (13->8->3->0); each key's exclusive upstream stops when its own last subscriber exits; final provider state: every key started once AND stopped once; close through the host re-asserts the same counts.

## Waits caused by load01 protocol

- none: checks at 12:06, 12:22, 12:27, 12:33, 12:40 (+0800) all `NO-LOAD01-PROCESS`; it10load01-20261016-r2 logs stayed at 11:51:56 (>5 min old); pilot dir absent. No 120 s waits were needed.

## Unresolved/remaining risks

- Cursor projection evidence stops at SDK bootstrap cursors + per-key event sequences + hub heads; the SDK has no public post-event cursor read without `recover()` (noted in file comments).
- Sub-items 2 (history->live seam), 3 (bar revision) and 5 (portfolio leg skew) of DIM02 are NOT covered by this increment (recon §5.2/§5.3); leg-skew module also needs a naming decision (recon §6.3).
- GW01 epoch node is red at HEAD (foreign pre-existing defect documented above) — regression run exit 1 is fully attributable to it.
- Foreign head/working-tree moves during the session (parent commit a023e4ed, execution submodule dirt) are recorded above; they do not touch the market path imports (submodule HEADs unchanged) and were not caused or modified by this agent.

## Resume point

DONE for this increment. If a follow-up round continues DIM02: implement recon §5.2 (history+live seam + bar revision fail-closed) next; nothing in this file needs rework. Final state: deliverable file at sha 938125294243acaeee6bb168d0c7f12c661c34ae91032ba599510327839e5e46, two fresh green runs (run2b, run2c), GW01 regression main node green.

