# MIG04 r3 formal-round evidence — 2026-10-16 r1 (re-frozen combo at CURRENT dev tips)

Output dir: `D:/temp/it10mig04-r3-20261016-r1` (fresh; r2 dir `it10mig04-r2-20261016-r1` read-only,
never touched). No repository file was modified or committed; every build came from `git archive`
of a pinned commit. Status file: `D:/bt_api_py/.codex-tmp/mig04-r3-status.md`.
Python 3.11.5 / pip 26.0.1 (`C:/anaconda3/python.exe`); venvs get bundled pip 23.2.1.

**MIG04 clause under acceptance:**
"base/SDK/transport/四包/provider/BT/certlib/AFI 指定发行组合可安装；代码版本、schema/wire、
extras 与状态库格式兼容或明确拒绝 | wheel/sdist 干净安装及正常入口跨仓集成，不用 submodule
源码导入代替发行验收；普通路径无四包/无 ZMQ"

Methodology: same pilot methodology as r2, re-run at the CURRENT dev tips. Run scripts are
byte-identical to r2 (`logs/09-run-scripts-sha256.txt` matches r2's 09 hashes), so the only
variable is the pin set.

Operator note (audit trail): during the first `git archive` attempt, `-o src/<name>.tar` resolved
relative to each `-C <repo>` dir and briefly wrote 8 stray `.tar` files into repo trees at 10:44
(sdk/backtrader failed; nothing else touched). They were deleted immediately; `git status
--porcelain` counts for all 10 repos returned exactly to the run-start values
(19/0/0/0/0/5/0/0/6/24, see `logs/00-repos-heads-status.log`). All subsequent archives used
absolute output paths. No tracked file was modified; the evidence below is unaffected.
Final audit (run end): all repos at run-start counts except AFI 24→23 — concurrent workstream
activity inside its `.codex-tmp/`/`scripts/` (visible in `git status`; no tar traces from this
session), i.e. external to this pilot.

## 1. Re-frozen combo (all branch dev; HEADs resolved live; pins = run-start HEADs)

| Repo | Pin (r3) | r2 pin | Delta |
|---|---|---|---|
| D:/bt_api_py (SDK bt_api_py 0.15.5) | `466977392985ef1d7fa9b282a220096c8d19dff8` | 99bec079 | **advanced** (+3 commits; 5ff3da11 portfolio-analytics role slice, `service_components.py` +87 lines only on service surfaces) |
| bt_api/bt_api_base 0.15.8 | `b4aabd1e17b9c7252fa7b4d266932087d99b5517` | b4aabd1e | unchanged |
| bt_api/bt_api_transport_zmq 0.1.0 | `a397fdb460d2deb22e67ceb4963992ef63dbbb0e` | a397fdb4 | unchanged |
| bt_api/bt_api_gateway 0.1.0 | `59b164ab7f89c6428ae6546103aad6058dab4c16` | 59b164ab | unchanged |
| bt_api/bt_api_execution 0.2.0 | `dc8bd7a3682d00cfaef4f15261607043c6d24fca` | dc8bd7a3 | unchanged |
| bt_api/bt_api_risk 0.1.0 | `131175a4070c930f1dd5186ea707d6bc9bbb4310` | a1485ee | **advanced** (+1 commit; 131175a durable portfolio-analytics task service; extras gating re-verified at 131175a: extras analytics/ml/dev, `_OPTIONAL_EXTRAS` ImportError naming intact) |
| bt_api/bt_api_monitor 0.1.0 | `536a4f04747182749fabdac5e28624f80465bc53` | 536a4f04 | unchanged |
| bt_api/bt_api_binance 2.0.1 | `b438a277c92a22851a16be0be1a8c8ee9f852b79` | b438a277 | unchanged |
| D:/source_code/backtrader 1.4.0 | `b002afa2da66ca6d14f1623280109be8e653a1b6` | b002afa2 | unchanged |
| D:/source_code/ai-for-investor | `34f59117ac9a2e73f00b6908cfeeb11fa53f5610` | 34f59117 | unchanged; packaging unit = `src/backend` |

All 10 repos resolved on branch `dev` with HEAD == pin (live `git status` audit:
`logs/00-repos-heads-status.log`). Worktrees remain dirty from concurrent workstreams
(SDK 19, risk 5, backtrader 6, AFI 24 porcelain lines) — irrelevant to the build: sources come
from archives, not worktrees.

## 2. Wheelhouse (ONE dir `wheelhouse/`, 10 wheels, all from `git archive`, exit 0 each)

Commands (sha256-taken at build time, `logs/02b-wheels-sha256.txt`, first 16 hex):

| Wheel | sha256 | Build log/exit |
|---|---|---|
| bt_api_py 0.15.5 | `6ed0ccc39991ef71…` | 02-build-sdk.log exit 0 |
| bt_api_base 0.15.8 | `840090fbc13e7b1f…` | 02-build-base.log exit 0 |
| bt_api_transport_zmq 0.1.0 | `2d7bd6421b573ea0…` | 02-build-transport_zmq.log exit 0 |
| bt_api_gateway 0.1.0 | `bad35ebfcdefe2b6…` | 02-build-gateway.log exit 0 |
| bt_api_execution 0.2.0 | `bfc396e7be9b64c0…` | 02-build-execution.log exit 0 |
| bt_api_risk 0.1.0 | `0adb56b3fc9b00e9…` | 02-build-risk.log exit 0 |
| bt_api_monitor 0.1.0 | `6bf28cb42f3a6dd1…` | 02-build-monitor.log exit 0 |
| bt_api_binance 2.0.1 | `7cac597539bfa6a2…` | 02-build-binance.log exit 0 |
| backtrader 1.4.0 | `d825efaeb82245fe…` | 02-build-backtrader.log exit 0 |
| ai_for_investor_backend 0.1.0 | `287ee6fcef1ff50f…` | 02-build-afi-backend.log exit 0 |

Build cmd shape: `C:/anaconda3/python.exe -m pip wheel --no-deps --wheel-dir wheelhouse src/<name>`
(AFI from extracted member `src/afi/src/backend`; AFI repo root is a uv workspace, no `[project]`).
Archive stage: `logs/01-archive.log` (10x exit 0), tar hashes `logs/01b-archive-sha256.txt` —
the 8 unchanged repos produce **byte-identical tarballs to r2** (cross-checked: base 155ee311…,
transport 46bff701…, gateway 2cfc2fe7…, execution 5ed61c7b…, monitor 11526e5e…, binance
d1906782…, backtrader 1002e242…, AFI 388cefe6…). New: sdk tar 5812863b…, risk tar 32ebf5f4….

**certlib is NOT wheel-buildable at the pin** — `binance_penetration/` has no pyproject/setup.py.
Exact pip error (`logs/02c-build-certlib.log`, exit 1):
`ERROR: Directory '…/binance_penetration' is not installable. Neither 'setup.py' nor 'pyproject.toml' found.`
Same honest treatment as r2: covered by an import check from the backtrader archive extraction.

Observation: wheels from the 8 unchanged pins differ in BYTES from r2's wheels (zip build
timestamps) even though their source tars are identical — wheel-level hashes identify this build,
tar-level hashes identify the sources.

## 3. Venvs (4 fresh, pip 23.2.1; installs ONLY from wheelhouse files + PyPI closure deps)

Install exits (`logs/98-create-exits.log`), pip checks (`logs/07-pipcheck-*.log`), freezes
(`logs/03-05-freeze-*.txt`, `logs/06-consumers-freeze.txt`):

| Venv | Install set (one pip command each, exit 0) | dists | key resolved versions |
|---|---|---|---|
| strategy | base + SDK | 37 | numpy 2.4.6, pandas 3.0.6, pydantic 2.14.1; no pyzmq |
| service | base + SDK + transport_zmq + gateway + execution + risk + monitor | 48 | numpy 2.4.6, **pyzmq 27.2.0** |
| extras | base + risk (no extras) | 37 | numpy 2.4.6 (via base core dep), **sklearn/scipy/joblib ABSENT** |
| consumers | base + SDK + binance + backtrader + AFI backend | 120 | **numpy 1.26.4** (backtrader pin), sklearn 1.9.1 (AFI closure) |

`pip check` → `No broken requirements found.` exit 0 in all four. Freezes record the wheelhouse
direct-references with sha256 (de-facto lockfile, limit N3).

## 4. Verifications (all exit 0; stdout/stderr/exit captured)

**A. Strategy venv (base+SDK) — PASS** (`logs/10-strategy-isolation.stdout.json`):
`bt_api_py` 0.15.5 AND `bt_api_base` 0.15.8 load from venv `site-packages`
(`include_system_site_packages=false`). `bt_api_gateway`, `bt_api_execution`, `bt_api_risk`,
`bt_api_monitor`, `zmq` → `find_spec false`, distribution null, `ModuleNotFoundError`
(four packages + ZMQ absent on the normal path). cfg01 CLI, both configs exit 0:
`python -m bt_api_py.service_config_io --role client <cfg>` → `{"status":"valid","client_count":0,`
`"component_count":0,"activated":false,…}` (`logs/11-…`, `logs/12-…`).

**B. Service venv — PASS** (`logs/20-service-host-min.stdout.json`): one explicit component
`gateway`/`mig04-pilot-market-hub` on declared port `service.market_source`, `start_failure` null,
`cleanup_failures` []; after 3× `serve_once` + close → state `closed`,
`exact_single_explicit_component` true, `closed_without_cleanup_failures` true. Origins confirm
wheel installs: `bt_api_py.service_host`, `bt_api_base.service_protocol`,
`bt_api_gateway.market_hub`, `bt_api_transport_zmq.server` all from `venvs/service/Lib/site-packages`.
Only stderr is the known PyZMQ Proactor RuntimeWarning (379 B, `logs/20-…stderr.log`) — acceptable.
Scope note: synthetic provider, zero client traffic (provider 0 starts/0 stops) — start/close
criteria only.

**C. Extras venv (risk 131175a4) — PASS** (`logs/30-extras.stdout.json`):
Wheel metadata: core deps base/pydantic/typing-extensions only; `Provides-Extra` =
analytics/ml/dev; `analytics → numpy>=1.26`, `ml → scikit-learn>=1.0`. Real venv: sklearn/scipy/
joblib genuinely absent; `AnomalyDetector`/`RiskEnsembleModel` raise explicit
`ImportError: … requires the 'ml' extra of bt_api_risk (install bt_api_risk[ml]): No module named 'sklearn'`;
`RiskManager` imports (numpy present via base — nuance N1); `RiskAssessor` imports. Base/admission
path loads NO numpy/sklearn/scipy/joblib (`base_path_sys_modules` all false). Simulated numpy
absence (child process, meta-path blocker): `RiskManager` → explicit ImportError naming
`'analytics'`, both ML exports → `'ml'`, base/service path still imports. `policy_catalog` port
`service.risk_policy` and `RiskReadService`/`validate_service_options` present at the new pin.

**D. Consumers venv — PASS** (`logs/40-consumers.stdout.json`): `import backtrader` 1.4.0 from
site-packages; `bt_api.plugins` entry point `binance` loads (`register_plugin`); `bt_api_binance`
2.0.1 from site-packages; `certlib` IMPORTED from the backtrader **archive extraction**
(`src/backtrader/examples/live/000_live_certification/binance_penetration/certlib/__init__.py`) —
import-level only (N2). AFI: backend wheel installed in the same venv, `import app` exit 0
(`logs/45-afi-import-app.log`), pip check clean after.

## 5. Clause mapping (sub-claim → evidence → verdict)

| Sub-claim | Evidence | Verdict |
|---|---|---|
| 指定发行组合可安装 | 10/10 archives exit 0 (`01*`); 10/10 wheels exit 0 (`02*`, `02b`); 4/4 venv installs exit 0 (`98-create-exits.log`, `03-05/06-install`); pip check exit 0 ×4 (`07-*`) | **PASS** |
| 代码版本兼容（发行组合冻结） | Pins table §1 (run-start HEADs == pins); resolved versions in freezes (`03-05/06-freeze`) | **PASS** (pin discipline; live heads keep moving — N4) |
| schema/wire 兼容 | Service run B via wheels: config_schema_version 1/2 parse, WireIdentity/WireScope/CURVE authority accepted by SDK ServiceHost + gateway + transport; started/closed clean | **PASS** (smoke scope: synthetic provider, no client traffic) |
| extras 兼容或明确拒绝 | Extras run C: ml gating explicit ImportError (live); analytics gating explicit under simulated absence; base path imports none of numpy/sklearn/scipy/joblib | **PASS** (N1: analytics extra not pip-reachable while base core-requires numpy) |
| 状态库格式兼容 | service venv created/used `run/registry-min.sqlite3` via ServiceHost, closed without cleanup failures (`20-*`); risk pin exposes policy_catalog control plane (`RiskReadService`, `service.risk_policy`, sqlite-backed versions) | **PASS** (minimal registry usage; policy catalog API/import level) |
| wheel/sdist 干净安装 + 正常入口跨仓集成 | Fresh venvs, wheelhouse-only installs; entry points: `python -m bt_api_py.service_config_io` (CLI), `bt_api.plugins`→binance EP, `import backtrader`, AFI `import app` | **PASS** |
| 不用 submodule 源码导入代替发行验收 | All module origins in `venvs/*/Lib/site-packages` (`10-`, `20-`, `40-` JSONs); archives are build inputs only | **PASS** (exception: certlib import-level from archive — N2) |
| 普通路径无四包/无 ZMQ | Strategy venv: ModuleNotFoundError for gateway/execution/risk/monitor + zmq (`10-`) | **PASS** |

Provider statement (honest): this combo's wheel-installed provider is **binance only**
(`bt_api_binance` 2.0.1 wheel + `bt_api.plugins` entry point). `ctp` exists in the tree
(`bt_api/bt_api_ctp` submodule at a497bab) but is **explicitly OUT** of this combo (not archived,
not wheel-built, not installed, not exercised); there is no `simnow` package in the tree. No
provider was installed from submodule source.

## 6. Honest limits (carried from r2 + new)

- **N1** (carried): risk `analytics` gating is not triggerable by plain pip anywhere `bt_api_base`
  is normally installed, because base CORE-requires numpy>=1.26; only the `ml` (sklearn) gating is
  operative in practice. Proof of the `analytics` ImportError path is under simulated numpy absence.
- **N2** (carried): certlib cannot join the wheelhouse (no build config at pin); consumer evidence
  is import-level from the archive; runtime backend wiring still unexercised.
- **N3** (carried): per-venv freeze files are the de-facto lockfile; wheels do not pin dep versions.
- **N4** (re-confirmed): live dev HEADs move between runs (this round: SDK 99bec079→46697739,
  risk a1485ee→131175a4; other 8 stable); combo integrity depends on pin discipline.
- **N5** (new observation): wheel bytes are not reproducible across builds even from identical
  source tars (zip timestamps); use tar hashes to certify sources and wheel hashes to certify a
  specific build.
- Scope as r1/r2: synthetic provider, loopback endpoints, single machine, no real account.

## 7. Readiness

All checks pass at the current dev tips. The combo (§1) + wheelhouse (§2) + per-venv freezes (§3)
are frozen and reproducible from the archives. MIG04 formal acceptance can proceed on this
evidence package; N1–N3 remain documented scope decisions/limitations, N4/N5 operational notes.

## 8. Log index (all under `logs/`)

`00-repos-heads-status.log`, `01-archive.log`, `01b-archive-sha256.txt`, `01c-extract.log`,
`02-build-*.log` (10x), `02b-wheels-sha256.txt`, `02c-build-certlib.log`, `03-05-freeze-*.txt`,
`03/04/05-*-install.stdout.log`, `06-consumers-{freeze.txt,install.stdout.log}`,
`07-pipcheck-*.log` (4x, all "No broken requirements found."), `08-venv-facts.log`,
`09-run-scripts-sha256.txt` (== r2's), `10-…12-` strategy, `20-` service (+stderr),
`30-` extras (+stderr), `40-` consumers (+stderr), `45-afi-import-app.log`,
`98-{build,create,check}-exits.log`. Run scripts/configs in `run/`; checks were executed with cwd
outside all repos (AFI import check ran with cwd = extracted archive member `src/afi/src/backend`).
