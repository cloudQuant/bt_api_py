# DIM02 seam increment report — sub-items (2) 回补切实时 + (3) bar 修正 (SDK side)

- Date: 2026-10-16. Repo: `D:/bt_api_py` (branch `dev`). Read-only git: nothing staged, nothing
  committed (`git diff --cached` is empty; the new test file is untracked `??`).
- Scenario: real spawned `ServiceHost` process (real ZMQ CURVE endpoints + sqlite registry) with a
  synthetic deterministic provider that serves bounded/paginated history AND live publications,
  plus a real spawned strategy-client process running `ServiceMarketClient` over
  `ZmqMarketTransportFactory`. No network to real venues; deterministic data only.
- This is ONE increment toward DIM02. It does **not** claim formal DIM02 PASS (sub-items 1/4/5 and
  merge registration are separate lines).

## Files created

| file | sha256 |
|---|---|
| `D:/bt_api_py/tests/unit/test_service_market_dim02_seam.py` (1129 lines, 4 nodes) | `7c86904384a8423d42b2cccdc92984ef185a8e2e9e9df19c705b5612531a0bc6` |
| `D:/bt_api_py/.codex-tmp/dim02-seam-status.md` (status/resume file) | `e47371aa2c15cd67b95513b587b5c1e4c33b27ea06ed1c821e99eda53b584f7c` |

No production code or existing test file was modified. `bt_api_py/service_market*.py` untouched;
the sibling files of other agent lines (`tests/unit/test_service_market_dim02_integration.py`,
`bt_api_py/service_market_legs.py`, `tests/unit/test_service_market_dim02_legs.py`,
`tests/unit/test_service_market_legs.py`) were not touched.

Evidence: `D:/temp/it10dim02seam-20261016-r1/` (created fresh this run; each run dir has separate
`cmd.txt` / `stdout.log` / `stderr.log` / `exit.txt`, never overwritten).

## Exact commands, exit codes, pass/fail counts

Interpreter/env (reused from `D:/temp/it10dim02-20261016-r1/run1p-probe/cmd.txt`):
`C:/anaconda3/python.exe`, `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONPATH` = bt_api_py + `bt_api/{bt_api_base,bt_api_execution,bt_api_risk,bt_api_transport_zmq}/src`,
`--noconftest --import-mode=importlib -p no:cacheprovider`, `--basetemp` under the evidence dir.

- **Run 1** — full file, all 4 nodes (command in `run1/cmd.txt`):
  exit **0**, `4 passed, 1 warning in 11.94s` (0 failed / 0 error / 0 skipped; the single warning
  is the ambient `PytestConfigWarning: Unknown config option: asyncio_default_fixture_loop_scope`,
  unrelated to this file).
- **Run 2** — full-file rerun for determinism (command in `run2/cmd.txt`):
  exit **0**, `4 passed, 1 warning in 12.45s`.
- **Run 3** — collect-only node list (command in `run3/cmd.txt`): exit **0**, 4 tests collected:
  1. `tests/unit/test_service_market_dim02_seam.py::test_history_to_live_seam_is_gap_free_and_passes_through_source_times`
  2. `tests/unit/test_service_market_dim02_seam.py::test_silent_key_and_beyond_head_read_do_not_fabricate_bars_or_freshness`
  3. `tests/unit/test_service_market_dim02_seam.py::test_corrected_history_reread_returns_provider_supplied_values`
  4. `tests/unit/test_service_market_dim02_seam.py::test_conflicting_live_overlap_fails_closed_at_the_consumer_projection`

Serialization protocol: `ps -W | grep -i python | grep -v grep` before each run; only two
long-lived uvicorn dev servers were present (since Oct 13); one transient
`python -c "import backtrader ..."` probe from another agent line (12:19:20) had already exited
before run 2 started. No waits were needed; runs were strictly serial. Recorded in each `cmd.txt`.

## What each node proves

1. **Seam continuity (sub-item 2).** Subscribe first (silent bootstrap: snapshot `None`, head 0,
   `latest_event`, `complete=false`, `full_baseline_available=false`); two warm-up publications
   consumed and discarded (GW01 SUB-join pattern); bounded paginated history `[T0,T4) limit 2`
   → `(B0,B1, complete=False, next=T2)`; second page `[T2,T4) limit 2` → `(B2, complete=True,
   next=None)`; history reads leave the live cursor unchanged (epoch/sequence frozen at 2 after
   warm-ups). Live bar `B3` published through `hub.publish` with provider-supplied
   `observed_at_utc=T4` arrives as sequence 3 with epoch equal to the bootstrap epoch,
   key/stream_id identity exact, and `header.sent_at_utc == "2026-10-14T00:04:00Z"` — the exact
   provider source time, not local wall-clock (also asserted different from the machine's
   now-second). Seam math: last history bar close `T3` == first live bar open `T3` (no gap); no
   history bar overlaps `[T3,T4)` (no unintended overlap for the same key); live cursor advances
   0→2→3 consistently, registration releases cleanly (`starts,stops == (1,1)`, `active_upstreams`
   returns to 0; host close `("closed","closed",1,1)`).
2. **No fabricated backfill.** Silent key with an active authoritative subscription
   (`active_upstreams == 1`): `idle-poll` yields zero events, cursor stays (epoch, 0), no head
   advance, no freshness claim, no synthetic zero bar. A history read of an interval with no
   provider bars returns an explicit empty `complete` page (`bars == []`,
   `next_start_time_utc is None`) whose `observed_at_utc` equals the provider-supplied value
   (never wall-clock, never invented bars). Provider fetch count/timeout recorded exactly once.
3. **Correction via re-read (sub-item 3, history leg).** Same bounded request read twice around a
   provider-side correction of an already-read bar (same `open_time`, different OHLCV): the second
   read returns the corrected provider value (page equality against the provider-published page),
   page 1 is not mutated, nothing is cached by hub or SDK (provider `fetch` called twice with
   exact timeout `5.0`), live cursor unchanged by both reads.
4. **Conflicting live overlap — fail-closed at the consumer boundary (sub-item 3, live leg).**
   Live re-delivery of an already-read bar (identical duplicate of `B2`) and a conflicting
   revision of another read bar (`B1` with same `open_time`, different OHLCV) are **both
   forwarded by the SDK stream verbatim, in provider order, unchanged** (observed: sequences
   [3, 4], exact source times, exact payloads; no SDK-side dedup, merge, replacement, or drop).
   The fail-closed policy is then enforced at the consumer projection boundary driven by those
   real SDK events (`_FailClosedBarProjection` in the test file): identical overlap deduplicates
   exactly once (`len` stays 3, no double count); conflicting overlap raises
   `_ProjectionConflict`, leaves the projection at 3 bars with the original delivered `B1`
   (no partial projection, no silent replacement).

## Documented correction policy (FAIL-CLOSED) and where it is enforced

Documented in the module docstring of the new test file:

- The SDK market surface **never interprets bar payloads**: `GatewayMarketHub.publish` takes
  opaque `data`; the SDK stream client validates stream identity/epoch/sequence/key only and
  forwards every event verbatim. It performs **no dedup, no merge, no replacement, no drop, and
  no partial projection** (demonstrated observationally in node 4), and it holds no bar
  projection of its own.
- **No fabrication**: silent keys yield no events/head advance/freshness; no-bar intervals yield
  explicit empty `complete` pages; `sent_at_utc`/`observed_at_utc` are the provider-supplied
  source times (period; enforced by hub `publish` passing `observed_at_utc` into the wire header
  and by page equality checks in the tests).
- **History is stateless**: no cache at hub or SDK; a corrected re-read returns the provider's
  new value; only explicit finite errors (`HISTORY_*` codes) on failure.
- **Enforcement point for projections**: because the SDK deliberately forwards overlaps, the
  fail-closed boundary for any consumer bar projection is the *consumer*. This increment
  demonstrates it with the test-local `_FailClosedBarProjection` driven from real SDK events
  (identical → dedup once; conflicting → explicit raise, state untouched). The accepted
  Backtrader-side consumer proof is round-07B `ServiceReadProxy`
  (`HISTORY_LIVE_CONFLICT` / `HISTORY_BAR_CONFLICT`); it was **not re-run** here.

## Findings / gaps

- **Observed SDK-side behavior (documented, not a bug):** the SDK stream silently duplicates
  nothing and silently accepts nothing — it forwards duplicate and conflicting bar revisions
  verbatim; there is no SDK-side bar projection in which a conflict could be "silently accepted".
  Consumers must implement fail-closed conflict handling; nothing in this increment justifies a
  production change. If a future consumer relies on the SDK stream without its own projection
  guard, conflicting overlaps will reach it un-filtered — that is the honest boundary to record.
- `hub.publish` stores the caller's `observed_at_utc` into `WireHeader.sent_at_utc` (true
  source-time passthrough, verified end-to-end through real ZMQ CURVE), and `market.head`'s
  `latest_observed_at_utc` derives from the same value.
- History pages carry no epoch/cursor; seam epoch consistency is asserted on the live stream
  side (bootstrap epoch == event epoch(s); history identity consistency is via the exact
  `MarketHistoryRequest` key echo enforced by the client).
- **Deferred (per coordinator, out of scope):** BT feed-level seam node (07B already accepted,
  not re-run; would live in the Backtrader repo); multi-key/sparse-option coverage (sub-items
  1+4) and leg-skew module (sub-item 5) are other agent lines' files — untouched here.

## Proven vs NOT proven

Proven (this increment, real process chain): history→live seam gap/overlap math for one bar key
with epoch/cursor/identity consistency; provider source-time passthrough (exact, not wall clock);
explicit no-data for silent keys and no-bar intervals (no fabricated bars/freshness); stateless
history with corrected re-read returning provider values; SDK verbatim forwarding of duplicate and
conflicting overlaps; fail-closed dedup/conflict behavior at a consumer projection driven by real
events; clean subscription release (`starts,stops == (1,1)`).

NOT proven (deliberately out of scope): formal DIM02 acceptance (sub-items 1/4/5 unmerged);
multi-key or multi-timeframe seams; option-chain sparsity; leg time-skew readiness (another
agent's module); BT-feed-level behavior (07B proved separately, not re-run); no real-venue data.

## Resume point

This increment is COMPLETE and self-contained — nothing is half-finished. To re-verify, run the
exact command in `D:/temp/it10dim02seam-20261016-r1/run1/cmd.txt` (with the `ps -W` serialization
probe first). Remaining DIM02 scope is owned by other agent lines as listed under Deferred; the
final merge/registration of accepted DIM02 rounds is a coordinator step outside this file's scope.
