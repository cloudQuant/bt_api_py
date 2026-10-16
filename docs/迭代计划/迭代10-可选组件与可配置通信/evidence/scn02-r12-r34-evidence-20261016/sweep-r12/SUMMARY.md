# SCN02 R1+R2 evidence — summary (2026-10-16)

Round: iteration-10 acceptance, SCN02 R1 (9-dir statement) + R2 (zero-I/O replay/dry sweep).
Evidence dir: `D:/temp/it10scn02-r12-20261016-r1/` (this dir). Never overwritten; reruns would use `*-r2` suffixes (none needed).

## Environment

- Interpreter: `C:/anaconda3/python.exe` = Python 3.11.5.
- `PYTHONPATH=D:/bt_api_py/bt_api_py;D:/bt_api/bt_api_base/src;D:/bt_api/bt_api_execution/src;D:/bt_api/bt_api_transport_zmq/src` (set for every run).
- Backtrader repo `D:/source_code/backtrader`: HEAD `b002afa2d` before AND after sweep (`git-baseline.txt`, `git-post-sweep.txt`); `git status --porcelain` identical before/after (6 entries, incl. 3 other-agent modified files untouched since 2026-10-11 — mtime check in `git-post-sweep.txt`). Worktree did NOT change during the sweep; no rerun needed.
- No repo writes by the sweep: replays redirected via `--evidence-dir` into this evidence dir; only `__pycache__/*.pyc` refreshed inside example dirs (bytecode cache, git-ignored). No credentials/secret values in artifacts (only env NAMES, masked `"***"` fields).

## Results table (one subfolder per dir)

| Dir (subfolder) | Zero-I/O mode run (exact command, cwd=dir unless noted) | Exit | Result | Evidence level | Caveats |
|---|---|---|---|---|---|
| 007_ctp | `C:/anaconda3/python.exe run_runtime.py` + `C:/anaconda3/python.exe -m backtrader_runtime.cli doctor --strategy-dir examples/unified/007_ctp/runtime` (cwd=repo root) | 0 / 0 | `LOCAL_REPLAY_PASS` (`admission_status=LOCAL_REPLAY_ONLY`, network 0/order_write 0) / offline `diagnostic` | SCN02-R2 increment: registered replay route (config-gate + no-action probe) | NOT CTP/SimNow/trading evidence; historical live/run.py rejection probes not in this increment (R5) |
| 012_1 | `run.py` / `run.py --replay --evidence-dir <E>/012_1/replay-evidence` / `run.py --live` | 0 / 0 / 2 | `DRY` / `FORMULA_CHECK_PASS` (network 0, order_write 0) / `BLOCKED live_observation_unavailable` (channel=observe) | SCN02-R2 increment: dry+replay (R0 formula fixture) + explicit observe-gap fact | replay is NOT market/profitability evidence; demo mechanical pair (needs external demo endpoints) NOT rerun — historical `MECHANICAL_DEMO_SMOKE_PASS` ×2 (2026-10-10) referenced only |
| 012_2 | same 3 commands as 012_1 | 0 / 0 / 2 | `DRY` / `FORMULA_CHECK_PASS` / `BLOCKED live_observation_unavailable` | SCN02-R2 increment (same boundaries) | `hft_label=event_driven`, HFT level stays `HFT_FAILED/NOT_ADMITTED`; historical `MECHANICAL_DEMO_SMOKE_PASS` ×1 (2026-10-10) |
| 013_1 | `run.py` / `run.py --replay --evidence-dir <E>/013_1/replay-evidence` | 0 / 0 | `DRY` / `LOCAL_REPLAY_PASS` (scenario profitable, pair_actions 4, network 0/order_write 0) | SCN02-R2 increment: dry + frozen-fixture replay | `LIVE_OBSERVE_PASS` still zero-hit repo-wide (R3); replay != fill evidence; stderr carries 2.3MB local TradeLogger JSONL session log (session_started/stopped) |
| 013_2 | same 2 commands as 013_1 | 0 / 0 | `DRY` (rb2701/rb2705) / `LOCAL_REPLAY_PASS` | SCN02-R2 increment | same as 013_1; README L26 bt-runtime path typo noted |
| 013_3 | `run.py` / `run.py --replay --evidence-dir <E>/013_3/replay-evidence` / `run.py --replay --scenario trend --evidence-dir <E>/013_3/replay-trend-evidence` | 0 / 0 / 0 | `DRY` / `LOCAL_REPLAY_PASS` (no_signal, orders 0, sdk_write_requests 0) / `LOCAL_REPLAY_PASS` (trend) | SCN02-R2 increment: dry + 2 replay scenarios; rich local artifact set (manifest/preflight/reconciliation/risk) | drill (observed/admission write path) NOT run (R6); historical `fill_round_trip` PASS 2026-10-09 + later `FAIL lifecycle_only` kept as-is |
| 014_1 | `run.py` / `run.py --replay --evidence-dir <E>/014_1/replay-evidence` | 0 / 0 | `DRY` (`live_tier.status=STRICT_ADAPTER`) / `LOCAL_REPLAY_PASS` (scenario eligible) | SCN02-R2 increment | replay keeps `FILL_TIMING_UNKNOWN`, 0 confirmed fills, `LOCAL_BASKET_FLAT_UNVERIFIED`; three-leg write round-trip still unaccepted (R6; needs trading session) |
| 014_2 | `run.py` / `run.py --replay --evidence-dir <E>/014_2/replay-evidence` / `run.py --live --allow-write` | 0 / 0 / 2 | `DRY` (`live_tier.status=OBSERVATION_ONLY`) / `LOCAL_REPLAY_PASS` (no_edge) / `BLOCKED write_tier_not_implemented_for_decision_only_strategy` | SCN02-R2 increment: dry + replay + write-refusal-by-contract fact | live observation (needs SimNow) NOT run (R4); historical live observations UNKNOWN×2/BLOCKED×2 (2026-10-09), no PASS |
| 015 | `run.py` / `run.py --replay --evidence-dir <E>/015/replay-evidence` / `run.py --live --allow-write` | 0 / 0 / 2 | `DRY` (`hft_status=FAIL/NOT_ADMITTED`) / `LOCAL_REPLAY_PASS` (valid_cohort, hft FAIL/NOT_ADMITTED) / `BLOCKED` (same code) | SCN02-R2 increment | live tick observation (needs SimNow) NOT run (R4); historical `OBSERVATION_PASS` 2026-10-09; HFT never claimed by name |

`<E>` = `D:/temp/it10scn02-r12-20261016-r1`. Exact commands + env per dir also in each subfolder's `cmd-run.txt` (007: `cmd-run.txt`).

## Subfolder contents

- `cmd-run.txt` — cwd, PYTHONPATH, exact command lines.
- `<mode>.stdout.txt` / `<mode>.stderr.txt` / `<mode>.exitcode.txt` per run.
- `replay-evidence/` (`replay-trend-evidence/` for 013_3) — replay report.json / full artifact set written via `--evidence-dir`.
- `git-baseline.txt`, `git-post-sweep.txt` — HEAD + porcelain snapshots (+ hashes/mtimes of other-agent files).
- `SCN02-9目录读写能力说明-20261016.md` — R1 delivery-ready 9-dir statement (path, demonstrates, read/write contract, evidence status, SCN02 clause mapping).

## Honest evidence boundary

- All runs self-reported `network: 0` / `order_write: 0` (or equivalent counters) and stayed on documented zero-I/O paths; no SimNow/demo endpoint was contacted; no write-capable mode was executed.
- This sweep is **SCN02 increment evidence, NOT SCN02 formal PASS**. `LIVE_OBSERVE_PASS` (013_1/013_2/013_3/014_1), 014_2/015 live observation, demo smoke rerun, admitted drills (013_3/013_1/013_2/014_1), and the 007 historical-flag rejection facts remain open (R3–R6); formal adjudication = R7.
- No blockers: every one of the 9 dirs has a documented zero-I/O mode and it ran green.
