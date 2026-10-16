# DIM02 legs agent status (iteration-10)

Updated: 2026-10-16 (r1) — COMPLETE
Agent scope: recon §1 item (5) 组合腿时间偏差; §3 last row "腿偏差模块"; §5 item 3; §6 open-point item 3.

## Mission (done)
- NEW `D:/bt_api_py/bt_api_py/service_market_legs.py` — read-only composition over existing `market.head` RPC; no new wire ops; no edits to any existing module.
- NEW `D:/bt_api_py/tests/unit/test_service_market_legs.py` — 9 unit tests (injected deterministic clock, fake runtime objects).
- NEW `D:/bt_api_py/tests/unit/test_service_market_dim02_legs.py` — 1 spawn node (real ServiceHost + synthetic two-leg provider).
- Evidence: `D:/temp/it10dim02legs-20261016-r1` (unit1, unit2, spawn1, spawn2, spawn3 — each cmd/stdout/stderr/exit). Report: `report.md`.
- No git add/commit/stash anywhere; read-only git only. Sibling files (dim02_integration/dim02_seam) untouched.

## Results
- unit1 (pre-format): exit 0 — 9 passed.
- spawn1: exit 1 — first run exposed a wrong parent expectation (hub counts one registration ref per subscribed key: 2, not 1); test-side fix only.
- spawn2 (post-fix, pre-format): exit 0 — 1 passed.
- unit2 (final artifact): exit 0 — 9 passed in 0.75s.
- spawn3 (final artifact): exit 0 — 1 passed in 3.06s; clean process table after (no stray spawns; no fallback cleanup needed).
- ruff check: all 3 files pass; ruff format applied to match repo style (existing files are format-clean).
- Serialization: ps checked before every pytest run; only long-lived Oct-13 daemons present except one transient `/c/anaconda3/python` (PID 739) that exited within a 20s observation window (recorded wait); unit2 ran 0.75s unaffected; spawn3 ran after confirming no active runner.

## Final hashes (sha256)
- service_market_legs.py                 1d6456c25406dd034cb098a71a3e4e412202b784b93e3f1083295bec6571edf2
- tests/unit/test_service_market_legs.py d99776ab16a36b2adff8ca9ec992a3cc9f69ce90180a49a56a0f37c967a2c53b
- tests/unit/test_service_market_dim02_legs.py a9de557c1321217304e5606e3b3b3622026801a15bf1f479c3d55cb07718a4b4

## Resume point
Work complete. If re-verification is needed:
```
cd /d/bt_api_py
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src" C:/anaconda3/python.exe -m pytest tests/unit/test_service_market_legs.py --noconftest --import-mode=importlib -p no:cacheprovider -q
```
(and the same for test_service_market_dim02_legs.py, one suite at a time; check ps first)
