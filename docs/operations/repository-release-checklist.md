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
- [ ] Offline pytest baseline (`python -m pytest tests -q -n 8 -m "not network and not integration and not performance and not e2e and not ctp"`) exits 0.
- [ ] No `# type: ignore`, ruff `noqa`, or skip was added to silence a real failure.

## 4. Remote verification

- [ ] All local commits are pushed to the remote.
- [ ] Remote CI shows a green build for the release commit.
- [ ] All submodule pins referenced by the release are pushed and visible in the child repositories.

## 5. Forwarding gateway safety

- [ ] `GatewayConfig` defaults to read-only + loopback/IPC (`enable_trading=False`).
- [ ] Remote TCP or write-enabled configuration requires an explicit safe policy and raises otherwise.
- [ ] No production API keys are committed, logged, or embedded in the release artifacts.

## 6. Release candidate identity and promotion

- [ ] A manual `workflow_dispatch` was treated as build-only evidence; it did not write TestPyPI or PyPI.
- [ ] `expected_sha` is a complete lowercase 40-character SHA, equals the checkout HEAD, and is reachable from `master`.
- [ ] `dist/` contains exactly one wheel and one sdist, with no extra files or symbolic links.
- [ ] `dist-meta/release-candidate.json` and `SHA256SUMS.txt` bind the source SHA, version, wheel/sdist filenames, sizes, SHA256 values, and wheel-contract receipt.
- [ ] Build outputs expose the source SHA, version, wheel/sdist filename and SHA256, plus manifest filename/SHA256; every publishing job verifies the original artifact against those independent values before use.
- [ ] The production chain is one run with one build: `build → publish-testpypi → smoke-install-testpypi → publish-pypi`. No downstream job rebuilds distributions.
- [ ] TestPyPI smoke uses bounded retries, a fresh empty directory, disabled cache, and an exact-version wheel-only download; it rejects a second wheel, a different filename, or a different SHA256.
- [ ] The smoke environment installs the verified local wheel. Dependencies resolve only from PyPI; TestPyPI is not an extra dependency index.
- [ ] PyPI publication depends on build, TestPyPI publication, and smoke, then re-verifies the original candidate immediately before publication.
- [ ] No publish path uses `skip-existing`, `continue-on-error`, `|| true`, or a missing-artifact fallback.

## 7. External release admission (D4)

- [ ] `pypi` and `testpypi` GitHub Environments exist and their protection settings are recorded.
- [ ] PyPI/TestPyPI trusted publishers exactly match this repository, `.github/workflows/publish.yml`, and the environment names.
- [ ] The `v*` tag Ruleset and bypass actors are verified from the remote API.
- [ ] All third-party actions in the privileged workflow are pinned to officially verified immutable full commit SHAs; mutable `@vN`/`@release/v1` references are not accepted.
- [ ] Release build tools and all transitive dependencies are installed from a reviewed hash-locked file; no floating `pip install build twine` runs before candidate digests are recorded.
- [ ] Candidate verification runs without `id-token: write`; OIDC publishing jobs do not checkout or execute candidate repository code and consume only the verified immutable artifact/digest.
- [ ] `ENABLE_PYPI_RELEASE=true` is set only for the approved release window. Missing or false must skip every external registry-writing job.
- [ ] Hosted-run evidence confirms the exact TestPyPI wheel filename/SHA256, successful fresh-venv smoke, and re-verification before PyPI.
- [ ] The release record notes that `release: published` creates GitHub Release metadata before registry smoke; a failed chain is withdrawn or clearly marked failed and must not be described as a successful package release.

Until every D4 item and the hosted same-candidate run are complete, the release status is **PARTIAL / NO-GO** even when local candidate and workflow tests pass.
