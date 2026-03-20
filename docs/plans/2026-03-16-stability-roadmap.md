# bt_api_py Long-Term Stability Roadmap

## Goal

Improve long-term stability by reducing configuration drift, tightening quality gates,
and making exchange support status easier to maintain.

## Phase 1: Foundation

1. Establish single sources of truth for package version and exchange support metadata.
2. Remove fragile command construction from developer tooling and CI entrypoints.
3. Expose package metadata consistently for docs, release checks, and users.

## Phase 2: Trustworthy Quality Gates

1. Tighten `mypy` incrementally for `registry`, `bt_api`, and top-traffic feeds.
2. Split CI into fast unit, network, CTP, and performance lanes with explicit ownership.
3. Track coverage and failure trends by module instead of only global pass/fail.

## Phase 3: Architecture Hardening

1. Refactor oversized feed modules into smaller domain-focused units.
2. Move script-style utilities out of importable library packages into `scripts/` or `examples/`.
3. Standardize library logging so runtime code avoids direct `print()` usage.

## Phase 4: Documentation Consistency

1. Generate exchange capability tables from shared metadata.
2. Archive stale improvement reports and keep one current roadmap.
3. Align README, docs index, release checklist, and support matrix from the same data.

## Suggested Execution Order

1. Version and metadata single-source cleanup.
2. Test/CI runner hardening.
3. Documentation consistency pass.
4. Mypy tightening on core modules.
5. Large-module refactors for the most frequently changed exchanges.
6. Library/script boundary cleanup.

## Done in This Iteration

1. Centralized package version in `bt_api_py/_version.py`.
2. Exported `bt_api_py.__version__` for docs and release checks.
3. Reworked `run_tests.sh` to build the pytest command with Bash arrays instead of `eval`.
