# DIM02 incr-1 Independent Review — FINAL (2026-10-16)

Reviewer: read-only; no repo edits/stage/commit; no GW01 re-runs.
Frozen evidence (untouched): D:\temp\it10dim02-20261016-r1
Review outputs: D:\temp\it10dim02-review-20261016-r1  |  VERDICT: ADOPT

## Evidence table
| Check | Result |
|---|---|
| sha256 deliverable (claimed 9381252942...e5e46) | MATCH; unchanged before/after runA+runB |
| runA (fresh) 12:26:30->12:26:42 +0800 | exit 0, "2 passed, 1 warning in 11.61s", stderr 0B |
| runB (fresh) 12:26:51->12:27:03 +0800 | exit 0, "2 passed, 1 warning in 12.04s", stderr 0B |
| Preserved run2b / run2c | exit 0 / 0, "2 passed" 10.91s / 10.76s; stdout differs ONLY in duration token |
| run2a (older revision, exit 1) | stale expectation (actual hub head 3, expected 2); file rewritten 12:13:39 before run2b 12:13:49 |
| run1q collect-only | exit 0, exactly the 2 claimed node ids |
| Command shape | bash, cwd /d/bt_api_py, PYTHONDONTWRITEBYTECODE=1, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONPATH=D:/bt_api_py + bt_api_base/execution/risk/transport_zmq src, C:/anaconda3/python.exe -m pytest <file> --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=<run>/basetemp -q |
| Process hygiene | no spawned leftovers after runs; only 2 resident Oct-13 daemons (ai-for-investor venv, Python312) |
| Serialization | wait#1 120s (PID 1350), wait#2 120s (PIDs 1732/86608); inline ps guard in runA/runB: clean at start; no foreign runner overlapped |
| git status | DIM02-incr1 = only untracked tests/unit/test_service_market_dim02_integration.py. Other agents: M bt_api/bt_api_execution, M bt_api/bt_api_risk (dirty submodule worktrees); M tests/unit/test_load01_hundred_strategy_30min.py (mtime 12:25:01, 0 dim02 refs, appeared mid-review); untracked dim02 legs/seam + service_market_legs.py, "0", "%SystemDrive%/" |
| GW01 defect | pre-existing in 4d5cebf5: tests/unit/test_service_market_integration.py:903 epoch node asserts ready[2:5] while client ready tuple has account_ref at [2]; 4d5cebf5 fixed the ten-clients node to ready[3:6] but not the epoch node. run1p (12:09, before DIM02 file final mtime 12:13:39) and run3 fail deterministically on that line. Independent of DIM02 (other target, --noconftest, not imported). |

## Assertion-quality (all claimed semantics asserted in code)
- Real spawn 1 host + 3 clients: subprocess spawn context; registration-id uniqueness; is_alive checks; bootstraps per key.
- 6 keys (spot trades, bar 1m/5m, options C105 live, C110/C115 silent): asserted stream_id uniqueness at module level.
- Per-key upstream counts: key_starts==1 all; key_stops per liveness; provider raises on duplicate/unknown handle.
- Dual level: active_upstreams==6 vs open_connections==5; merge group "connection:spot-bars"=(1,2); connection_starts/stops==1.
- Projection independence: per-key stream_id/epoch/head/observed; epochs unique across keys; key3 seq restarts to 1 while key0 at 2.
- Cross-key zero cross-stream: marker sweeps assert no match tuple AND zero queue depth.
- Silent keys: head 0 / observed None everywhere; bootstrap 0/0; test-side join node marks no_data and raises on fabricated freshness.
- Last-release cascade asserted at each step: 6->5->3->0; final key/connection stops all exactly 1; open_connections ().

## Determinism hazards (reviewed; none blocking)
- No fixed ports (ephemeral probes; small TOCTOU close->bind window, fail-loud).
- Bounded sleeps/deadlines: 0.4s join warmup; 6s collect deadline; 0.6/0.8s sweeps; ZMQ slow-join fail-loud, not silent.
- Cleanup: bounded join + terminate/kill fallback + assert no fallback used; pipes/processes closed.
- Third run not required: no instability in runA/runB (or preserved run2b/run2c).

## Verdict
ADOPT. Caveats: "identical final bytes" (run2b/2c) is overstated (duration token differs); no sha256
snapshot preserved in evidence dir (revision identity inferred from mtime chain 12:13:39 < run2b 12:13:49
and unchanged since); GW01 defect is out of DIM02-incr1 scope (GW01 follow-up).
