# SCN02 R3+R4 evidence — summary (2026-10-16)

Round: iteration-10 acceptance, SCN02 R3 (SimNow zero-write observe sweep 013_1/013_2/013_3) + R4 (014_1 observe; 014_2/015 observation-only layers).
Evidence dir: `D:/temp/it10scn02-r34-20261016-r1/` (this dir). Extends — never overwrites — R1+R2 evidence in `D:/temp/it10scn02-r12-20261016-r1/`.
This is **increment evidence, NOT SCN02 formal PASS** (formal adjudication = R7).

## Environment

- Interpreter: `C:/anaconda3/python.exe` = Python 3.11.5 (all runs).
- `PYTHONPATH=D:/bt_api_py/bt_api_py;D:/bt_api/bt_api_base/src;D:/bt_api/bt_api_execution/src;D:/bt_api/bt_api_transport_zmq/src` (set for every run; as in R1+R2 — note only `D:/bt_api_py/bt_api_py` exists on disk, the other entries are inert). No proxy variables set in the shell (checked: unset) — SDK connects directly; run.py reads `.env` by NAME only and never injects values into the process env.
- Backtrader repo `D:/source_code/backtrader`: HEAD `b002afa2d` (branch `dev`) before AND after the sweep (`git-baseline.txt`, `git-after.txt`, identical incl. `git status --porcelain` 6 entries — other agents' edits untouched). No tracked-file edits, no commits; runs redirected via `--evidence-dir` into this evidence dir (per-dir git-ignored `evidence/` untouched).
- **Credential safety**: NO credential values in any artifact (mask names only). Automated scan of all 39 artifacts against all 39 secret-like `.env` values (in-memory, values never printed): 0 real hits — the single substring match is the non-secret enum word `direct` (`HONGYUAN_CONNECTION_MODE=direct`) occurring as ordinary English text; 0 occurrences of any password/user/token field name. Credentials were passed by the runs to the SDK only.
- **Write safety**: every run below is observation/replay only. NO `--allow-write` was ever passed; no order submission; no account writes. 014_2/015 would refuse `--allow-write` by contract (decision-only strategies).

## Results table (R1+R2 baseline columns + R3/R4 observe columns)

| Dir | R1+R2 baseline (zero-I/O; r12 evidence dir) | R3/R4 run (cwd=dir; NO --allow-write) | Exit | Result | Evidence level | Caveats |
|---|---|---|---|---|---|---|
| 007_ctp | `LOCAL_REPLAY_PASS` registered replay + offline doctor | not rerun | — | — | R2 (unchanged) | not part of R3/R4 scope |
| 012_1 | `DRY` / `FORMULA_CHECK_PASS` / `BLOCKED live_observation_unavailable` | not rerun | — | — | R2 (unchanged) | Demo mechanical pair not rerun (R5) |
| 012_2 | same as 012_1 | not rerun | — | — | R2 (unchanged) | R5 scope |
| 013_1 | `DRY` / `LOCAL_REPLAY_PASS` (profitable) | `run.py --live --evidence-dir <E>/013_1_observe/run-evidence` | 0 | `LIVE_OBSERVE_PASS` (first repo-wide; channel=observe) | **SCN02-R3 increment**: real SimNow zero-write session+query observation | zero order writes (write_health armed/frozen/active_orders all false/0); boundary `SESSION_AND_QUERY_OBSERVATION_ONLY_NOT_MARKET_OR_PROFITABILITY_EVIDENCE`; drill remains R6 |
| 013_2 | `DRY` / `LOCAL_REPLAY_PASS` | same command pattern → `<E>/013_2_observe` | 0 | `LIVE_OBSERVE_PASS` | **SCN02-R3 increment**: same channel | as above; "highfreq" name ≠ HFT evidence |
| 013_3 | `DRY` / `LOCAL_REPLAY_PASS` ×2 scenarios | same → `<E>/013_3_observe` | 0 | `LIVE_OBSERVE_PASS` | **SCN02-R3 increment**: single-contract observe | 93 instrument metadata rows read-only; admitted drill (clause-named) remains R6 |
| 014_1 | `DRY` / `LOCAL_REPLAY_PASS` | same → `<E>/014_1_observe` | 0 | `LIVE_OBSERVE_PASS` (3 distinct legs C/P/F) | **SCN02-R4 increment**: three-leg zero-write observe | three-leg write round-trip still unaccepted (R6); replay `FILL_TIMING_UNKNOWN` unchanged |
| 014_2 | `DRY` / `LOCAL_REPLAY_PASS` / `BLOCKED write_tier_not_implemented...` | `run.py --live --run-seconds 240 --evidence-dir <E>/014_2_observe/run-evidence` (+1 retry) | 3 + 3 | `UNKNOWN` ×2 — `run_error: ValueError: exchange must be an exact non-empty string` | **SCN02-R4 increment**: observation-only layer exercised; deterministic runtime gap archived (no PASS) | order_write 0; feed_evidence_fault null; feature_minutes 0; stderr empty; stale-clock path did NOT reproduce (fresh live non-synthetic mapping); historical stale-clock fact archived (2026-10-09, ~47.9h age); historical record: no PASS |
| 015 | `DRY` / `LOCAL_REPLAY_PASS` / `BLOCKED` (write) | `run.py --live --run-seconds 150 --evidence-dir <E>/015_observe/run-evidence` | 0 | `OBSERVATION_PASS` (ticks 222/8/1 per leg) | **SCN02-R4 increment**: observation-only tick-arrival layer; fresh TRUSTED_NOW_INVALID reject facts | reject_counts `TRUSTED_NOW_INVALID: 231`; cohort_decisions 0; `hft_status` stays `FAIL/NOT_ADMITTED`; order_write 0 |

`<E>` = `D:/temp/it10scn02-r34-20261016-r1`. Exact command lines per dir in each subfolder's `cmd-run.txt`.

## Subfolder contents

- `cmd-run.txt` — cwd, PYTHONPATH, exact command, explicit "NO --allow-write" note.
- `observe.stdout.txt` / `observe.stderr.txt` / `observe.exitcode.txt` (014_2: also `observe-retry.*`).
- `run-evidence/` (014_2: also `run-evidence-retry/`) — run.json (+ strategy_report.json where the run produced one) written by the example via `--evidence-dir`.
- `key-facts.txt` — condensed run facts per dir.
- `git-baseline.txt`, `git-after.txt` — HEAD + branch + porcelain before/after (identical).

## Honest evidence boundary

- 013_1/013_2/013_3/014_1 verified `LIVE_OBSERVE_PASS` on the new zero-write `run_live_observe` channel: real CTP SimNow login (read_only_ready), account/positions/orders/trades + instrument-metadata queries, session closed with Store.stop health PASS. The pass is session+query observation only — NOT market/profitability evidence; no fills, no orders.
- Zero writes are evidenced by the store-local gate projection (`armed=false, frozen=false, active_orders=0`; no arm calls on the observe path), not by a process-wide counter audit.
- 014_2: honest UNKNOWN — the observation layer currently terminates with an empty-exchange ValueError in the feed-sealed-bar evidence path (deterministic 2/2 at `b002afa2d`); stale-clock BLOCKED did not reproduce but remains in the historical record. No PASS exists for 014_2 live observation.
- 015: OBSERVATION_PASS with the expected honest reject facts (`TRUSTED_NOW_INVALID` ×231 = 100% of ticks: the direct CTP tick source carries no `ctp.quote.v2` attestation / decision-boundary clock). HFT stays `FAIL/NOT_ADMITTED` — not flipped, not claimed.
- Open for later rounds: admitted drills (013_3 clause-named, 013_1/013_2 two-leg, 014_1 three-leg first FILL round-trip) = R6; 012/007 boundary facts = R5; 014_2 live-observation fix + PASS = follow-up work item; formal SCN02 adjudication = R7.
