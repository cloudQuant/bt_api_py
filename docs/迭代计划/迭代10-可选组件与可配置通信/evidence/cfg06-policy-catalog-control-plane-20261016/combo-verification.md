# Round 154 committed-combination verification status

- Date: 2026-10-16
- Verdict: **PASS**
- Scope: read-only verification that the round-154 COMMITTED combination (parent 99bec0797354fd78dc1840cbe7007847c98fea41; risk gitlink a1485ee28447741010c99fb6979c4a16ee67ebc2) still passes the same 6 tests that the accepted QA run (D:\temp\it10cfg06-root-20261016-r1) passed against the uncommitted WIP worktree.
- Evidence dir: D:\temp\it10r154-combo-verify-20261016-r1 (probe.txt, child-probe.txt, pins.json, pytest-stdout.log, pytest-stderr.log, junit.xml, exit.txt, summary.json; no repo was modified).

## Method

Candidate tree built from committed bytes: `git -C D:\bt_api_py archive 99bec079... | tar -x -C candidate`, plus `git archive <pin>` of ALL 20 submodules recorded in that commit (bt_api_base b4aabd1e, ..., bt_api_risk a1485ee, bt_api_transport_zmq a397fdb; full list in pins.json repos.submodule_pins / submodules-archive.log). WIP neighbors are therefore replaced by their committed versions.

## Exact command (identical to accepted run; root/cwd -> candidate, outputs -> evidence dir)

```
cwd = D:\temp\it10r154-combo-verify-20261016-r1\candidate
unset: PYTHONPATH PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD PYTEST_ADDOPTS PYTEST_CURRENT_TEST PYTEST_PLUGINS PYTHONDONTWRITEBYTECODE PYTHONNOUSERSITE
env:  PYTHONPATH=D:\temp\it10r154-combo-verify-20261016-r1\candidate;...candidate\bt_api\bt_api_base\src;...candidate\bt_api\bt_api_risk\src;...candidate\bt_api\bt_api_execution\src;...candidate\bt_api\bt_api_transport_zmq\src
      PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
C:\anaconda3\python.exe -m pytest -c D:\temp\it10cfg06-scratch\pytest-risk-fx.ini --rootdir D:\temp\it10r154-combo-verify-20261016-r1\candidate -q --noconftest --import-mode=importlib -p no:cacheprovider --junitxml=D:\temp\it10r154-combo-verify-20261016-r1\junit.xml --basetemp=D:\temp\it10r154-combo-verify-20261016-r1\pytest-tmp tests/unit/test_service_policy_catalog_control_plane.py tests/unit/test_service_components_group_runtime_bundle.py
```

Result: exit 0, **6 passed**, 0 failed/errors/skipped, 5.27s. The 6 junit node ids are identical to the accepted run's junit.xml.

## Resolution table

| package | pytest parent (cwd=candidate) | boundary child subprocess (hardcoded cwd=D:\bt_api_py) |
|---|---|---|
| bt_api_py | candidate (probe.txt) | main tree D:\bt_api_py\bt_api_py **only if imported**; tested path never imports it |
| bt_api_risk | candidate | candidate (PYTHONPATH; cwd has no bt_api_risk dir) |
| bt_api_base | candidate | candidate |
| bt_api_execution | candidate | not imported by that node |
| bt_api_transport_zmq | candidate | not imported by that node |

All five resolve under candidate in the probe (required precondition, passed).

## Pin check (170 accepted-spec pins, full mapping in pins.json)

- 134 exact match; **30 eol-only matches** (accepted pin == current live worktree bytes; `git archive` with core.autocrlf=true exported these as CRLF, content-identical modulo CR); **4 real content differences**; 1 untracked harness file missing from tree (expected); 1 harness file outside tree (pytest-risk-fx.ini sha still matches its pin).
- Deliverables (5 parent + 3 risk + bt_api_py/__init__): 5 byte-exact in candidate; the 2 test files + risk service_component.py differ raw only by CRLF — their committed blob bytes are byte-identical to the accepted pins (cat-file sha256 = e333206a / 0dbe3925 / 184bbad1). risk pyproject.toml is not in the accepted pin set (sha recorded: 54faf785...). Net: all 8 pinned deliverables content-identical to the accepted-run bytes.

## Divergence vs accepted run

1. Accepted run: parent live HEAD e2f51910 with WIP neighbors, risk HEAD 756ffbd + WIP. Candidate run: 99bec079 + a1485ee committed bytes.
2. The 4 real pin differences are exactly the DIM01 WIP neighbors replaced by committed bytes: bt_api_py/service_risk_order_entry.py, bt_api_py/service_risk_order_journal.py (both genuinely imported by the group-runtime-bundle test), risk core/admission.py, risk core/exposure.py. The candidate run passed 6/6 with the committed versions, which is the point of this verification.
3. 30 CRLF export artifacts (autocrlf=true) are content-neutral; no substantive byte changes elsewhere.
4. Verification-time state: parent worktree still has those WIP edits uncommitted (plus other untracked files); risk worktree still has admission.py/exposure.py modified. Nothing was touched by this verification.

## Caveat: subprocess boundary node

`test_base_install_serves_policy_catalog_without_numpy_or_sklearn` spawns `[sys.executable, "-c", _SUBPROCESS_BOUNDARY]` with hardcoded `cwd=r"D:\bt_api_py"` and inherits the candidate PYTHONPATH. Child probe: bt_api_risk and bt_api_base resolve to candidate; bt_api_py would resolve to the main worktree, but the tested child path **never imports bt_api_py** ('bt_api_py' not in sys.modules after the full boundary import set; zero bt_api_py references in risk src). Hence that node exercised committed candidate bytes for risk/base only — no main-worktree modules. The exact committed boundary script run standalone under the same spawn conditions: rc=0, printed BOUNDARY-OK. Main-worktree copies of the child-imported modules (risk service_component/__init__) equal committed bytes anyway (risk worktree clean for those files; pins.json).
