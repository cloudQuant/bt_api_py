# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`AGENTS.md` is the authoritative policy for this repo (Chinese). Follow it. Key points:

- Iteration 53 is a **source-refactor phase**: finish the refactor and hand it to the user for review; functional fixes and acceptance wait for that review.
- Before you analyse, review, propose migrations or edit anything, read the README section "SDK family and responsibility boundaries". Also read the Backtrader architecture docs: `docs/TRADING_SYSTEM_ARCHITECTURE.md` and `docs/source/developer-guide/repository-responsibilities_zh.md` in a local Backtrader checkout. AGENTS.md gives Windows `D:/source_code/backtrader/...` paths. If no local checkout exists, use the public links in the README and note which version you read. For Iteration 53 work, read the current plan in `docs/迭代计划/`.
- Keep these apart: agreed responsibilities, current source, planned targets and the level of evidence. Source or simulated tests are **never** account acceptance. Don't describe them that way in code, docs or commit messages.
- Workflow: edit directly in each repo's existing `dev` checkout (no extra worktrees unless asked). Each round is fix → agreed acceptance → commit to `dev`. Commit only the files or hunks that were accepted, and leave other existing changes alone. Commit submodules first, then the parent gitlink. Record scope, results, repo/branch and commit hash for each round.
- Unsupported capabilities must be rejected explicitly. Never silently fall back or fabricate fills. Mode names (`paper`/`live`) do not grant write permission.

## Layout

- `bt_api_py/`: the SDK package. It holds the `BtApi` facade (`bt_api.py`), provider route discovery, and cross-package session/service composition. The modules are flat and named by concern: `service_*`, `ctp_*`, `execution_*`, `market_*`, `account_*`, `order_*`, `query_*`, `*_composition.py`, `*_contracts.py`.
- `bt_api/`: git submodules, one repo per package, each tracking `dev`, with its own `pyproject.toml`, `src/` and `tests/`. The direct refactor scope is `bt_api_base`, `bt_api_ctp`, `bt_api_okx` and `bt_api_binance`. README and AGENTS.md say the other submodules were moved to `bt_api/deperated/`. In this checkout they are still directly under `bt_api/`, so check before relying on either layout.
- `tests/`: SDK tests. The root `conftest.py` adds the repo root and `scripts/` to `sys.path` and applies to `examples/network_tests` too.
- `docs/`: architecture, decisions, iteration plans (`docs/迭代计划/`) and frozen evidence records. Don't rewrite evidence or protected files.

## Architecture essentials

- **Ownership split.**
  - `bt_api_base` owns neutral DTOs, protocols and shared primitives.
  - Provider packages own everything exchange-native: sessions, auth, field names, request/response mapping, and conversion to neutral form.
  - `bt_api_py` owns routing, discovery and composition only.
  - Migrations in this repo usually move provider-specific logic out of `bt_api_py` into the provider package and leave a neutral seam in the SDK.
  - Optional packages (`bt_api_execution`, `bt_api_risk`, `bt_api_gateway`, `bt_api_monitor`, `bt_api_runtime`, `bt_api_transport_zmq`) must never become implicit prerequisites of the plain direct path. Identity, protocol safety, request correlation and UNKNOWN handling must still work when they are absent.
- **Provider routes and entry points.**
  - Routes are named `EXCHANGE___ASSET` (e.g. `OKX___SWAP`, `BINANCE___SPOT`, `CTP___FUTURE`).
  - Providers plug in through many `bt_api.*` entry-point groups declared in their `pyproject.toml`: `bt_api.plugins`, `query_providers`, `market_stream_providers`, `order_request_projections`, `normalization_policies`, `execution_configuration_policies`, and so on.
  - `bt_api_py` discovers them (see `provider_entrypoints.py`, `_plugin_catalog.py`). Per-route behaviour is therefore declared by the provider, not hard-coded in the SDK. When you add or move a provider policy, update the provider's entry-point table, and reinstall it (`pip install -e`) before discovery will see it.
- **Two execution paths.**
  - Direct: `BtApi`/Feed in-process, calling the provider plugin. The optional `execution_config` adds a local journal.
  - Managed: Backtrader `ServiceExecutionSession` → service-v2 `ServiceHost` (`service_host.py`, `service_*`).
  - Installing the SDK does not enable the managed path.
- **`OrderRequest`** is the cross-transport order contract. The legacy positional form needs a side-qualified type such as `"buy-limit"`.

## Commands

```bash
pip install -e ".[dev]"            # SDK; submodules: pip install -e bt_api/bt_api_<name>
python -m bt_api_py.doctor --bundle core-reference --format json   # installed-plugin diagnosis

make test                          # scripts/run_tests.sh: all non-CTP tests, 8 xdist workers
make test-fast                     # excludes slow/network
./scripts/run_tests.sh -m "unit"   # marker selection; --ctp includes ctp-marked tests
pytest tests/test_doctor.py::test_name -q   # single test (pytest uses --import-mode=importlib)
pytest bt_api/bt_api_okx/tests -q  # a submodule's tests

make lint                          # ruff check bt_api_py tests
make lint-all                      # core + scripts + examples + bt_api/*/src,tests
make format                        # ruff format + fix (bt_api_py, tests)
make type-check                    # mypy bt_api_py
make quality-ratchet               # CI gate: ruff debt may only decrease
make format-ratchet                # CI gate: submodule format debt may only decrease
```

CI (`.github/workflows/tests.yml`) runs `ruff check` and `ruff format --check`, plus `mypy bt_api_py tests --ignore-missing-imports`, `bandit`, `pip-audit` and both ratchets. Python 3.11–3.13 block releases; 3.14 is canary only. Ruff line length is 100. Network or auth tests need exchange env vars (see `conftest.py`) and are not acceptance evidence.

## Docs and encoding

Chinese Markdown created on Windows for Windows tools defaults to UTF-8 with BOM. Several existing files, including README.md, already start with a BOM. Keep each file's existing encoding. Check that it decodes as strict UTF-8 with no replacement characters.
