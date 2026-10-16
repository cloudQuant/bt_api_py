# ARB01 increment status — account-sharing fixture variants (2026-10-16)

Owner workstream: ARB01 (gapmap2 row 767; case ~60%). Successor must be able to resume from this file alone.

## Mission scope

Extend `D:/bt_api_py/tests/unit/test_service_account_integration.py` (GW01 round-38 accepted two-account
real-spawn/CURVE fixture; 1 passed baseline) with FIXTURE VARIANTS:

- (a) cross-exchange variant: two accounts on different synthetic venues (provider stays `synthetic`).
- (b) same-venue-same-symbol variant: two accounts on one venue sharing ONE instrument identity.
- Invariants asserted in BOTH: per-account provider connect/read exactly once (refreshed read only via
  the owning host), single active upstream (provider session) per account key while 2 strategies read
  the shared account, wrong-account access rejected (route-b `AMBIGUOUS_OR_MISSING_ROUTE`; unauthorized
  principal registration `DENIED`; unknown CURVE key `REQUEST_NOT_SENT`), exact close-once semantics.
- Optional cheap Host-level node: implemented as "dual-binding unqualified call rejected
  `AMBIGUOUS_OR_MISSING_ROUTE` before dispatch, zero provider reads" on strategy-one (bound to both
  accounts) against the live two-host fixture. NOTE: this rejection is the SDK runtime routing check
  (service_runtime.py:633-644) exercised against real hosts, not a new host-side code path.
- No market-hub dimension added here: `active_upstreams` in this file's vocabulary = per-account provider
  session count (connect-close). Market-key dedup (GW02 `active_upstreams==1`) is NOT re-derived in this run.

OWNERSHIP: only the account-integration test file is modified (+ helpers local to it). No commits.

## Files

- Modified: `D:/bt_api_py/tests/unit/test_service_account_integration.py`
- Status (this file): `D:/bt_api_py/.codex-tmp/arb01-status.md`
- QA runner (copied format): `D:/bt_api_py/.codex-tmp/iteration10-focused-qa.py`
- QA output dir (fresh): `D:/temp/it10arb01-20261016-r1/` (spec.json/command.json/stdout.log/stderr.log/
  junit.xml/exit.txt/source-before.json/source-after.json/result.json; result keys pytest_exit/
  sources_unchanged/heads_unchanged/stdout_sha256).

Repos pinned (branch dev): `D:\bt_api_py` (parent), `bt_api\bt_api_base`, `bt_api\bt_api_gateway`,
`bt_api\bt_api_transport_zmq`. NOTE parent HEAD advances via other workstreams; capture HEADs immediately
before the formal run; rerun in a new r2 dir if a foreign commit lands mid-run.

## Milestones

- [x] M0 recon (2026-10-16): test located (992 lines, accepted body + fixture). Runner format read
  (`.codex-tmp/iteration10-focused-qa.py`). Env: `C:\anaconda3\python.exe`; imports resolve to
  `bt_api/*/src` worktrees. Plan: parameterize fixture (account_keys + instrument per account),
  refactor scenario body into `_exercise_shared_account_variant`, add 2 variant tests + ambiguity node.
- [x] M1 edits complete (test file).
- [x] M2 dev smoke green (3 nodes, `3 passed, 1 warning in 20.97s`, 2026-10-16; warning = disabled
  pytest-asyncio config option, harmless).
- [x] M3 formal fixed-source QA: r2 ACCEPTED (see evidence below). r1 retained as invalidated run.
- [x] M4 final report + status update.

## Formal QA evidence

- r1 (RETAINED, INVALIDATED — do not cite as acceptance): output `D:/temp/it10arb01-20261016-r1/`;
  pytest_exit 0, `3 passed in 18.40s`, sources_unchanged true, stderr empty, BUT parent HEAD moved
  4e46ff01 -> 5ff3da11 mid-run (foreign commit) -> heads_unchanged false; runner exited 1 on its
  dependency guard. Artifacts kept untouched.
- r2 (ACCEPTED): authored spec `D:/bt_api_py/.codex-tmp/it10arb01-20261016-r2-qa/focused-qa.json`
  sha256 `5fd58f735a2416227db144006c2d8cf9f0c121d7a8a2d4b5b37260ff7f72f12e` (output spec.json bytes
  identical). Command (cwd `D:\bt_api_py`):
  `C:\anaconda3\python.exe .codex-tmp\iteration10-focused-qa.py .codex-tmp\it10arb01-20261016-r2-qa\focused-qa.json`.
  Output `D:/temp/it10arb01-20261016-r2/`: exit 0; stdout `3 passed, 1 warning in
  19.13s`, stdout_sha256 `d1c68d9aebc257c481e72b71b05180756ab58eeb695264c040e136255cfaf135`; stderr empty;
  junit tests=3 errors=0 failures=0 skipped=0 (18.737s); 37 source pins before==after; 4 repo heads
  unchanged (parent `5ff3da11b9541fc7b94c12d12ba0ed910525eba1`, base `b4aabd1e17b9c7252fa7b4d266932087d99b5517`,
  gateway `59b164ab7f89c6428ae6546103aad6058dab4c16`, transport `a397fdb460d2deb22e67ceb4963992ef63dbbb0e`).
- Nodes: `tests/unit/test_service_account_integration.py` (3 tests: original + cross-exchange variant +
  same-venue-same-symbol variant).
- New assertions in all variants: `route-ambiguous` (strategy-one dual-binding unqualified call ->
  `AMBIGUOUS_OR_MISSING_ROUTE` before wire/provider), per-account-key `active_upstreams` dict == {A:1, B:1},
  shared-instrument dict equality (variant b) / venue inequality (variant a), close-once
  (`connect-close==0` at shutdown), plus all original R06 assertions retained.
- No commits; only `tests/unit/test_service_account_integration.py` modified (+ .codex-tmp scratch/spec/
  status and D:/temp artifacts). ruff check/format clean (line-length 100).
- Python QA invocations: `C:\anaconda3\python.exe`; env PYTHONPATH=parent+base/gateway/transport `src`;
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1; conftest loaded (in-tree default; pytest-asyncio config warning only).

## Dev run command (replicating spec env)

cwd `D:\bt_api_py`; env PYTHONPATH=`D:\bt_api_py;D:\bt_api_py\bt_api\bt_api_base\src;
D:\bt_api_py\bt_api\bt_api_gateway\src;D:\bt_api_py\bt_api\bt_api_transport_zmq\src`,
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1, PYTHONNOUSERSITE=1; unset PYTHONPATH/
PYTHONHOME/PYTEST_ADDOPTS first. Command: `C:\anaconda3\python.exe -m pytest -q --no-header
-p no:cacheprovider tests/unit/test_service_account_integration.py`.

## Evidence level target

`FIXED_SOURCE_TWO_ACCOUNT_FIXTURE_VARIANTS_CURVE_SYNTHETIC_PROVIDER_NO_ACCOUNT` — synthetic providers,
no real account, no commits. Blocked/gaps: market-subscription cross-account combination (E3) and
Host group-leg negatives (ARB03/DIM05 chain) remain out of scope.
