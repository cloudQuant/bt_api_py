# SCN02 014_2 live-observation framework fix — ROOT REPORT (iteration 10, r2; successor #2)

Date: 2026-10-16 (local +0800). Mission: complete dry/replay/live runs + honest report for the
`014_2_ctp_options_midfreq` empty-exchange framework fix. No commits/staging in any repo.
No `--allow-write` was ever passed by any run in this evidence chain; zero order writes.

## 1. Fix summary (predecessor, re-verified by successor #2)

- Repo: `D:/source_code/backtrader` @ dev `b002afa2da66ca6d14f1623280109be8e653a1b6`.
- File: `backtrader/stores/btapistore.py` only. sha256 before `9670cca751932c19241ac1a694513afd47a4514b1258bbbd73a88a07972a5ee1`
  → after `451b488fe1f6b1fad0a034dd6fb40e4c8f95829f0cdba3c190ed2c593392c19a` (+43/−1, 4 hunks; diff
  `btapistore-fix.diff`). Final sha256 re-verified byte-identical after all runs (this report).
- Mechanism: retain the parsed exchange per instrument at subscribe time and, when the venue tick
  carries an empty `ExchangeID`, fall back to the framework CZCE inference (mirrors
  `_normalize_ctp_instrument` CZCE branch), incl. a narrow CZCE option pattern
  `^([A-Z]{1,2})(\d{3})([CP])(\d{3,5})$` (blocks DCE/SHFE 4-digit-month codes; validated negatives
  UR2505C1900 / rb2510C3300 / m2501C2900).
- Why retention alone is inert: run.py passes bare codes (`dataname=code`), so `subscribe()` parses
  exchange `""`; the framework inference is the operative part. (KEY VERIFIED FINDING in status file.)
- Verification on record: `py_compile` OK; offline regex validation; in-process behavioral checks
  30/30 PASS (`behavioral-check-out.txt`).

## 2. Unit-test failure-list comparison (successor #2, task 2)

- Fixed tree: `unit-tests-out.txt` — 38 failed / 854 passed / 13 skipped / 2 errors (full stores suite).
- Pristine HEAD subset: `head-pristine-failing-subset.txt` — same failing subset re-run on a pristine
  HEAD checkout: 38 failed / 221 passed / 2 errors.
- Programmatic comparison: `failures-identical-check.py` → `failures-identical-check.txt`
  (script exit 0). Result: FAILED and ERROR **sets and order identical** (0 unique to either side,
  no duplicates, totals 40 == 40). **Conclusion: the fix introduces zero new test failures.**

## 3. Runs (successor #2, task 3) — exact commands, exit codes, outcomes

cwd for all: `/d/source_code/backtrader/examples/unified/014_2_ctp_options_midfreq`;
env: `PYTHONPATH=D:/bt_api_py/bt_api_py;D:/bt_api/bt_api_base/src;D:/bt_api/bt_api_execution/src;D:/bt_api/bt_api_transport_zmq/src`;
interpreter: `C:/anaconda3/python.exe` (Python 3.11.5). No proxy vars in shell. NO `--allow-write`.

| run | command (files) | exit | outcome |
|---|---|---|---|
| dry | `C:/anaconda3/python.exe run.py` (`0142-dry-cmd.txt`, `.stdout/.stderr/.exitcode`) | 0 | `status=DRY`, network 0, provider_sessions 0, effective_tier dry (cli_tier null, same as r1 dry) |
| replay | `C:/anaconda3/python.exe run.py --replay --evidence-dir .../0142-replay-evidence` (`0142-replay-*`) | 0 | `LOCAL_REPLAY_PASS`, run_id `replay-20261016T032158Z-062101ef`, scenario no_edge, network 0, order_write 0 |
| live | `C:/anaconda3/python.exe run.py --live --run-seconds 240 --evidence-dir .../0142-live-evidence` (`0142-live-*`) | 1 | `status=FAIL`, reason `no_complete_minute_window`, `run_error=null`, seconds 385.078, run_id `live-20261016T032210Z-ae1ea79c`, run.json + strategy_report.json in `0142-live-evidence/` |

Live run details: front pair selected index 1 (standard, whitelisted, score 164 ms; R34 selected
index 2 — selection is score-based), legs `CZCE.SA701 / CZCE.SA701C1080 / CZCE.SA701P1080`;
preflight trading_day 20261016, metadata OK (SA701 tick 1.0/mult 20; options tick 0.5/mult 20),
account_rows 1; clock live non-synthetic (`czce-live-observe:20261016`); cleanup `closed`;
stderr empty (0 lines); `external_request_counts.order_write = 0`; observation: decisions 0,
feature_minutes 0, feed_evidence_fault null. Exit codes: EXIT_PASS=0, EXIT_FAIL=1, EXIT_BLOCKED=2,
EXIT_UNKNOWN=3 (run.py:47).

## 4. Primary fix verdict: SUCCESS (empty-exchange ValueError eliminated)

- Pre-fix (R34 + r1, same HEAD): every live attempt terminated with
  `ValueError: exchange must be an exact non-empty string` at the first sealed bar (66–102 s,
  exit 3 UNKNOWN, barrier.py `_text`).
- Post-fix: `run_error = null`; the run survived the full 240 s window; strategy_report.json shows
  7 closed minute bars were **sealed and delivered** to the strategy
  (`minute_events`: 7 × `FEED_CLOSED_BAR_PROVENANCE_REQUIRED`, origin `notify_bar`).
- Proof the exchange was filled: `BarEvidence` construction validates `exchange` non-empty at
  construction time (barrier.py:73 via `__post_init__` :459; built at run.py:493-495 from the sealed
  BarEvent). Pre-fix this raised the ValueError *before* the strategy ever saw a bar; post-fix all 7
  constructions succeeded, so the sealed bars carried a non-empty exchange (CZCE) ⇒ the store
  fallback fired as designed.

## 5. Second latent blocker: FIRED exactly as predicted; reported, NOT fixed (out of 1-file scope)

- Observed final outcome: `FAIL / no_complete_minute_window` (run.py:1071-1074), exit 1, with
  feature_history `[]`, feature_minutes 0. The rejection reason is recorded (not an exception):
  strategy_report.json `minute_events`/`rejections` = 7 × `FEED_CLOSED_BAR_PROVENANCE_REQUIRED`.
  There is no traceback anywhere (stderr empty, run_error null) — these arrays **are** the exact
  error lines.
- Exact chain (verified in code this session):
  1. Fed feed gets bare code: run.py:913 `dataname=code` ("SA701"); `run.py:924 adddata(feed, name=full_symbol)`
     only sets `data._name`.
  2. Sealed identity is the bare dataname: btapifeed.py:1484-1485 `BarEvent(symbol=self._dataname, exchange=...)`.
  3. Strategy's evidence map is keyed by FULL contract symbols: ctp_options_midfreq_strategy.py:674-681
     (`_feed_data_by_symbol = {symbol: self.datas[index] ...}` over `contracts` = CZCE.SA701 etc.).
  4. notify_bar (:986) → `_feed_bar_has_expected_provenance` (:944-956) does exact
     `self._feed_data_by_symbol.get(evidence.symbol)` → miss (bare vs full) → reject :987-990.
- Why not fixed: requires a change in btapifeed.py (sealed identity) or run.py wiring (map keying) —
  outside this round's single-file (btapistore.py) scope, and a feed-side symbol qualification would
  change sealed identity for **every** existing live feed. Not trivially resolvable within
  btapistore.py; per mission constraint it is reported, not attempted.

## 6. Repo hygiene / evidence pin

- HEAD `b002afa2da66ca6d14f1623280109be8e653a1b6` (dev) before and after all runs
  (`run-preflight-porcelain.txt`, `run-postrun-porcelain.txt` — identical). My runs created no new
  repo files (all outputs redirected to this evidence dir).
- `btapistore.py` final sha256 `451b488fe1f6b1fad0a034dd6fb40e4c8f95829f0cdba3c190ed2c593392c19a`
  — byte-identical to the verified fix (`run-postrun-sha256.txt`).
- Porcelain delta vs predecessor's recorded baseline (`git-porcelain-after.txt`): exactly ONE extra
  entry NOT authored by successor #2 — `backtrader/strategy.py` modified (+1 line
  `from decimal import Decimal`, mtime 2026-10-16 11:19:24 +0800, sha256
  `6c085eb5dc8acfaec76d86d8ba5fd684b97e6d6430cfac345adf32fda44c7372`, stable across my runs).
  It appeared **concurrently** after the predecessor's 11:14 baseline snapshot (external activity;
  likely another agent on this shared repo). Left strictly untouched; must not be attributed to
  this mission nor committed blind.
- Pre-existing dirty files untouched (present in baseline): `backtrader/stores/service_execution_adapter.py`,
  `backtrader_runtime/ctp_configured_front_check.py`, `backtrader_runtime/ctp_simnow_operator.py`,
  `tests/unit/runtime/test_ctp_simnow_config_operator_route.py`, untracked `.codex-tmp/`,
  `docs/_internal/opts/.../evidence/`, `tests/integration/test_managed_paper_v2_fresh_process_recovery.py`,
  and the odd ` D 0` entry (present in baseline before AND after). None touched.
- Zero-write: `order_write = 0` in live stdout/run.json; `--allow-write` never passed anywhere.
- Credential safety: `secret-scan.py`/`secret-scan.txt` (strict scope PASS) +
  `secret-collision-analysis.py`/`.txt`. All cross-check hits resolved as either the two public
  SimNow placeholder constants hardcoded in the repo (CTP_APP_ID = `simnow_client_test`,
  CTP_AUTH_CODE = 16 zeros — the .env uses the public demo defaults) or embedded-only numeric
  collisions inside synthetic fixture data (standalone occurrences: 0). No real credential value
  appears in any run artifact. No secret values were written to any file.

## 7. Remaining gaps

- 014_2 live observation still has **no PASS** anywhere in history: the second blocker must be fixed
  in feed/example scope (owner decision: change sealed BarEvent identity or evidence-map keying),
  then the live 240 s run must be repeated. The zero-write contract is unaffected either way.
- Front-pair selection is score-based and varies run to run (index 1 now, index 2 in R34) — env
  variance, not a regression.
- The CZCE inference is intentionally narrow (CZCE 3-digit months only); venues with empty
  ExchangeID outside CZCE remain uncovered (mirrors `_normalize_ctp_instrument`).
- Ownership/context of the external `backtrader/strategy.py` edit is unknown.

## 8. Evidence index (this directory)

- `unit-tests-out.txt`, `head-pristine-failing-subset.txt`, `head-checkout/` — unit-test diligence (predecessor).
- `failures-identical-check.py` / `.txt` — programmatic identical-failure-list proof (successor #2, exit 0).
- `btapistore-fix.diff`, `btapistore-sha256-{before,after}.txt`, `git-porcelain-{before,after}.txt` — fix + baseline (predecessor).
- `behavioral-check.py` / `behavioral-check-out.txt` — 30/30 in-process checks (predecessor).
- `0142-dry-cmd.txt` / `0142-dry.{stdout,stderr,exitcode}.txt` — dry run (exit 0, DRY).
- `0142-replay-cmd.txt` / `0142-replay.{stdout,stderr,exitcode}.txt` / `0142-replay-evidence/report.json` — replay (exit 0, LOCAL_REPLAY_PASS).
- `0142-live-cmd.txt` / `0142-live.{stdout,stderr,exitcode}.txt` / `0142-live-evidence/{run.json,strategy_report.json}` — live (exit 1, FAIL/no_complete_minute_window, run_error null).
- `run-preflight-*` / `run-postrun-*` — porcelain + hash snapshots around the runs (identical).
- `secret-scan.py` / `secret-scan.txt`, `secret-collision-analysis.py` / `.txt` — credential-safety evidence.
- This file: `root-report.md`.
