## Verdict: GO

Independent static review of MIG04 r3 evidence package `D:/temp/it10mig04-r3-20261016-r1/`
against author status `D:/bt_api_py/.codex-tmp/mig04-r3-status.md`. Read-only on repos; no builds,
no installs re-run; all claims verified by re-hashing and reading raw artifacts. GO is conditioned
on the mandatory residual-gap disclosure in §8 and the certlib strict-reading caveat in §3/§7.

## 1. Hash integrity — 30/30 byte-exact matches, no mismatch

Cmd: `cd /d/temp/it10mig04-r3-20261016-r1 && sha256sum src/*.tar` (all 10 re-hashed)
All outputs byte-identical to `logs/01b-archive-sha256.txt`. Full hex for the two claimed-new tars:
- `src/sdk.tar  5812863b36829975406772db79057db4be05d6041824994659ef5927fab86c5e` (report prefix 5812863b… OK)
- `src/risk.tar 32ebf5f48ef01d1314f3b96061ca7c1ed4dc00f30583ae3e0e2b9df62b494050` (report prefix 32ebf5f4… OK)
Unchanged-8 (all 8 re-hashed, not just 3): afi `388cefe657ae3219243101df383e496d13a11e259cb557537331b38040173cb7`, backtrader
`1002e24219cf7a4f178e9c159301deb2d63ced1b7a46adef8a17fdde8f51d4fb`, base `155ee31167570385db4f7ae7ba107fa618ca078d0c7ce0f09dad043570c22c54`,
binance `d1906782353e389d93d194fc23c08eff4bbe08cf48eac15fae6d97adbc326d8a`, execution `5ed61c7b752786bf8b90d1dbbc4e158cdfff3d497ef9ecfbab94ba0f8c1d0af2`,
gateway `2cfc2fe748742d732888d7fcb90bd89c93bb6237800407c7f0862da950c1781d`, monitor `11526e5e83626df4038705474a38d0bed5c1fa1db542b32f6a08be7efeb02015`,
transport_zmq `46bff70138e8a615237d3824296727ccb755c2edf0e3ae5a0999ad141820732f` — all match 01b in full.
Cross-check vs r2 `logs/01b-archive-sha256.txt`: the 8 hex values above appear **identically** in r2
(byte-identical-to-r2 claim VERIFIED); r2 sdk `b2a5ed8c…` / risk `67c617eb…` differ from r3 (new tars as claimed).

Cmd: `sha256sum wheelhouse/*.whl` — all 10 byte-identical to `logs/02b-wheels-sha256.txt`. Full hex examples:
bt_api_py `6ed0ccc39991ef715f33a8a816c41ee6244b4490b911c2204eba4486a04377b8`,
base `840090fbc13e7b1fefdc686805ec8685a5e5a7dfd39ace3e7a5763922fd5dbb0`,
risk `0adb56b3fc9b00e9a03d29b735a4d56ee542aa2418ae6661058844f741d602b3`,
backtrader `d825efaeb82245fe91dbfcce213e85f6bacf43a1ef27f5c5955463f20ab0749d`,
AFI `287ee6fcef1ff50f0d51600e951285741596cc3a1d365906dbb0d7afcb0fa234`. Internal cross-check:
`02-build-sdk.log` prints `sha256=6ed0ccc39991ef71…` for the created wheel; `02-build-afi-backend.log`
prints `sha256=287ee6fcef1ff50f…` — consistent with the wheelhouse files.

Cmd: `sha256sum run/check_consumers.py run/check_extras.py run/check_strategy_venv.py run/service_host_min.py
run/strategy-configs/*.yaml` — all 6 hashes identical to `logs/09-run-scripts-sha256.txt`
(e.g. check_consumers.py `f2c9e0d4696b7ce1…66dc`, check_strategy_venv.py `ea3bdbfe0552dead…2276`), and r2's
09 has the same hex → "byte-identical to r2" VERIFIED.

## 2. Pins / HEAD truth — pins are ancestors of current dev; advances covered by N4 wording

Cmd: `git -C <repo> rev-parse HEAD` (live, now) vs `logs/00-repos-heads-status.log` / report §1:
- SDK D:/bt_api_py: pin `466977392985ef1d7fa9b282a220096c8d19dff8` → now `bb7624659e37d802b83c4907626bbbdd12b64895` (+3 commits: c9f2ec74, 6931b26b, bb762465). `merge-base --is-ancestor 46697739… HEAD` → **IS ancestor**.
- bt_api_base: `b4aabd1e17b9c7252fa7b4d266932087d99b5517` — **unchanged** (matches pin).
- bt_api_risk: pin `131175a4070c930f1dd5186ea707d6bc9bbb4310` → now `089b5efd71ebd0398c73e9aab370d674d88b41ba` (+1: 089b5ef). `--is-ancestor` → **IS ancestor**.
- AFI: pin `34f59117ac9a2e73f00b6908cfeeb11fa53f5610` → now `794eab0c26589ccad7ebb49899ac04e75a46d0cb` (+1: 794eab0c). `--is-ancestor` → **IS ancestor**.
- Other 6 unchanged vs pins: transport a397fdb4, gateway 59b164ab, execution dc8bd7a3, monitor 536a4f04, binance b438a277, backtrader b002afa2.
All 10 on branch `dev`. Report §1 ("HEADs resolved live; pins = run-start HEADs") + N4
("live dev HEADs move between runs") covers the three advances; pinned trees remain reachable. ✓
Also verified: commit `5ff3da11` (reported SDK delta) is an ancestor of pin 46697739, and `git -C
/d/source_code/backtrader ls-files` shows certlib is tracked in the backtrader tree.

## 3. Exit / check logs — exactly as claimed

- `98-build-exits.log`: sdk/base/transport_zmq/gateway/execution/risk/monitor/binance/backtrader/afi-backend `exit=0` (10/10); `certlib exit=1` (11th). `02c-build-certlib.log` verbatim:
  `ERROR: Directory 'D:/temp/it10mig04-r3-20261016-r1/src/backtrader/examples/live/000_live_certification/binance_penetration' is not installable. Neither 'setup.py' nor 'pyproject.toml' found.`
  Directory listing of `binance_penetration/` = README.md, cases/, certlib/, config.example.yaml — no build config, so N2 is factual.
- `98-create-exits.log`: `venv strategy/service/extras/consumers exit=0` (4/4) and `install strategy/service/extras/consumers exit=0` (4/4).
- `07-pipcheck-{strategy,service,extras,consumers}.log`: all 4 read `No broken requirements found.`
- `98-check-exits.log`: 7/7 `exit=0` (10/11/12/20/30/40/45).
- stderr sizes: 10/11/12-*.stderr.log = 0 bytes; 30-/40-stderr = 0 bytes; 20-stderr = 379 B containing exactly the known PyZMQ Proactor RuntimeWarning (cmd: `cat logs/20-service-host-min.stderr.log`).
- `01c-extract.log`: 10/10 `extract <name> exit=0`.
- Minor nuance: `01-archive.log` is 0 bytes, so the archive-stage "10x exit 0" is not itself content-logged;
  it is traceable via 98-build-exits.log + 01c + valid tars (I listed members of sdk.tar/backtrader.tar with
  `tar -tf`; both are well-formed git archives). Not a mismatch.

## 4. Key JSON reads — all report §4 claims reproduced

- `10-strategy-isolation.stdout.json`: bt_api_py 0.15.5 and bt_api_base 0.15.8 both from
  `venvs\strategy\Lib\site-packages`; `include_system_site_packages: "false"`; bt_api_gateway/execution/risk/monitor/zmq
  each `distribution: null, find_spec: false, import: "ModuleNotFoundError: No module named '<name>'"`.
- `11-…/12-…stdout.log`: `{"activated": false, "client_count": 0, "component_count": 0, … "status": "valid"}` (both configs).
- `20-service-host-min.stdout.json`: origins all under `venvs\service\Lib\site-packages`
  (bt_api_py.service_host, bt_api_base.service_protocol, bt_api_gateway.market_hub, bt_api_transport_zmq.server);
  one explicit component `gateway`/`mig04-pilot-market-hub`, declared port `service.market_source`;
  `start_failure: null`, `cleanup_failures: []`; final `state: "closed"`, `exact_single_explicit_component: true`,
  `closed_without_cleanup_failures: true`, `provider_starts: 0`, `provider_stops: 0` (synthetic provider, zero client traffic — as disclosed).
- Schema/wire 1/2: `run/service-config-min.json` carries `config_schema_version: 1` (communication + profiles);
  `run/service_host_min.py` also builds a component config with `config_schema_version: 2`; the run started and closed cleanly → report "config_schema_version 1/2 parse" corroborated.
- `30-extras.stdout.json`: `Provides-Extra` = analytics/ml/dev; analytics→`numpy>=1.26.0`, ml→`scikit-learn>=1.0.0`;
  real venv ml path: AnomalyDetector/RiskEnsembleModel `ImportError … requires the 'ml' extra …: No module named 'sklearn'`;
  RiskManager/RiskAssessor IMPORTED (N1 in action); simulated absence (meta-path blocker child, exit 0):
  RiskManager→ImportError naming `'analytics'`, both ML exports→`'ml'`; `base_path_sys_modules` all false;
  sklearn/scipy/joblib absent (nulls); numpy 2.4.6; `policy_catalog_port: ["service.risk_policy"]`; RiskReadService/validate_service_options true.
- `40-consumers.stdout.json`: backtrader 1.4.0 from site-packages; `bt_api.plugins` EP `["binance"]` loads `register_plugin`;
  bt_api_binance 2.0.1 from site-packages; certlib `IMPORTED …\src\backtrader\examples\live\000_live_certification\binance_penetration\certlib\__init__.py` (archive extraction).
- `45-afi-import-app.log`: `import_app_ok D:\temp\it10mig04-r3-20261016-r1\src\afi\src\backend\app\__init__.py`
  (traceable, but see D1 — the import resolved from the extracted source tree, not the installed wheel).
- Registry: read-only sqlite query of `run/registry-min.sqlite3` → valid SQLite DB, tables
  `registration_keys, registry_meta, sessions, strategy_write_claims` (rows 0/2/0); `.lock` present → minimal registry usage is real.

## 5. Freeze / facts — resolved versions confirmed

- `08-venv-facts.log`: all 4 venvs pip 23.2.1.
- Dist counts = freeze line counts: strategy 37, service 48, extras 37, consumers 120 (all match report).
- strategy: numpy 2.4.6, pandas 3.0.6, pydantic 2.14.1, **no pyzmq**. service: numpy 2.4.6, **pyzmq 27.2.0**.
  extras: numpy 2.4.6 (via base core dep), **sklearn/scipy/joblib absent**. consumers: **numpy 1.26.4**, scikit-learn 1.9.1.
- `grep -l pyzmq 03-05-freeze-*.txt 06-consumers-freeze.txt` → only `03-05-freeze-service.txt`. ✓
- Cause of consumers numpy 1.26.4 verified from `wheelhouse/backtrader-1.4.0-py3-none-any.whl` METADATA:
  `Requires-Dist: numpy<2.0.0,>=1.20.0; python_version < "3.13"` → "backtrader pin" wording correct.
- Install provenance ("Processing …wheelhouse\…" lines) exactly: strategy base+SDK; service base+SDK+transport_zmq+gateway+execution+risk+monitor; extras base+risk; consumers base+SDK+binance+backtrader+AFI; each log contains 1x "Successfully installed".
- N5 corroboration: r2 02b wheel hashes all differ from r3 (e.g. r2 base `b620538f…` vs r3 `840090fb…`) even for the 8 unchanged sources;
  `unzip -v` member name+CRC diff for the base wheel r2 vs r3 → **identical CRCs** (byte difference is zip metadata only, as claimed).

## 6. Clause sub-claim judgment

| Sub-claim | Judgment (sufficient for 发行验收?) |
|---|---|
| 指定发行组合可安装 | Sufficient for the 10 distributable artifacts (archives+wheels+installs all exit 0, hashes frozen). certlib: see strict-reading caveat below — sufficient **only** with the explicit exception wording in §8. |
| 代码版本兼容 | Sufficient: pins recorded, pins verified ancestors, resolved versions in freezes, tar hashes identify sources. |
| schema/wire 兼容 | Sufficient at declared smoke scope (real wheels, ServiceHost+gateway+transport accept config v1/2, WireIdentity/WireScope/CURVE, start→close clean). Not data-plane proof; scope must be stated. |
| extras 兼容或明确拒绝 | Sufficient with N1 disclosure: ml gating is the live 明确拒绝 path; analytics path proven only under simulated numpy absence. |
| 状态库格式兼容 | Sufficient minimal: sqlite registry created and used via installed wheels; clean close; policy-catalog API/import level. |
| wheel/sdist 干净安装 + 正常入口跨仓集成 | Sufficient with the AFI nuance (D1): installs are wheelhouse-only and pip-check clean; CLI/EP/backtrader entries verified from site-packages; AFI `import app` exited 0 but resolved from the archive tree (cwd), not from the installed wheel — wheel-installed `app/` package exists in site-packages (verified on disk), entry-from-wheel remains unproven by this exact check. |
| 不用 submodule 源码导入代替发行验收 | Sufficient: module origins in 10/20/40 are all site-packages; archive use is build input; certlib exception disclosed (N2); AFI cwd nuance as D1. |
| 普通路径无四包/无 ZMQ | Sufficient: ModuleNotFoundError set for gateway/execution/risk/monitor + zmq in the normal strategy venv. |

Strict-reading caveat (certlib): the clause lists certlib in the combo, and at the pin certlib has no
release artifact, so literal "certlib installable" is not demonstrable — the package delivers the exact
pip refusal (02c) + import-level verification + disclosure. I judge this sufficient for GO because the
clause itself embraces "…或明确拒绝" as an acceptable disposition class and the refusal is explicit and
evidenced; but the formal record must scope the sub-claim as pass-with-exception (never claim certlib installability).

## 7. Dispositions N1–N5

- **N1 (analytics extra pip-unreachable while base core-requires numpy): non-blocking**; disclosure required (only ml gating is live; analytics proven under simulated absence). Report wording is accurate.
- **N2 (certlib not wheel-buildable, import-level only): non-blocking for GO only with a formal exception sentence** in the acceptance record; if the clause is enforced strictly letter-for-letter, this would be the single blocking item (no installable certlib artifact exists to accept). Recommend recording it as an explicit scoped exception, not silently folding into PASS.
- **N3 (per-venv freezes = de-facto lockfile): non-blocking**; verified freezes record wheelhouse wheel sha256 direct refs. Operational note.
- **N4 (live heads move): non-blocking**; verified — pins are ancestors of today's dev (SDK/risk/AFI advanced post-run; consistent with concurrent DIM01/ARB01/AFI07 work). Pin discipline statement covers this.
- **N5 (wheel bytes non-reproducible): non-blocking**; verified byte differences r2↔r3 from identical sources; CRC-identity shows metadata-only delta. Recommendation already in report (tar hashes for sources, wheel hashes per build) is correct.

## 8. Discrepancies register (none invalidates a hash, exit code, or headline claim)

- **D1 (minor, scope-limiting):** `45-afi-import-app.log` shows `import app` resolved from
  `src\afi\src\backend\app\` (archive extraction, cwd precedence), while report §4.D's phrasing suggests the
  wheel-installed entry was exercised. Mitigations verified: AFI wheel genuinely installed
  (`06-consumers-freeze.txt`: `ai-for-investor-backend @ file:///…wheel#sha256=287ee6fcef1ff50f…`),
  `app/` package present in `venvs/consumers/Lib/site-packages/app/__init__.py`, pip check clean.
  Must be worded in the commit disclosure.
- **D2 (minor):** `logs/01-archive.log` is 0 bytes; archive-stage exit codes are only inferable from
  98-build-exits.log (whose names match wheel-build targets) + artifact validity. No mismatch found.
- **D3 (pre-existing, not pilot-caused):** SDK worktree contains untracked `0` (file, mtime 2026-10-15
  20:24, content `D:/temp/iteration10-topic-v2-sdk-20261015-r4/integration-exit.txt`) and `%SystemDrive%/ProgramData/Microsoft/Windows`
  (2026-10-15 16:28) — both predate the r3 run (started 10:43 Oct 16). Not tar pollution; hygiene items outside MIG04.
- **D4 (expected drift):** porcelain now 13/0/0/0/0/3/0/0/6/23 vs run-start 19/0/0/0/0/5/0/0/6/24.
  Explained by post-run commits (SDK bb762465/6931b26b/c9f2ec74; risk 089b5efd; AFI 794eab0c which matches
  the report's own run-end audit "AFI 24→23"). No leftover `*.tar` anywhere.

## 9. Repo-write audit / stray-tar accident

Cmd: `git -C <repo> status --porcelain | wc -l` current vs run-start (00 log): SDK 13(19), base 0, transport 0,
gateway 0, execution 0, risk 3(5), monitor 0, binance 0, backtrader 6(6), AFI 23(24) — see D4; no porcelain line is a `*.tar`.
Tar sweep: `find <repo> -name '*.tar' -not -path '*/.git/*'` → all 9 non-SDK repos CLEAN; SDK excluding
`.codex-tmp` → 0 tars; `find /d/bt_api_py … -newermt '2026-10-16 10:40'` → 0 tars newer than pilot start.
The only tars under SDK are inside `.codex-tmp/` (concurrent-session archives dated 05:22–06:41, pre-pilot).
Conclusion: the disclosed 8-stray-tar slip left **no visible pollution**; current state is consistent
with the report's "deleted immediately; counts restored" account. backtrader's ` D 0` (deleted tracked file
named `0`, pre-existing, part of the run-start count 6) is unrelated to MIG04.

## 10. Traceability spot-checks (≥6 claims → raw file)

1. sdk/risk tar prefixes 5812863b/32ebf5f4 → re-hash matches 01b in full (§1).
2. "8 unchanged tars byte-identical to r2" → r2 01b literal hex equality for all 8 (§1).
3. "10/10 wheel exit 0" → 98-build-exits.log + per-wheel `Successfully built` in 02-build-*.log + internal sha256 match.
4. "strategy isolation: 4 packages + zmq ModuleNotFoundError" → 10 JSON (§4).
5. "exact_single_explicit_component / closed_without_cleanup_failures true" → 20 JSON (§4).
6. "ml ImportError naming 'ml' (live); analytics naming 'analytics' (simulated)" → 30 JSON (§4).
7. "backtrader 1.4.0; certlib import from archive; binance EP" → 40 JSON (§4).
8. "AFI import app exit 0" → 45 log + 98-check-exits (`check 45-afi-import exit=0`) — with D1 wording caveat.
9. "numpy 2.4.6 vs 1.26.4; pyzmq only in service; sklearn absent in extras" → freezes (§5) including the backtrader wheel Requires-Dist cause.
10. "certlib pip refusal" → 02c text + directory listing (§3).
All report claims sampled are traceable to raw artifacts; the only wording adjustment needed is D1/D2.

## 11. GO — what the round commit / acceptance note must disclose

Record: combo pins (§1 of report) + wheelhouse hash set (02b) + per-venv freezes as the frozen lockfile;
and word the residual gaps: (1) certlib is NOT installable at pin (no build config; exact pip refusal
logged) — acceptance of certlib is import-level only, runtime wiring unexercised (N2, explicit exception);
(2) the only live extras 明确拒绝 path is ml/sklearn; analytics gating proven solely under simulated numpy
absence because base core-requires numpy (N1); (3) schema/wire evidence is smoke-scope: synthetic provider,
zero client traffic, start/close criteria only; (4) AFI `import app` check was executed with cwd at the
archive extraction (source resolution), while wheel install + pip check + site-packages `app/` presence
are separately verified (D1); (5) freezes are de-facto lockfiles, wheels do not pin dep versions (N3);
(6) pins are run-start HEADs; dev heads have since advanced (SDK bb762465, risk 089b5efd, AFI 794eab0c)
— descendants verified (N4); (7) wheel bytes are not reproducible; tar hashes certify sources, wheel hashes
certify this build (N5); (8) the stray-tar operator slip is disclosed and residues verified absent.
Provider scope as stated in report §5 (binance only; ctp submodule a497bab verified OUT; no simnow package — verified).
