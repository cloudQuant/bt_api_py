# iteration-10 DIM02 legs increment — delivery report (2026-10-16, r1)

Scope (recon `D:/bt_api_py/.codex-tmp/dim02-recon.md`): §1 table sub-item (5) 组合腿时间偏差;
§3 last row "腿偏差模块"; §5 item 3; §6 open-point item 3 (module naming; server-side readiness
projection explicitly NOT in this round). Deliverable: new SDK module + unit + one spawn node.
No git staging/committing was performed; git was used read-only.

## 1. Files created (sha256)

| File | Lines | sha256 |
|---|---|---|
| D:/bt_api_py/bt_api_py/service_market_legs.py | 240 | 1d6456c25406dd034cb098a71a3e4e412202b784b93e3f1083295bec6571edf2 |
| D:/bt_api_py/tests/unit/test_service_market_legs.py | 339 | d99776ab16a36b2adff8ca9ec992a3cc9f69ce90180a49a56a0f37c967a2c53b |
| D:/bt_api_py/tests/unit/test_service_market_dim02_legs.py | 703 | a9de557c1321217304e5606e3b3b3622026801a15bf1f479c3d55cb07718a4b4 |
| D:/bt_api_py/.codex-tmp/dim02-legs-status.md | — | 7341bcbf98d696b35b60f87cbde8a4a7658f719e8e79dec125bd853ec0a1c75c |

No existing module was modified: `git status --porcelain` shows the three files as untracked and
no staged changes; the only modified entries (`bt_api/bt_api_execution`, `bt_api/bt_api_risk`
gitlinks) pre-existed from other work lines. Sibling files
`tests/unit/test_service_market_dim02_integration.py` and `tests/unit/test_service_market_dim02_seam.py`
(other agents) were not touched. No `git add/commit/stash/checkout/reset/clean` was run.
ruff (`C:/anaconda3/python.exe -m ruff check`) passes on all three files and `ruff format`
was applied only to the three new files (repo style: existing files are format-clean).

## 2. Module public API (bt_api_py/service_market_legs.py)

Read-only composition over the EXISTING `market.head` RPC (`runtime.call(mount, "market.head",
{"key": key.to_dict()})`); no new wire operation, no server change.

- `ServiceMarketLegsError(RuntimeError)` — finite codes with optional `cause_code`.
  Codes: INVALID_RUNTIME, INVALID_MAX_TIME_SKEW, INVALID_CLOCK, INVALID_LEGS, EMPTY_LEG_GROUP,
  INVALID_LEG_BINDING, INVALID_LEG_MOUNT, INVALID_LEG_KEY, DUPLICATE_LEG, INVALID_CLOCK_VALUE,
  LEG_HEAD_READ_FAILED, INVALID_HEAD_REPLY, INVALID_OBSERVED_AT.
- `MarketLegBinding(mount: str, key: MarketSubscriptionKey)` — one existing leg.
- `MarketLegStatus(mount, key, head, observed_at_utc, age_seconds, no_data, ready)`.
- `MarketLegGroupStatus(ready, max_time_skew_seconds, legs)`.
- `ServiceMarketLegGroup(runtime, legs, *, max_time_skew_seconds, utc_clock=UTC-now)`;
  properties `legs`, `max_time_skew_seconds`; method `review() -> MarketLegGroupStatus`.

Semantics implemented (the acceptance spec): (a) head==0 or observed_at None -> explicit
`no_data`, ready False, age None — never fresh; (b) a leg with age > declared skew makes the
group not ready and other legs updating does not change that; (c) when every leg is within the
skew the group is ready; (d) all judgments use provider source times (`latest_observed_at_utc`)
plus the injected clock — the logic never reads a wall clock (default injected clock is
`datetime.now(UTC)`, overridable). Head replies are validated strictly (exact field set,
stream_id == key.stream_id, epoch/head/oldest bounds); unparsable or naive observed times are
rejected; a head read failure aborts the review (fail closed) instead of guessing a verdict.

## 3. Runs, commands, exit codes (evidence in D:/temp/it10dim02legs-20261016-r1)

Invocation pattern per run (cmd/stdout/stderr/exit saved separately, never overwritten):
```
cd /d/bt_api_py
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src" C:/anaconda3/python.exe -m pytest <target> --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=D:/temp/it10dim02legs-20261016-r1/<run>/basetemp -q
```

| Run | Target | Result | Exit |
|---|---|---|---|
| unit1 | tests/unit/test_service_market_legs.py (pre-format) | 9 passed, 1 warning, 0.75s | 0 |
| spawn1 | tests/unit/test_service_market_dim02_legs.py (first) | 1 failed — test-side expectation (hub counts one registration ref per subscribed key: 2 for one registration + two legs); production module unchanged | 1 |
| spawn2 | same after test fix (pre-format) | 1 passed, 3.30s | 0 |
| unit2 | tests/unit/test_service_market_legs.py (final artifact) | 9 passed, 1 warning, 0.75s | 0 |
| spawn3 | tests/unit/test_service_market_dim02_legs.py (final artifact) | 1 passed, 1 warning, 3.06s | 0 |

The only warning is a pre-existing environment pytest-config notice
(`asyncio_default_fixture_loop_scope` unknown), unrelated to these files.
Serialization protocol observed: `ps -W | grep -i python` before each run; only two long-lived
daemons (Oct-13 ai-for-investor venv backend, Python312) were present throughout; a transient
`/c/anaconda3/python` PID 739 appeared once and exited within a 20s observation window (wait
recorded); unit2 (0.75s) ran unaffected; spawn3 started only after confirming no active runner.
After spawn3 the process table showed only the two daemons — no stray spawned processes, and the
node's cleanup path reported no forced/fallback cleanup.

## 4. What is proven

Unit (deterministic injected clock + fake runtime heads, exact RPC args recorded):
- silent legs in both forms (head 0/observed None, head>0/observed None, head 0/observed set) are
  explicit no_data, age None, not ready; group not ready;
- leg beyond skew -> group not ready; another leg updating further does not flip the group;
- stale leg catch-up inside skew -> group ready; as the reviewed instant advances both legs fall
  out of skew -> group not ready again (age semantics over time);
- ages derive exactly from provider source times (microsecond precision, offset-equivalent
  representations, negative age when source time is ahead of the injected clock);
- one head read per declared leg, in declared order, on the exact read-only `market.head` RPC;
- fail-closed: runtime errors -> LEG_HEAD_READ_FAILED (with cause code), malformed/short/extra
  head fields, negative head, bad oldest_sequence, non-string observed, unparsable or naive
  observed -> INVALID_HEAD_REPLY / INVALID_OBSERVED_AT; naive/non-datetime clock -> INVALID_CLOCK_VALUE;
- configuration validation: empty group, duplicate leg, bad mount/key/binding, invalid skew
  (negative/NaN/inf/bool/str), non-callable clock.

Real spawn node (real ServiceHost process + registry/NACK'd grants + CURVE-authenticated Zmq
PUB/SUB and RPC chain, synthetic deterministic two-leg provider with explicit provider-supplied
sent_at values):
- one registration, two real `ServiceMarketClient` leg subscriptions; provider per-key upstream
  counts starts {leg-a:1, leg-b:1}, active_upstreams 2 -> 0 after release, per-key stops
  {leg-a:1, leg-b:1}; client "ready" reported both leg stream ids and latest_event/incomplete
  bootstrap flags;
- group transitions across the real process chain: silent -> both legs no_data/not ready;
  leg-a fresh + leg-b 30s stale (skew 5s) -> group not ready; leg-a updates again -> group still
  not ready; leg-b catches up -> group ready; reviewed instant +31s with no new publishes ->
  group not ready; observed_at strings equal the exact provider sent_at values passed through
  (no local-time substitution);
- clean shutdown to ("closed","closed",...), runtime/market states closed/not healthy.

## 5. What is NOT proven / deliberately out of scope

- No formal DIM02 PASS claim: this is only the sub-item (5) increment of the recon's minimal
  increment list; the DIM02 verdict belongs to the coordinator after all five sub-items.
- Server-side readiness projection is explicitly out of scope (recon §6 item 3 alternative); the
  module is client-side read-only composition, no new wire operation, no server change.
- No client-health gating in the module (recovery_required/resync_required of the leg's market
  client): a caller may compose that check itself; not implemented, not tested.
- No cross-flight atomicity: legs are read sequentially, so one review is not a single atomic
  snapshot across streams (inherent to composing separate head RPCs).
- Real venue/option-chain legs and true exchange time skew are not covered (VENUE/DIM08 scope);
  the spawn node uses the synthetic deterministic provider, as the acceptance requires.
- Not proven here: that strategy-side consumers act on the group verdict (no strategy loop
  integration in this increment); no throttling/caching (one head RPC per leg per review, by design).

## 6. Residual gaps / resume point

Work is complete for this increment. If re-verification is needed, rerun the unit2 and spawn3
commands in section 3 (one suite at a time, check `ps` first), or re-read
D:/bt_api_py/.codex-tmp/dim02-legs-status.md for the exact resume instructions.
