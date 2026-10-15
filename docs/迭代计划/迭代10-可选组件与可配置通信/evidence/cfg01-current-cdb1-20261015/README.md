# CFG01 current committed SDK wheel evidence (2026-10-15)

This packet preserves the one current-HEAD isolated-client observation and a metadata-only reconciliation. It is evidence for Root review; it does **not** record a formal CFG01 PASS.

## Fixed build and client environment

The wheel was built from committed SDK revision `cdb1b81e39b661afb145ff3d9e94695c97429d7e`, tree `6943f04da92133ab23ace7fc0c3ceefdc676eb50`, using a selective `git archive` of `pyproject.toml`, `setup.py`, `MANIFEST.in`, `README.md`, and `bt_api_py`. The archived source hash is `e84eacaf4d53f38e702bd39033adaf8db95675ca680152caed6fa711ade7943e`; the resulting `bt_api_py` wheel is version `0.15.5`, SHA-256 `f529664e95b4b489cf418b4fc78e81d7a77b72f5ba80c37ed3f5c4ce03c9033b`. The build tool was `build 1.6.1` (recorded under `build.build_tool_version`). The wheel and source archive themselves are not copied into this small evidence packet; their hashes and original temp paths are recorded in `build-manifest.json`.

The client used Python 3.11.5 in an isolated venv with `include-system-site-packages = false`. Pip used its normal resolver for the SDK, accepted Base and Backtrader wheels, and public dependencies; there were no constraints or `--no-deps`. The raw install log is retained because it is 26 KB. The install log ends in `Successfully installed`; `pip check` returned exit 0 and `No broken requirements found.` No domain package, ZMQ transport/PyZMQ, or spdlog distribution was installed for the observed runtime node.

## One probe invocation and its result

`cfg01_current_head_probe_r3.py` is the exact probe source invoked once. The fixed source config has bytes `# Ordinary SDK/backtest use: no optional clients or services.
{}
`; the second config is exact bytes `clients: {}`. Both were parsed by the public config loader and CLI. The probe then constructed and closed default `BtApi` using `DirectBackend`, and ran one synthetic bar through normal `Cerebro`.

The original result is preserved verbatim in `cfg01-current-head-observation-r1.json`; its status is `ASSERTION_FAILED`. The original probe process returned exit code 1 in the command tool because one probe assertion used a wrong metadata lookup: it read `build_tool_version` at the manifest top level instead of `build.build_tool_version`. The process exit is the observed command-tool return, **not** a reconstructed execution receipt. The child stdout is retained separately in `cfg01-current-head-probe-r3.stdout.log`. Do not describe the original probe process as exit 0 or rewrite its status.

The metadata-only reconciliation independently reads the installed wheel `METADATA` and `direct_url.json`, the wheel metadata/hash, source archive/hash, fixed Git config blob, and the original result. It ran under `python -I -S`, imported no SDK modules, and did not rerun the business probe. It confirms installed and wheel version `0.15.5`, matching direct_url wheel hash, and build tool `1.6.1` at the nested manifest field. All ten captured CFG01 runtime gates in the original observation are true; the sole false original assertion is the incorrect build-tool lookup. Independent review approved the metadata reconciliation and runtime-gate reading, not the original process exit or formal CFG01 acceptance.

The metadata reconciliation also exposes an unrelated build-record typo without editing the frozen manifest: the tracked source config checkout is 65 bytes and byte-identical to the fixed Git blob (SHA-256 `588fccd3c0b5ad7e0d42a296aa8359f7b541faeb39e949f2c897bc740b897592`; Git blob object `4c637df0174450c5ffb554b3beb4d83871a171b4`), while the manifest says `bytes: 68`. Its recorded config SHA and Git blob object ID are correct. The 11-byte `clients: {}` config matches its manifest. The discrepancy remains marked false in the reconciliation; original records are unchanged.

Resource facts are phase-limited: captured config, direct SDK, and Cerebro phases have empty optional-resource counters and no `ServiceHost` constructor events; no service modules or watched optional imports were observed. The imports phase records one socket audit event and two subprocess audit events. Full-run process-handle delta `+438` and thread delta `0` are diagnostic only; this packet makes no claim that every import-time OS handle remained at zero.

## Contents and byte pins

All eight evidence artifacts below are byte-copied from the task temp directory. Sizes and SHA-256 pins were checked before copying. This README is the sole newly written packet file.

| File | Bytes | SHA-256 |
|---|---:|---|
| `cfg01_current_head_probe_r3.py` | 20,342 | `46544793a81b6ffb372ec2136684e4ffa5d3661151bed9ec11ba3512a2ba52fc` |
| `build-manifest.json` | 2,698 | `fd8fd3b9477b1080bddb681c531b11880a58b586e1a51d7c8de6950a541956af` |
| `cfg01-current-head-observation-r1.json` | 15,026 | `73d65c107adcab0f21e507e505f0e90910093a1d89f64038a493380267d7c26b` |
| `cfg01_current_head_metadata_reconciliation_r1.py` | 12,740 | `c345dabcb2df138fa2d9ad6aa93de88fce70a7478d3c5dc7d0c19419683bcaf1` |
| `cfg01-current-head-metadata-reconciliation-r1.json` | 8,819 | `eeab4aca8a4fc0c655960f4481d3aae218b9dcf269e11bff24f31c499d040530` |
| `pip-check-current-cdb1.txt` | 31 | `672aec65a45bed50e70229c70934a7eebf0462595c459b83023027645d57cccf` |
| `pip-install-current-cdb1.log` | 26,312 | `faf3ff519f8b6f99a0324b62ef45cdf7aaab9d7aa547560f49e49a004880b76e` |
| `cfg01-current-head-probe-r3.stdout.log` | 93 | `96fe8029302539963b13dc10cb8130a7e70cb13e9d8e08efb43152a1232032c1` |

The source SDK, shared service code, plans, progress documents, and repository Git index were not changed by this packaging action. No tests, SDK imports, business probe, or service/provider operation were run while assembling this packet.
