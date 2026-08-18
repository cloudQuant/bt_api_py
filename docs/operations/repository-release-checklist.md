# Repository Release Checklist

Checklist executed before tagging or publishing a `bt_api_py` release. Each item
must be verifiable from a machine-readable artifact or a command, not from a
local-only claim.

## 1. Repository baseline

- [ ] `python scripts/verify_repository_baseline.py --json docs/acceptance/<date>-baseline-inventory.json` exits 0.
- [ ] The generated manifest shows every `.gitmodules` path with a matching `pinned_commit` and `checked_out_commit`.
- [ ] `pin_mismatch_submodules` is empty, OR every entry has an owner decision recorded (see `docs/plans/` open-decision register D-01).
- [ ] `dirty_submodules` is empty, OR every entry is a deliberate local-only change not required for the release.
- [ ] `bt_api/bt_api_ctp` gitlink divergence (`a8a3792` vs working-tree `b1e21b3`) has an explicit maintainer decision: pin the working tree, or restore the parent-recorded commit.

## 2. Plugin inventory

- [ ] `plugin_count` in the manifest matches the intended bundle set for this release.
- [ ] No fixed plugin count (e.g. `>=61`) is used to claim support; each plugin has an `installed`/`certified` status.

## 3. Quality gates

- [ ] `ruff check bt_api_py tests` exits 0 on core paths (`bt_api.py`, `_contracts`, `forwarding`, `gateway`, `broker`).
- [ ] `ruff format --check bt_api_py tests` exits 0.
- [ ] `mypy bt_api_py tests --ignore-missing-imports` exits 0 on core paths.
- [ ] Offline pytest baseline (`pytest tests -m "not network and not integration and not performance and not e2e and not ctp"`) exits 0.
- [ ] No `# type: ignore`, ruff `noqa`, or skip was added to silence a real failure.

## 4. Remote verification

- [ ] All local commits are pushed to the remote.
- [ ] Remote CI shows a green build for the release commit.
- [ ] All submodule pins referenced by the release are pushed and visible in the child repositories.

## 5. Forwarding gateway safety

- [ ] `GatewayConfig` defaults to read-only + loopback/IPC (`enable_trading=False`).
- [ ] Remote TCP or write-enabled configuration requires an explicit safe policy and raises otherwise.
- [ ] No production API keys are committed, logged, or embedded in the release artifacts.
