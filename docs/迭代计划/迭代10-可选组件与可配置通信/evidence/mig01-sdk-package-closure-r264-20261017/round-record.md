# R264: MIG01 SDK legacy package closure (candidate)

Date: 2026-10-17  
Repository: `D:\bt_api_py` / `dev`  
SDK parent at candidate freeze: `3b8025b7f81ad664d7da80796497fcb7419eedb3`  
Base child: `be51f280b70f52e5d86b37d3e3f9a148700deb9a`  
Gateway child: `8d05c53690d25409afd9bfe38c34b82d5bc56a89`

## Scope

The 49 tracked candidate paths remove the SDK's `bt_api_py.forwarding` and `bt_api_py.gateway` packages, `_gateway_wire`, and `runtime_plugins.gateway_dispatch`; remove their retired top-level exports; replace old runtime-positive tests with package-absence, API-surface, and normal-direct-path checks; remove deleted test paths from the three CI smoke lists; and update the SDK docs. The exact source scope is:

- CI: `.github/workflows/optimized-tests.yml`, `.github/workflows/reusable-compat-matrix.yml`, `.github/workflows/tests.yml`.
- Docs: `README.md`, `SECURITY.md`, `docs/explanation/architecture.md`, `docs/reference/bt_api.md`, `docs/reference/core-api.md`.
- SDK exports and package removals: `bt_api_py/__init__.py`, `bt_api_py/_gateway_wire.py`, `bt_api_py/forwarding/{__init__.py,btapi_adapter.py,btapi_backend.py,btapi_bridge.py,client.py,hub.py,memory.py,private_event_pump.py,router.py,schema.py,service.py,source_supervisor.py,state.py,transport.py}`, `bt_api_py/gateway/{__init__.py,client.py,config.py,safety.py}`, `bt_api_py/runtime_plugins/{__init__.py,gateway_dispatch.py}`.
- Tests: `tests/bt_api_contract/{test_gateway_runtime_unknown_execution.py,test_normalized_public_api.py}`, `tests/forwarding/{test_btapi_backend_command_paths.py,test_command_reconciliation.py,test_market_snapshot_contract.py,test_private_snapshot_cache_contract.py,test_server_write_scope_binding.py}`, and `tests/{test_btapi_forwarding_bridge.py,test_forwarding_bus_router_client.py,test_forwarding_schema.py,test_forwarding_zmq_transport.py,test_gateway_safety.py,test_light_import.py,test_mig01_forwarding_writers_retired.py,test_mig01_gateway_entrypoints_retired.py,test_private_event_pump.py,test_router_audit.py,test_runtime_gateway_dispatch_binding.py,test_source_supervisor.py}`.

Unrelated service, quota, DIM, provider, and submodule changes are excluded.

The replacement tests verify that retired SDK packages are unresolvable, retired names are absent from both public export sets, ordinary `BtApi` construction keeps `DirectBackend`, and non-`None` retired configuration is rejected before setup. They do not claim that Base's separate `bt_api_base.gateway` tree has been removed.

## Author verification

`tests/test_mig01_forwarding_writers_retired.py`, `tests/test_mig01_gateway_entrypoints_retired.py`, and `tests/test_light_import.py`: **9 passed**, with one existing `PytestConfigWarning` from disabled plugin autoload (`asyncio_default_fixture_loop_scope` is unknown). Ruff check, Ruff format check, and scoped `git diff --check` passed. This is author-local source evidence; no Root QA, Host, account, or formal MIG01 acceptance was run.

Wheel inspection used an SDK wheel (1,287,672 bytes, SHA-256 `ea26dade7cc58cde0fe7a68c3375eaa6d89ae1fa1a9f93f3f2b4fa5db6ac46db`) and Base wheel (177,540 bytes, SHA-256 `580ab2c50e8750d767441204d85c3039230e62acebcf37c44f1d7335e4986146`) built from the candidate package tree. The SDK wheel had 173 members and none of the removed package paths. The fresh venv was created **with `--system-site-packages`**. Its smoke checked installed import origins, absence of retired module imports, direct backend construction, and CLI `--help`; `_plugins_loaded=True` was set to keep ambient entry-point metadata out of the construction check. Therefore this is package/import evidence only, not isolated-dependency or normal provider-discovery verification. An unmasked construction exposed an ambient stale CTP entry point lacking `runtime_factory` and an unsupported MT5 plugin warning. The earlier system-site smoke remains package/import-only; its limitations are superseded by the separate isolated smoke below.

## Additive isolated normal-load smoke (author-local)

The isolated-load follow-up used Python 3.12.10 at `D:\temp\mig01-r264-normal-load-r1\.venv`; `pyvenv.cfg` records `include-system-site-packages = false`. Base and all fourteen providers were exported from clean `dev` commits, built as local wheels, and installed without dependencies; declared external runtime dependencies were installed inside this venv. Provider archive HEAD pins: Base `be51f280b70f52e5d86b37d3e3f9a148700deb9a`; Binance `e4c96e1e1265936e9abd746e69120f7a9d85043c`; Bitget `9e47a64b746a79cd61476d632297f6d0f790bdca`; Bybit `fc252ed4664facaf352910dc75e2635b22812274`; Coinbase `d1a22aa2f4ae9f6262019d692739a2cf631a43c3`; CTP `85917b31965039c5e0d973d3bce66070c0dc6280`; dYdX `01e6dbe886f61859d6194c35dc04bc4870bafcf2`; Gate.io `e00aa01649638000cc92c8138d29b2889a2db16f`; HTX `426f47234ee19e0630a9d5ae3a0cbb9f6e4fe4ea`; Hyperliquid `895900fda2f30a83732380198ffe5ef939179f2f`; IB Web `90643ccc6608de79cceebb0fbca07a71ee8e1c5d`; Kraken `e739ff32e1e75511babc3e7ba7f9a74c1b503763`; MEXC `adc5479da497f5ddb9edcefdf84aa726246576e4`; MT5 `b704dc94ff20c4ae9b7d715ae0e70afa4e2ba66d`; OKX `7691ab439438e827da104de501aea380f70e4d9c`. The SDK wheel was built from SDK archive HEAD `1ee1c1ea9aefa961f0c555d0487f03bac2511521`, with only the two R264 initializer overlays and the twenty R264 retired-source deletions applied. The SDK overlay SHA-256 values were `bt_api_py/__init__.py` `131f82040d63ae788cd264b3a5b61f26d2315a62b945150d6b45d0914d1390b7` and `bt_api_py/runtime_plugins/__init__.py` `8afe91bb9aa0f856faac6eac9b94acd7bd319d7c4f87c0197ee228a565dcf184`. The SDK wheel was 1,291,271 bytes / `ae5899f08a79edc7b2b4f026983efd4f9fe052361c13ab492c0412c93ae47e40`; Base wheel was 178,426 bytes / `cc43cd82947f5cc56fc99ddbfcdb5fc0c32975de3e4989815a385bfc14cfcc09`.

From the temp directory, ordinary `BtApi(debug=False)` construction left `_plugins_loaded` at its default false and triggered installed entry-point discovery: 14 providers were discovered, 13 registered, and the unsupported MT5 declaration was rejected with `plugin bt_api_mt5 is unavailable: it declares no supported exchanges`. The direct registry contained 26 exchange keys; the backend was `DirectBackend`. `BtApi` resolved from the venv `site-packages`; all four retired module specs and imports were absent. After `close()`, only `MainThread` remained. This is `AUTHOR_LOCAL_ISOLATED_WHEEL_NORMAL_PLUGIN_LOAD_ONLY`: no exchange configuration or feed was supplied, and no Host, account, or formal MIG01 acceptance is claimed. The first diagnostic invocation was discarded because its repository working directory shadowed the installed SDK; the recorded result is the corrected temp-directory invocation whose module origin was in the venv.

## Remaining boundary

`bt_api_base.gateway` remains in the Base package because AFI still has active manager and quote consumers. Its public `GatewayRuntime` constructor and `start_in_thread()` fail closed, but its retained private implementation still references the now-removed SDK forwarding modules; the Base tree must be migrated and retired in the later AFI consumer round. Provider runtime factories remain unestablished. This candidate does not complete formal MIG01.

No files were staged or committed for this candidate. SDK index was empty at the freeze check; unrelated working-tree changes remain untouched.
