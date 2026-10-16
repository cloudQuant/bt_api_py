# CFG06a portfolio_analytics increment — independent static + artifact review

Date: 2026-10-16. Reviewer: independent static/artifact pass (READ-ONLY; no repo file
modified by this review; verdict file excluded). Scope: the uncommitted CFG06a increment
only — new `service_analytics.py`, the `service_components.py` +87/-0 hunk, the new
control-plane test — not a CFG06 acceptance claim.

## Verdict: GO

## Evidence verified

### 1. Hash / blob / size claims (all exact)
- `bt_api_py/service_components.py`: sha256 `77d61c3f…a8e161`, blob `a6c4b189…c3a31`, 100398 B,
  numstat +87/-0 — all match the status file.
- `tests/unit/test_service_portfolio_analytics_control_plane.py`: sha256 `8114606c…b383e`,
  blob `1c157adad4…79af`, 66464 B — match.
- `bt_api/bt_api_risk/src/bt_api_risk/service_analytics.py`: sha256 `5f7f9fad…c07ac`,
  blob `c7b9da3473…0196`, 41738 B — match (submodule object store).

### 2. Store semantics (`service_analytics.py`)
- Idempotent submit: identical input+request returns the stored projection, divergent reuse is
  `TASK_REF_CONFLICT` (:691-697); quota counts pending+running per scope
  (`WHERE scope_json=? AND state IN ('pending','running')`, :704-710); upload idempotent on equal
  digest, `INPUT_ALREADY_COMPLETE` on divergent digest (:637-644); cancel idempotent on
  `cancelled` and `TASK_TERMINAL` on other terminals (:765-769); fixed terminal set :65-67.
- Pending-TTL sweep ungated in `maintain` (`sweep_expired` :786-796, called :1016); interrupted
  `running` rows fail closed as `TASK_INTERRUPTED` on open (:579-583); all error codes are
  literal constants, no f-string/echoed caller value (grep over all `AnalysisTaskError(` sites
  and `_UnavailableError(` sites; `handle`/`maintain` return `{ok, code}` only, :997-1030).

### 3. Authorization is scope-bound and fails closed
- Handler consumes SDK-provided `context.principal_ref` / `context.scope` (`service_components.py`
  :1960-1965); every store key is the canonical JSON of an exact, run-free `WireScope` with
  `portfolio_ref` set (`_task_scope` :459-494). Cross-scope read is `TASK_NOT_FOUND` (:1379-1385
  of the test); wire suite asserts reader write refusals `AMBIGUOUS_OR_MISSING_ROUTE` (test
  :1053-1057) and foreign binding `SCOPE_DENIED` (test :1068).

### 4. +87 hunk wiring (exactly four hunks, nothing else)
- Builtin table entry with resource key `analysis` and port `service.risk_analysis`
  (`service_components.py` :602-627), including `validator_module_name` — required because
  `module_name` is `service_analytics` (:873-889). `build_component` adapter branch
  `ANALYSIS_STORE_INVALID` (exact store type + profile_ref/modes/quota/max_input_bytes agreement
  with validated options) :1456-1492; `SUPPORTED_OPERATIONS` identity check + intersection
  :1733-1744; owner-thread `maintain` wiring with callable check :2013-2023. `validate_components`
  independently enforces write-ops ⊆ module-advertised ops (:893-917), so the identity check is
  not the only gate; the test monkeypatches the module frozenset and observes
  `BUILTIN_COMPONENT_INVALID` (test :1539-1548). `git status`/`git diff` clean for
  `service_host.py`, `service_config.py` and both frozen CFG06 suites (verified).

### 5. Test substance (not tautological)
- 45 handled checks (`_HANDLED_CHECKS`, test :108) — hand-counted and consistent; real
  ZMQ+CURVE in-process host with deterministic clock-gated executor (:568-654, :846-1075);
  both refusal layers exercised: SDK `parse_service_config` (`MISSING_FIELD`, `INVALID_VALUE`,
  `UNKNOWN_FIELD`, `VERSIONED_REFERENCE_REQUIRED`, `DUPLICATE_VALUE`, `PORT_ROLE_MISMATCH`) and
  `COMPONENT_OPTIONS_INVALID` via `validate_components` with zero resource-provider calls
  (:1134-1266). Value assertions are against independently computed digests/literals; two
  distinct task refs reproduce byte-identical payloads (:993-996).

### 6. Import surface
- `service_analytics.py` imports stdlib + `bt_api_base.service_protocol` +
  `bt_api_risk.portfolio_analytics` + `bt_api_risk.service_component` only (:29-59); heavy deps
  exist only in `ml_models/*` / `core/risk_calculator.py`. `__init__.py` untouched; the subprocess
  meta-path blocker test (:1555-1743) passes on the base path.

### 7. Artifacts / honesty
- r1 log shows the documented first-run 2-failure (wire tuple freeze; parse-gate
  `MISSING_FIELD`) matching the status file's findings; r2/r3/r4/r5 6 passed each (test names in
  r5-v match); regression pair 6 passed unmodified (r1/r2-v); risk primitive 17 passed.
- Independent re-run by this review: new suite 6 passed (3.47s); frozen pair 6 passed (7.47s).
- Remaining-acceptance items (fixed-source nodes, AFI04 cross-process run, group_policy out of
  scope) and known limits are present in the status file; no CFG06 acceptance overclaim.
- Other dirty parent-repo files (`__init__.py` legacy-forwarding retirement, DIM01 WIP, risk-order
  work) and the submodule's `admission.py`/`exposure.py` diffs are unrelated WIP, not disturbed.

## Minor observations (non-blocking, no verdict impact)
- `INPUT_ALREADY_COMPLETE`, `INPUT_DIGEST_MISMATCH` and the `failed`/`TASK_EXECUTION_FAILED`
  executor path are implemented but not directly exercised by the suite (covered by review of
  :637-644, :1043-1045, :1065-1070).
- "Missing maintain" negative is code-enforced (:2013-2023) but only the positive is tested.
- Cross-scope isolation is asserted at adapter level (test :1379-1385); the wire suite's foreign
  principal is refused before registration. Adequate combined coverage.
- Store is single-process (no OS file lock), consistent with the documented single-owner-thread
  limit and the remaining AFI04 cross-process item.
