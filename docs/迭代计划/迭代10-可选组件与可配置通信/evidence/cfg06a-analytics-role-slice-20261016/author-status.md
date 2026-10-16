# CFG06a — portfolio_analytics execution role vertical slice (iteration 10) — status

Date: 2026-10-16. Branch: dev (parent repo `D:\bt_api_py` + risk submodule `bt_api\bt_api_risk`).
State: GREEN. No commits made; no doc edits; `service_host.py` untouched; DIM01 WIP files untouched.

The iteration-10 gap clause "portfolio_analytics has no real SDK registration (no full
analytics worker)" is closed with a minimal but real slice: a durable, SQLite-backed
analysis task service in the risk subpackage plus a fixed-table SDK builtin
`("risk", "portfolio_analytics")` on public port `service.risk_analysis`, covered by a
real in-process ZMQ+CURVE control-plane test.

## Files changed

| file | sha256 | git blob id (git hash-object) | bytes |
|---|---|---|---|
| `D:\bt_api_py\bt_api_py\service_components.py` (modified, +87/-0) | `77d61c3f61177735663355f07f37d77391f2bef09b4df85818f8084162a8e161` | `a6c4b189b07b8e9202244f1944c3600d606c3a31` | 100398 |
| `D:\bt_api_py\tests\unit\test_service_portfolio_analytics_control_plane.py` (new) | `8114606c7de61deebdcefa8130fc4dd8df16783486fc120c6eb8341a2dfb383e` | `1c157adad4ade9c4b57406f71043ba619cba79af` | 66464 |
| `D:\bt_api_py\bt_api\bt_api_risk\src\bt_api_risk\service_analytics.py` (new) | `5f7f9fadc4c38304609745f4143620081b45450cb14c52c0801473debd7c07ac` | `c7b9da3473f7f7452bf3ce89a4db36bc52ca0196` | 41738 |

Blob ids for parent-repo files come from the parent object store; the submodule blob id
comes from `git -C bt_api/bt_api_risk hash-object src/bt_api_risk/service_analytics.py`.
The `service_components.py` diff contains exactly four scoped hunks: the
`("risk","portfolio_analytics")` `_Builtin` table entry, the `build_component` adapter
branch (`ANALYSIS_STORE_INVALID` cross-checks against validated options), the
`SUPPORTED_OPERATIONS` identity check (`BUILTIN_COMPONENT_INVALID`), and the `maintain`
hook wiring for the owner-thread task executor. `src/bt_api_risk/__init__.py` was not
touched (no `_OPTIONAL_EXTRAS` change needed).

## Exact test commands

Environment (Git Bash), Python `C:\anaconda3\python.exe`:

```
PYTHONPATH=D:\bt_api_py;D:\bt_api_py\bt_api\bt_api_base\src;D:\bt_api_py\bt_api\bt_api_risk\src;D:\bt_api_py\bt_api\bt_api_execution\src;D:\bt_api_py\bt_api\bt_api_transport_zmq\src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONDONTWRITEBYTECODE=1
PYTHONNOUSERSITE=1

C:\anaconda3\python.exe -m pytest -c D:\temp\it10cfg06-scratch\pytest-risk-fx.ini --rootdir D:\bt_api_py -q --noconftest --import-mode=importlib -p no:cacheprovider <files>
```

1. New suite (final, verbose r5):
   `... tests/unit/test_service_portfolio_analytics_control_plane.py`
   → **6 passed, 1 warning** (2.78s). Repeats r2/r3/r4: 6 passed each (deterministic
   clock-gated wire progression). Test names:
   `test_portfolio_analytics_real_registration_control_plane`,
   `test_portfolio_analytics_options_fail_closed`,
   `test_analysis_port_rejects_other_roles_and_bad_options_before_resources`,
   `test_analysis_task_store_fail_closed_semantics`,
   `test_build_component_binds_the_analysis_task_store`,
   `test_base_install_serves_portfolio_analytics_without_numpy_or_sklearn`.
2. Committed CFG06 regression (r2 verbose):
   `... tests/unit/test_service_policy_catalog_control_plane.py tests/unit/test_service_components_group_runtime_bundle.py`
   → **6 passed, 1 warning** (5.19s); both accepted-evidence files unmodified and green.
3. Risk submodule primitive sanity:
   `... bt_api/bt_api_risk/tests/test_portfolio_analytics.py` → **17 passed** (0.22s).

The single warning in every wire run is the pre-existing zmq Proactor
`add_reader` RuntimeWarning on Windows, identical to the committed CFG06 suites.

Raw logs (fresh dir `D:\temp\it10cfg06a-analytics-dev-20261016-r1\`):
- `pytest-analytics-r1.log` — first run: 2 failed, 4 passed (findings below, fixed).
- `pytest-analytics-r2.log` / `-r3.log` / `-r4.log` / `-r5-v.log` — 6 passed (r5 verbose).
- `pytest-cfg06-regression-r1.log`, `-r2-v.log` — 6 passed.
- `pytest-risk-analytics-primitive-r1.log` — 17 passed.

First-run findings (both fixed in the test, service/SDK code was already correct):
(a) wire decoding freezes payloads (`bt_api_base.service_protocol` converts decoded
JSON arrays to tuples), so wire-read `reason_codes` assert as tuples
(`()` / `("MEMBERSHIP_VERSION_MISMATCH",)`); (b) `parse_service_config` already gates
the analysis option schema at parse time (`MISSING_FIELD`, `UNKNOWN_FIELD`,
`INVALID_VALUE`, `DUPLICATE_VALUE`, `VERSIONED_REFERENCE_REQUIRED`,
`PORT_ROLE_MISMATCH`), so the host-level negative now records both refusal layers:
the SDK config gate and the independent risk-package validator gate
(`COMPONENT_OPTIONS_INVALID` via `validate_components` for an out-of-band role claim).

## Design summary

Operations (fixed four) on public port `service.risk_analysis`; resource key
`"analysis"`; write operations = `analysis.input_upload`, `analysis.submit`,
`analysis.cancel`; read = `analysis.query`.
- Scope: exact `WireScope` per principal with `portfolio_ref` required and `run_id`
  unset; every store row is keyed by the canonical scope JSON.
- `analysis.input_upload` (write): source mode must be in `allowed_source_modes`
  (subset of `research`/`imported`/`paper`; no account-login/live modes; facts are
  offline research/imported/paper documents), facts validated by the existing
  `portfolio_analytics` parsers, digest = sha256 over the canonical facts payload
  (reproducible: re-upload with equal digest is idempotent; mismatched digest for an
  existing snapshot fails closed with `INPUT_ALREADY_COMPLETE`/`INPUT_DIGEST_MISMATCH`;
  facts whose portfolio does not match the scope → `INPUT_PORTFOLIO_MISMATCH`).
- `analysis.submit` (write): creates a `pending` task; identical resubmit returns the
  identical projection; divergent resubmit → `TASK_REF_CONFLICT`; per-scope quota
  (bounded concurrent pending+running tasks, `max_concurrent_tasks`) exceeded →
  `TASK_QUOTA_EXCEEDED`, fail-closed.
- Executor: deterministic state machine on the host owner thread via the `maintain`
  hook — at most one transition per second of the store clock
  (`pending → running → succeeded | unavailable | failed`). `succeeded` payload is
  computed by the existing `PortfolioAnalyticsService` snapshot primitive over the
  stored inputs; identical inputs reproduce the byte-identical result payload
  (asserted over the wire between two different task refs).
- `analysis.query` (read): never mutates; returns the finite projection
  (`task_ref`, `state`, `input_snapshot_id`, `input_sha256`, `created_ns`, `state_ns`,
  `detail_code`, `result`).
- `analysis.cancel` (write): idempotent on `cancelled`; `TASK_TERMINAL` on any other
  terminal state (terminal states are fixed); `TASK_NOT_FOUND` across scopes.
- Expiry: pending task older than `pending_ttl_seconds` → `expired` with
  `detail_code=TASK_EXPIRED`, `result=None`, swept ungated by `maintain`.
- Errors are constant code strings only (no caller values echoed). Module codes:
  `UNKNOWN_OPERATION`, `INVALID_REQUEST`, `CLOSED`, `INVALID_SCOPE`,
  `INVALID_REFERENCE`, `INVALID_INPUT`, `INVALID_CLOCK`, `ANALYSIS_STORAGE_UNAVAILABLE`,
  `ANALYSIS_EXECUTION_FAULT`, `INPUT_LIMIT_EXCEEDED`, `SOURCE_MODE_NOT_ALLOWED`,
  `INPUT_PORTFOLIO_MISMATCH`, `INPUT_NOT_FOUND`, `INPUT_ALREADY_COMPLETE`,
  `INPUT_DIGEST_MISMATCH`, `TASK_REF_CONFLICT`, `TASK_QUOTA_EXCEEDED`,
  `TASK_NOT_FOUND`, `TASK_TERMINAL`, `TASK_STATE_INVALID`, `TASK_EXPIRED`,
  `TASK_INTERRUPTED`, `TASK_EXECUTION_FAILED`, `INVALID_ANALYSIS_TASK_STORE`,
  `INVALID_STORE_PATH`, `INVALID_MODEL_PROFILE`, `INVALID_SOURCE_MODES`,
  `DUPLICATE_SOURCE_MODE`, `INVALID_TASK_QUOTA`, `INVALID_TASK_TTL`. SDK build codes:
  `ANALYSIS_STORE_INVALID` (exact-type store check plus profile_ref / source modes /
  quota / max_input_bytes agreement with the validated options),
  `BUILTIN_COMPONENT_INVALID` (supported-operations identity, missing maintain).
  Config/validator-layer codes: `MISSING_FIELD`, `UNKNOWN_FIELD`, `INVALID_VALUE`,
  `DUPLICATE_VALUE`, `VERSIONED_REFERENCE_REQUIRED`, `PORT_ROLE_MISMATCH`,
  `UNKNOWN_ROLE`, `INVALID_OPTIONS`, `INVALID_OPTION_VALUE`, `INVALID_SOURCE_MODES`,
  `DUPLICATE_SOURCE_MODE`.
- Options schema (`role=portfolio_analytics`): `model_profile_ref` (versioned ref,
  required), `allowed_source_modes` (non-empty mode subset, no duplicates, required),
  optional `max_concurrent_tasks` (int 1..64), optional `max_input_bytes`
  (int 1..64 MiB). Unknown keys/values rejected fail-closed at both layers.
- Authorization: per-action `PrincipalGrant`/`ResourceGrant`; a reader granted only
  `analysis.query` registers exactly that route and its write calls are refused
  locally (`AMBIGUOUS_OR_MISSING_ROUTE`); a binding whose exact `scope_ref` carries
  no grant fails `SCOPE_DENIED` at `runtime.start()`. Base install (no
  numpy/sklearn/scipy/pandas importable) serves all four actions (subprocess
  meta-path boundary test).
- Durability: SQLite `analysis_inputs` + `analysis_tasks`; on open, interrupted
  `running` tasks are recovered as `failed`/`TASK_INTERRUPTED` (never silently
  resumed or lost).

## Remaining for a later acceptance round

- Fixed-source spec nodes (plan-level fixed analysis request/source placement) on
  top of this port surface.
- Cross-process service run (AFI04): run the analytics role as a separate service
  process in the approved-target topology.
- The `group_policy` control-plane role remains explicitly OUT of scope here (no SDK
  builtin yet; recorded as a scoped gap by the accepted policy-catalog suite).

## Known limits

- Production pacing: the single owner-thread executor advances at most one task
  transition per store-clock second (no per-task parallelism beyond the bounded
  concurrent quota).
- Uploads are single-shot whole documents bounded by `max_input_bytes`; no chunked
  or resumable upload.
- Crash recovery marks interrupted running tasks failed (`TASK_INTERRUPTED`); there
  is no resume of partial work.
- The analysis result reuses the existing snapshot primitive; no optional heavy
  dependencies (numpy/sklearn) are imported anywhere in this path.
