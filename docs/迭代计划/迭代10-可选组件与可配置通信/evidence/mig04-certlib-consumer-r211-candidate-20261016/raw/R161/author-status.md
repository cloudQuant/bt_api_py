# MIG04 r3 status (2026-10-16) — formal-round re-freeze at CURRENT dev tips

Evidence dir: `D:/temp/it10mig04-r3-20261016-r1/` (fresh; r2 dir untouched). No repo writes
(one operator slip: 8 stray tar files briefly written/deleted in worktrees at 10:44; porcelain
counts restored exactly — see report §header), no commits; all builds from `git archive` of pins.
Python 3.11.5 / pip 26.0.1; venvs pip 23.2.1. Run scripts byte-identical to r2 (09 hashes match).

## Pins (all branch dev; run-start HEADs == pins)
- SDK bt_api_py `466977392985ef1d7fa9b282a220096c8d19dff8` (r2 99bec079 → +3 commits)
- base `b4aabd1e` unchanged · transport `a397fdb4` · gateway `59b164ab` · execution `dc8bd7a3`
- risk `131175a4070c930f1dd5186ea707d6bc9bbb4310` (r2 a1485ee → +1 commit)
- monitor `536a4f04` unchanged · binance `b438a277` · backtrader `b002afa2` · AFI `34f59117`
- Unchanged repos' tars byte-identical to r2 (01b); SDK/risk tars new (5812863b… / 32ebf5f4…)

## Results (fresh venvs, wheelhouse-only installs)
| Item | Result |
|---|---|
| archives/wheels | 10/10 archive exit 0; 10/10 wheel exit 0 (01*, 02*, 02b) |
| certlib | NOT wheel-buildable (no pyproject/setup.py; 02c exact pip error) — import-level check |
| pip check x4 | exit 0 "No broken requirements found." (07-*) |
| strategy check | exit 0: bt_api_py+base from site-packages; gateway/execution/risk/monitor/zmq all ModuleNotFoundError; cfg01 CLI both configs valid/0/0/activated=false (10-…12-) |
| service check | exit 0: one explicit gateway market_hub component; started→closed, no cleanup failures; only known PyZMQ Proactor warning (20-) |
| extras check | exit 0: ml gating live ImportError naming 'ml'; analytics gating under simulated numpy absence; base path loads no numpy/sklearn/scipy/joblib (30-) |
| consumers check | exit 0: backtrader 1.4.0; binance EP loads; certlib imports from archive; AFI wheel + `import app` exit 0 (40-, 45-) |
| resolved deps | numpy 2.4.6 (strategy/service/extras), 1.26.4 (consumers); pyzmq 27.2.0 (service); sklearn absent in extras (03-05/06-freeze) |

## Clause mapping verdict: full PASS on all sub-claims
组合可安装 PASS · 代码版本 PASS · schema/wire PASS (smoke scope) · extras PASS (N1 nuance) ·
状态库 PASS (minimal) · 干净安装+入口集成 PASS · 无 submodule 源码导入 PASS (certlib exception N2) ·
普通路径无四包/无 ZMQ PASS. Providers: binance only (wheel); ctp/simnow explicitly OUT of combo.

## Known gaps / limits
N1 analytics extra unreachable via plain pip (base core-requires numpy) · N2 certlib import-level
only · N3 per-venv freezes are the de-facto lockfile · N4 live heads keep moving · N5 wheel bytes
non-reproducible across builds (use tar hashes for sources, wheel hashes for the build).

## Readiness
Combo + wheelhouse + freezes frozen at current dev tips; all checks exit 0 — MIG04 formal
acceptance can proceed on this package. Full detail: `D:/temp/it10mig04-r3-20261016-r1/report.md`.
