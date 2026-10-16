# SCN02 R5 boundary-facts evidence — report (2026-10-16)

**This is a FACTS round report. It is NOT a formal SCN02 PASS claim.** No SCN02 adjudication is
made here; grading stays with the R7 adjudication round. Two of the four mission items were
already captured byte-equivalently in the accepted R1–R4 archive and are cited (not re-run)
per the no-duplicate rule; the remaining item (007 boundary) was run fresh.

- Evidence root (created this round, never overwritten): `D:/temp/it10scn02-r5-20261016-r1/`
- Repo for runs: `D:/source_code/backtrader`, branch `dev`, HEAD `eb03f91e0277f0f98bce8bfd3151c08ad8867fe7`
- Interpreter: `C:/anaconda3/python.exe` (3.11.5); `PYTHONPATH=D:/bt_api_py/bt_api_py;D:/bt_api/bt_api_base/src;D:/bt_api/bt_api_execution/src;D:/bt_api/bt_api_transport_zmq/src` for all runs
- Read-only git: `git-baseline.txt` vs `git-post.txt` — porcelain identical (pre-existing state:
  ` D 0`, 7 modified other-agent files, 3 untracked incl. `.codex-tmp/`) and all 11 entry-file blob hashes identical.
  No repo file modified by any run; no write flags used anywhere; no credentials read.
- Machine check before runs: only two long-idle python processes (since Oct 13), no active heavy
  suite; no waiting needed (all runs sub-second).
- Raw artifact rule honored: per run `stdout` / `stderr` / `exitcode` as separate files, failures kept raw.

---

## Item 1a — 012_1_midfreq_cross_exchange `run.py --live` (no write flag): CITED

Fact already in the accepted R1+R2 chain (`root-adjudication.md`: "GO（增量归档级）"; `sweep-r12/SUMMARY.md` row 012_1).

- Exact command (archived `sweep-r12/012_1/cmd-dry.txt`): `C:/anaconda3/python.exe run.py --live`
  (cwd `examples/unified/012_1_midfreq_cross_exchange`, same PYTHONPATH pattern).
- Exit code: **2** (`live-probe.exitcode.txt`). stdout JSON key line (`live-probe.stdout.txt`):
  `{"channel": "observe", ..., "mode": "live", "reason": "live_observation_unavailable", "status": "BLOCKED", "write_grant_result": "not_requested"}`
  with `entry_tier = {cli_tier: live, effective_tier: live, tier_selection_reason: cli_requested}`;
  stderr empty. Nothing connected or ordered.
- What it proves: the explicit observe-gap BLOCKED fact — maps to **recon row 2** and R5 rec item 1.
  The dir README (line 96) documents exactly this: no write grant → `BLOCKED / live_observation_unavailable`
  (`channel: observe`, `write_grant_result: not_requested`). No `--allow-write` used; no re-run in R5.
- Citation file: `01a-012_1-citation/CITATION.txt` (full archived paths inside).

## Item 1b — 012_2_event_driven_cross_exchange `run.py --live` (no write flag): CITED

Same accepted R2 capture; archived `sweep-r12/012_2/live-probe.*` + `cmd-run.txt` (cmd3 observe-probe).

- Exact command: `C:/anaconda3/python.exe run.py --live` (cwd `.../012_2_event_driven_cross_exchange`).
- Exit code: **2**; stdout JSON byte-identical BLOCKED payload (`live_observation_unavailable`,
  `channel: observe`, `write_grant_result: not_requested`); stderr empty.
- Maps to **recon row 3** / R5 item 1. README line 99 documents the same fact. HFT boundary untouched
  (dir stays `hft_label=event_driven` / `HFT_FAILED NOT_ADMITTED` per R2 SUMMARY).
- Citation file: `01b-012_2-citation/CITATION.txt`.

## Item 2a — 007_ctp `run_runtime.py` historical-flag rejection: FRESH capture

Evidence: `02a-007-run_runtime-historical-flags/` (cmd-run.txt + 4 × stdout/stderr/exitcode).
cwd `examples/unified/007_ctp`, mission env.

| cmd | exit | stderr key line |
|---|---|---|
| `run_runtime.py --help` | 0 | usage lists only `[-h] [--runtime-dir RUNTIME_DIR]` |
| `run_runtime.py --config runtime/config.yaml` | 2 | `run_runtime.py: error: unrecognized arguments: --config runtime/config.yaml` |
| `run_runtime.py --mode live --all` | 2 | `run_runtime.py: error: unrecognized arguments: --mode live --all` |
| `run_runtime.py --dry-run` | 2 | `run_runtime.py: error: unrecognized arguments: --dry-run` |

What it proves: every historical flag class named in the 007 README (`--config`, `--dry-run`,
`--mode`, `--all`) is rejected by the registered route before any runtime resolution — maps to
**recon row 1**, step ②. The no-arg `run_runtime.py` `LOCAL_REPLAY_PASS` fact is already in the
accepted R2 chain (`sweep-r12/007_ctp/replay-route.*`) and was not re-run.

## Item 2b — 007_ctp legacy fail-closed entries: FRESH capture (with a finding)

Evidence: `02b-007-legacy-entries/` (flag-reject/, variants/, no-arg-alias/, fence-path-math.txt,
supplementary-repo-root/, in-process-main/; all documented in its cmd-run.txt).

**As-invoked facts (mission-exact env):**

1. All **10** `ctp_*_examples/{backtest,live}/run.py` with `--config single_symbol.yaml` →
   exit **1**, stdout empty, stderr traceback ending
   `ModuleNotFoundError: No module named 'backtrader_runtime'` (run.py line 17). Uniform across all five families.
2. Flag variants on `ctp_bbroker_5s_examples/live/run.py` (`--dry-run`, `--mode live`, `--all`,
   `--subprocess-child`) → same exit 1 / ModuleNotFoundError.
3. No-arg invocation of `ctp_bbroker_5s_examples/{backtest,live}/run.py` → same exit 1 (the README's
   "no-argument invocation routes to the registered runtime" alias does NOT reach the runtime).

**Root cause (read-only diagnostic, `fence-path-math.txt`):** each shell's fence computes
`parents[3] / "runtime"` → `examples/unified/runtime` (does not exist; the correct `parents[2]`
target `007_ctp/runtime` does exist) and inserts `D:/source_code` on sys.path (no
`backtrader_runtime` there) instead of the checkout root. The file was added by the iteration43
relocation `db5ad2b21` ("refactor(iteration43): relocate dual-channel examples under
examples/unified"). Effect: still fail-closed (nothing executed, no framework/provider import,
no network, no order path), but the documented rejection JSON is unreachable under the documented
invocation — i.e. the recon row 1 claim that the shells return the registered rejection is true
in design, not reproducible as-invoked. **Flag for R7 / a fix round; no repo files were touched by this round.**

**Designed rejections (clearly-labeled supplementary env: mission PYTHONPATH + `D:/source_code/backtrader`
appended so the fence import resolves) — proves the guard code itself is intact:**

- `ctp_bbroker_5s_examples/live/run.py --config single_symbol.yaml` → exit **2**, stderr JSON:
  `{"error_code": "PRESET_POLICY_VIOLATION", "field_path": "argv", "reason": "legacy_cli_arguments_not_supported",
  "message": "legacy CLI arguments cannot select or override an Iteration 41 runtime; supplied values are redacted (***)"}`
  (same for backtest entry) — this is `backtrader_runtime/legacy.py::run_legacy_config_first_cli`'s historical-flag refusal.
- `.../live/run.py` (no args) → exit **2**, stderr JSON:
  `{"error_code": "PRESET_POLICY_VIOLATION", "field_path": "strategy_dir", "reason": "runtime_not_registered", ...,
  "diagnostic": {"offline": true, "provider_preflight_started": false, ...}}` — the alias targets the
  miscomputed `examples/unified/runtime`, so it fail-closes with `runtime_not_registered` instead of
  routing to `007_ctp/runtime` as the README states.
- In-process `main()` (driver-v2, pre-imports the real `backtrader` to defeat the fence's namespace-dir
  shadow — the v1 driver's raw shadow failure is kept in `driver.stderr.txt`): exit 0 with payload
  `{"main_defined": true, "module_import_failure": null, "main_raised_type": "RuntimeConfigError",
  "main_raised_error": {"error_code": "PRESET_POLICY_VIOLATION", "field_path": "legacy_entrypoint",
  "reason": "legacy_direct_execution_not_supported", "message": "the historical direct execution entrypoint is not
  supported by the Iteration 41 runtime; use the registered configuration-first migration route"}}`
  — this is the mission-named `legacy_direct_execution_error` guard, verified to raise.

Maps to **recon row 1** ("007 历史入口边界") and R5 rec item 5. No write flag, no network, no credential read.

## Item 3 — 015_ctp_options_highfreq `run.py --live --allow-write`: CITED (+ refusal-order verification)

Fact already in the accepted R1+R2 chain (`sweep-r12/SUMMARY.md` row 015).

- Exact command (archived `sweep-r12/015/cmd-run.txt` cmd3): `C:/anaconda3/python.exe run.py --live --allow-write`
  (cwd `examples/unified/015_ctp_options_highfreq`; README line 9 documents the same as "refused").
- Exit code: **2**; stdout JSON key line (`write-probe.stdout.txt`):
  `{"mode": "live", "reason": "write_tier_not_implemented_for_decision_only_strategy", "status": "BLOCKED",
  "write_grant_result": "refused_no_order_path_by_contract", "hint": "this strategy has no order path by contract; ..."}`;
  stderr empty.
- **Write count 0 / no network attempt — verified:** in `examples/unified/015_ctp_options_highfreq/run.py`
  the `--allow-write` refusal (lines 344–358) returns before the python-3.11 check, `_ensure_dev_sdk_path()`,
  `legacy_runner`/`simnow_direct`/`backtrader` imports, `_load_env()`, evidence-dir creation (lines 360–418)
  and any connect/login. The refusal JSON itself carries no `run_id` / `evidence_directory` — fields only
  populated at lines 413–418 on the observation path. So: no provider import, no credential load, no
  evidence dir, no network attempt, write count 0.
- Maps to **recon row 9** / R5 item 3. No re-run performed; no write executed.
- Citation file: `03-015-citation/CITATION.txt`.

---

## Deferrals and skips (explicit)

- **014_2_ctp_options_midfreq — SKIPPED entirely** per instructions (another agent actively uses the dir
  today; its `--allow-write` BLOCKED capture is deferred to a later round). I did not read/run/touch the dir.
  Note for the caller: the R2 archive already contains an equivalent write-refusal capture for 014_2
  (`sweep-r12/014_2/`: `run.py --live --allow-write` → exit 2, `write_tier_not_implemented_for_decision_only_strategy`),
  so the deferred capture may only be needed as a refreshed variant.
- **Demo-smoke re-runs (012 mechanical smoke etc.) — SKIPPED** per instructions; historical
  `MECHANICAL_DEMO_SMOKE_PASS` (012_1 ×2, 012_2 ×1, 2026-10-10) referenced from recon §3 only.
- 013_x/014_1 observe+drill, formal SCN02 adjudication: out of R5 scope (R6/R7).

## Environment / integrity notes

- Unexpected env behavior: **no** run attempted real network I/O. All 007 failures happened at module
  import; the supplementary runtime-CLI refusals self-report `"offline": true`,
  `"provider_preflight_started": false`.
- The only env deviations from the mission pattern (repo root appended) are isolated in
  `02b-007-legacy-entries/supplementary-repo-root/` and `in-process-main/` and labeled `.reporoot.` / v2.
- Status file: `D:/bt_api_py/.codex-tmp/scn02-r5-status.md` (kept current during the round).
- 014_2 dir, `backtrader/strategy.py`, `backtrader/brokers/btapibroker.py`,
  `backtrader/stores/service_execution_adapter.py`, `backtrader_runtime/*`, `tests/*`: untouched.
