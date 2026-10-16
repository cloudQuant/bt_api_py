# MIG01-R3 wire round — independent adversarial review (2026-10-16)

Reviewer: independent agent. Read-only on the repo (no Edit/Write on tracked files, no git add/commit/stage,
no daemons). Writes made: this file + evidence under `D:\temp\it10mig01-r3-review-20261016-r1\`.
Producer artifacts read: `.codex-tmp/mig01-r3-report.md`, `.codex-tmp/mig01-r3-status.md`,
`D:\temp\it10mig01-r3-wire-20261016-r1\{pins,diffs,junit,logs}`.

## Verdict: GO-with-disclosures

The change set that would be committed is verified end-to-end in this worktree:

- All 5 changed files + `_normalization.py` match the recorded hashes (Check 1).
- Wire module is self-contained (zero `bt_api_gateway` / `bt_api_py.forwarding` imports); outcome
  normalization emits exactly the 6 contract keys; the unknown-outcome `execution_unknown=True` fix is
  genuinely present; no exception-text projection path found (Check 2).
- `_direct_backend.py` diff is purely additive; wire branch taken only with `client is not None`;
  probe fallback swallows errors; client=None path has no modified lines (Check 3).
- `bt_api.py` raise condition is exactly `ZMQ and gateway_wire_config is None`; ZMQ-alone and
  ZMQ+ForwardingConfig still raise; client assembled only from `GatewayWireConfig` (Check 4).
- Both test re-homes are exactly 2 hunks each (+2/-3), all import lines and the config kwarg; no
  assertion/harness body touched (Check 5).
- Independent re-run: **17 passed, exit 0** (junit tests=17, failures=0, errors=0, skipped=0;
  split 14 T4 + 3 T3) (Check 6).
- Independent regression-A re-run: **76 passed, 1 skipped, exit 0** (junit tests=77, skipped=1;
  skip = known plugin-autoload artifact) (Check 7).
- Scope clean; nothing staged; `_normalization.py` not in status (Check 8).

DISCLOSURE (single, bookkeeping-only, does not change the commit content): the report's T4
**pre-edit** pin `27f30fd7908a38a4c4cea1a5e5f8f280907618385fa610dc290685c3dfd4b892` is **not
reproducible** from any state of the T4 file derivable from HEAD (details in Check 1/5). The value
first appears in `.codex-tmp/mig01-completion-recon.md:7`, i.e. it was copied from the recon doc, not
measured from this worktree. All other 4 before-pins reproduce exactly. The post-edit hashes are exact
and the net change HEAD→worktree for T4 equals exactly the two advertised hunks, so the committed
artifact itself is what was reviewed; only the "pre-edit pin verified" statement for T4 is unsupported
and must not be reused as evidence in later rounds.

---

## Check 1 — Hashes (PASS, with T4 before-pin caveat)

Command (from `D:\bt_api_py`), exact output (also in `ev/pins/reviewer-hashes.txt`):

```
sha256sum bt_api_py/_gateway_wire.py bt_api_py/_direct_backend.py bt_api_py/bt_api.py bt_api_py/_normalization.py \
  tests/bt_api_contract/test_gateway_runtime_unknown_execution.py tests/bt_api_contract/test_execution_session.py
85c8ce4514308bf4fbc0cb3d636f98510243df77b9151b4e9ca5da7536b3616c *bt_api_py/_gateway_wire.py
8e28ff14ad1644c8bd272aa7740b54906cc7af2fac2cbb3284d3735944d4e26d *bt_api_py/_direct_backend.py
91363d1ced5b032a61152a8c9af7c7a26e73a9ab2f7990dd7a69d7ba202caab4 *bt_api_py/bt_api.py
90bde31374395e49eef13886c1b6c345cd002e85974798c91fbc19c2aaf37a25 *bt_api_py/_normalization.py
50da4a0d5a9abe774af76653e67fb5ded15e3fbdf2cc7ddcea0acb12fef06d44 *tests/bt_api_contract/test_gateway_runtime_unknown_execution.py
e83f6b2dc973830531dcfa899f279598adddbdfad71d8e65d2f91e210b7a081b *tests/bt_api_contract/test_execution_session.py
```

All 6 == mission/Producer post pins. `_normalization.py` == its unchanged pin (and is absent from
`git status`).

Before-pins audit. This worktree uses `core.autocrlf=true`; worktree files are CRLF while git blobs are
LF, so the producer's pins are shas of raw (CRLF) worktree bytes. Reproducing "pre-edit = HEAD content
in CRLF" from `git show HEAD:<file>`:

| file | CRLF(HEAD) sha256 | claimed before pin | match |
|---|---|---|---|
| _direct_backend.py | 003a016bd883a56acf420988a1c8acddf5021d7a06909e1dcc752a81bc681073 | 003a016b… | YES |
| bt_api.py | abceffc22168442aa6325b43391f14c0f5c0540202ddedeeaad6041fdd5e33f0 | abceffc2… | YES |
| test_execution_session.py | 6496100809f750ee88cb0c9d9055ae0c8a0878688711c8eb3879644d5deb99af | 64961008… | YES |
| test_gateway_runtime_unknown_execution.py | 1240f28f0dceec89313230a14788936812b6a80f5d86a2377240abc0660fd8e8 | 27f30fd7… | **NO** |
| _normalization.py | 90bde31374395e49eef13886c1b6c345cd002e85974798c91fbc19c2aaf37a25 | 90bde313… | YES |

T4 alternatives tried and rejected (no match for 27f30fd7…): HEAD LF (0d178b6d…), HEAD CRLF-with-BOM,
LF/CRLF with/without trailing newline; 819 mixed-EOL variants (one LF-run among CRLF, one/two
separator exceptions); 12 partial re-home content variants (import-only re-home, kwarg-only re-home,
ForwardingConfig-import-restored variants); neighboring files `test_gateway_runtime_integration.py`
(6cda8600…). Conclusion: 27f30fd7… does not correspond to any plausible pre-edit state I can construct;
it is a stale recon-doc value (mig01-completion-recon.md:7), not a measurement of this worktree.

## Check 2 — `_gateway_wire.py` static audit (PASS)

- Imports: the only local import is `from bt_api_py._contracts.errors import CapabilityNotSupportedError,
  NormalizedApiError` (line 29). `grep -n "import bt_api_gateway|from bt_api_gateway|forwarding"` matches
  only docstring/text mentions at lines 5, 6, 144, 216, 417, 441 — all clearly non-import. ZERO imports
  of `bt_api_gateway` or `bt_api_py.forwarding`. PASS.
- 6-key contract: all outcomes flow through `_outcome_row` (line 509) returning exactly
  `{status, execution_unknown, terminal_confirmed, definite_reject, order_id, client_order_id}`.
  Runtime spot-check (all True for `set(row)==keys`):
  `unknown: status='submitted', execution_unknown=True`; `definite: status='rejected', definite_reject=True,
  terminal_confirmed=True`; `normal: status='accepted', order_id='o1'`;
  `probe-None (outcome_from_dispatch_status(None)): status='submitted', execution_unknown=True`.
- Claimed bug fix PRESENT: `_unknown_outcome` (line 529-531) calls
  `_outcome_row("submitted", execution_unknown=True, order_id=None, command=command)`. Read from source.
- Redaction: every raise in the module uses a static message; `except (TypeError, ValueError) as error`
  chains use `from error`/`from None` without projecting text; no logging, no `str(exc)`/`repr(exc)`.
  `_outcome_row` stringifies only `status`/`order_id`/`client_order_id` fields, never exception data.
  The T4 `NEVER_PRINT` assertions (result and `runtime._recent_errors`) passed in my independent run.

## Check 3 — `_direct_backend.py` (PASS)

`git diff` (my capture, identical to producer's `diffs/_direct_backend.diff`): the change is
**additive-only** (no `-` lines anywhere):

- ctor: `+ client: Any = None` and `+ self._client = client`.
- new `_wire_write`: builds intent, `client._send_command_sync(command)`; on `except Exception:`
  (bare, no name) probes `client.command_status(command.command_id)` inside a second
  `try/except Exception` that sets `status_row = None` (swallow verified in source), then returns
  `outcome_from_dispatch_status(status_row, command=command)`. No exception text captured/projected,
  no logging.
- `make_order`/`cancel_order`: `if self._client is not None: return self._wire_write(...)` added as the
  only change; `client=None` falls through to the pre-existing untouched lines.
- added `poll_event`/`close` (raise `CapabilityNotSupportedError`/inert when client is None). Adversarial
  cross-check on the caller side (`bt_api.py` unchanged regions): `_close_transports()` calls
  `self._backend.close()` only in the `ZMQ` branch (line 7936), `_poll_event_raw` calls
  `self._backend.poll_event(...)` only in the `ZMQ` branch (line 3599); the DIRECT branch uses
  `data_queues` and closes feeds, so client=None direct-mode semantics are unchanged. In plain
  ZMQ-without-wire mode the backend never gets constructed (raise fires first), so the new methods are
  unreachable with client=None. PASS.

## Check 4 — `bt_api.py` (PASS)

Diff hunks verified:

- ctor adds `gateway_wire_config: GatewayWireConfig | None = None`; dict coercion
  `if isinstance(gateway_wire_config, dict): gateway_wire_config = GatewayWireConfig(**gateway_wire_config)`.
- `_build_backend` raise condition is exactly `if transport_mode is TransportMode.ZMQ and
  gateway_wire_config is None:` — so ZMQ alone and ZMQ+ForwardingConfig still raise
  `LegacyForwardingRetiredError` (both have wire config None); the legacy test
  `test_zmq_transport_mode_raises_retired_migration_error` and
  `test_zmq_initialization_retired_and_never_creates_direct_feed` are green in regA.
- Client assembly: `client = GatewayWireClient(gateway_wire_config) if gateway_wire_config is not None
  else None` — only from `GatewayWireConfig`; `GatewayWireClient.__init__` type-checks
  (`TypeError` otherwise), so a mis-passed `ForwardingConfig` fails closed. `forwarding_config` is
  unused for client assembly.

## Check 5 — Test re-homes (PASS with T4 pre-pin disclosure)

Both files: `git diff --numstat` = `2 insertions, 3 deletions`, exactly 2 hunks each. Every hunk is an
import line or the config kwarg. Quoted (my captures, identical to producer's `diffs/*`):

T4 `test_gateway_runtime_unknown_execution.py` hunk 1 (:11):

```
 from bt_api_py._contracts.models import (
     CancelOrderRequest,
-    ForwardingConfig,
     OrderRequest,
     OrderType,
     Side,
 )
-from bt_api_py.forwarding.schema import CommandAck, OrderCommand
+from bt_api_py._gateway_wire import CommandAck, GatewayWireConfig, OrderCommand
```

T4 hunk 2 (:35):

```
         transport_mode="zmq",
-        forwarding_config=ForwardingConfig(
+        gateway_wire_config=GatewayWireConfig(
             command_endpoint="inproc://c",
```

T3 `test_execution_session.py` hunk 1 (:3682, inside the pinned node):

```
     from bt_api_base.gateway.config import GatewayConfig
     from bt_api_base.gateway.runtime import GatewayRuntime
 
-    from bt_api_py import ForwardingConfig
-    from bt_api_py.forwarding.schema import CommandAck, OrderCommand
+    from bt_api_py._gateway_wire import CommandAck, GatewayWireConfig, OrderCommand
```

T3 hunk 2 (:3711):

```
         transport_mode="zmq",
-        forwarding_config=ForwardingConfig(
+        gateway_wire_config=GatewayWireConfig(
             command_endpoint="inproc://c",
```

Byte-preservation evidence: for T3 the pre-pin reproduces exactly as CRLF(HEAD) → the pre-edit file
equaled HEAD content → the change is exactly these hunks → every assertion/harness byte untouched
(verified additionally by reading the node region 3670-3760 including its final assertions and
`api.close()`). For T4 the pre-pin is unreproducible (Check 1), but reconstruction proves
CRLF(HEAD + these exact 2 hunks) == current file == post pin 50da4a0d…, so the NET change to any file
that gets committed is exactly imports+kwarg; no assertion body differs from HEAD. The residual, purely
theoretical possibility that the pre-edit worktree deviated from HEAD in ways these hunks coincidentally
normalized cannot be excluded because the correct pre-pin cannot be checked — this is the disclosure.

## Check 6 — Independent combined re-run (PASS)

ps snapshot taken immediately before (SCN02 drill PID 13961 active; other sessions' transient
`C:\anaconda3\python.exe` workers observed — no interference; suite runs 2.13s in-process; one suite at
a time). Exact command, from `D:\bt_api_py`:

```
PYTHONPATH='D:\bt_api_py;...\bt_api_base\src;...\bt_api_execution\src;...\bt_api_risk\src;...\bt_api_transport_zmq\src'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
C:/anaconda3/python.exe -m pytest \
  D:/bt_api_py/tests/bt_api_contract/test_gateway_runtime_unknown_execution.py \
  D:/bt_api_py/tests/bt_api_contract/test_execution_session.py::test_session_uses_real_gateway_command_roundtrip_with_native_mt5_intent \
  -p no:cacheprovider --no-header -q --basetemp=ev/pytest-tmp --junitxml=ev/junit/combined-review.xml
```

Result: `EXIT_CODE=0`; stdout `17 passed, 1 warning in 2.13s` (log `ev/logs/combined-review.log`).
Junit `combined-review.xml`: `errors="0" failures="0" skipped="0" tests="17"`; classname split
14× `test_gateway_runtime_unknown_execution` + 3× `test_execution_session` — exactly the claimed
14+3=17. Producer's own junit cross-check: combined=17, t4-retry2=14, t3=3, all 0F/0E — consistent.

## Check 7 — Regression-A batch re-run (PASS)

Same env/ps discipline (run 4.52s). Targets: `tests/bt_api_contract/test_direct_backend_runtime_contract.py`,
`tests/bt_api_contract/test_direct_zmq_bt_api_contract.py`,
`test_normalized_public_api.py::test_zmq_initialization_retired_and_never_creates_direct_feed`,
`tests/test_light_import.py`, `tests/test_type_hint_introspection.py` (note: the last two live at the
`tests/` root, not under `bt_api_contract/`; my first attempt with a wrong path exited 4, corrected on
retry — recorded in `ev/logs/regA-review.log` history).

Result: `EXIT_CODE=0`; stdout `76 passed, 1 skipped, 2 warnings in 4.52s`. Junit `regA-review.xml`:
`errors="0" failures="0" skipped="1" tests="77"`. The single skip is
`test_direct_async_operations_keep_the_legacy_feed_boundary` with message
`async def function and no async plugin installed (see warnings)` — the known artifact of disabling
plugin autoload, identical in the producer's `regA.xml`. Counts match the claim exactly
(76P/1S, exit 0).

## Check 8 — Scope (PASS with 2 extra pre-existing untracked entries)

`git status --porcelain` (full, `ev/logs/git-status-full.txt`):

```
 M bt_api/bt_api_risk
 M bt_api_py/_direct_backend.py
 M bt_api_py/bt_api.py
 M tests/bt_api_contract/test_execution_session.py
 M tests/bt_api_contract/test_gateway_runtime_unknown_execution.py
 M tests/integration/test_arb02_group_budget_service.py
 M tests/unit/test_load01_hundred_strategy_30min.py
?? %SystemDrive%/
?? .codex-tmp/
?? 0
?? bt_api_py/_gateway_wire.py
```

Expected set present; `_normalization.py` NOT in status; `git diff --cached --name-only` is empty
(nothing staged). `bt_api/bt_api_risk` dirty state = only its own internal `?? .codex-tmp/`. Two
entries beyond the expected list: `?? %SystemDrive%/` (Windows caches, mtime Oct 15 16:28) and
`?? 0` (65-byte file, mtime Oct 15 20:24) — both predate today's round by ~a day, unrelated; exclude
from any staging. Producer's other in-flight files (arb02/load01 tests) untouched, as required.

Post-review addendum (status re-captured at 14:03, `ev/logs/git-status-post-review.txt`): two NEW dirty
entries appeared between 13:57 and 14:03 — ` M bt_api/bt_api_monitor` and
` M tests/integration/test_arb03_dim05_group_partial_fill_service.py` — plus `bt_api/bt_api_risk` still
dirty. These correlate with other concurrently running sessions (ARB03/DIM05 in-flight tasks), NOT with
this review or the R3 round; the reviewer made no writes to them. The 6 reviewed files' hashes were
re-verified unchanged after all review runs. The root agent must scope any commit to the R3 file set and
leave these concurrent entries alone.

## Check 9 — Serialization discipline (PASS)

`ps -W | grep -i python` snapshotted immediately before each of the two pytest invocations: SCN02
drill PID 13961 (started 13:44) continuously active; several transient `C:\anaconda3\python.exe`
workers from other sessions appeared/disappeared; no conflict observed. Each suite ran in-process in
2.1s/4.5s, strictly one at a time; nothing heavy started after 15:00 (all runs 14:01-14:02 Beijing).

---

## Claims NOT independently verified (and why)

1. **T4 pre-edit pin = 27f30fd…** — falsified as a measurement of this worktree (Check 1); recorded as
   disclosure rather than blocking, because the commit content is fully verified via the post pin +
   reconstruction + green re-runs.
2. Producer regressions B and C (64P/1S operation_dispatch/environment_guard; 184P/2S full T3 file) —
   their junit evidence exists in the producer dir but was not re-run by me (out of the mandated review
   scope/time budget). RegA (the stated highest-risk batch) was re-run and matched.
3. Producer's pre-fix failing state (t4-retry.xml 8F/6P) — not reconstructible now (fix is in place);
   the fixed behavior is what I verified.

## Residual risks for the root agent

- **Real-router path untested (R4/R5 scope)**: both re-homed tests still use
  `bt_api_base.gateway.runtime.GatewayRuntime` as the reference ack server and replace the assembled
  `GatewayWireClient` with a duck-typed stub (`api._backend._client = Client()`) before writing. The
  assembled client from config is instantiated but its dispatch path is never exercised (dispatch=None
  in tests). The real `bt_api_gateway` durable router remains unexercised, as disclosed by the producer.
- **`outcome_from_dispatch_status` row branches**: only the `None` row (probe missing) path is exercised
  by tests; the REJECTED/EXPIRED/FAILED → definite and SUCCEEDED/RETURNED_UNVERIFIED → normal branches
  of a real router status row have no direct test coverage yet.
- **`gateway_wire_config` dict-coercion path** (ctor `GatewayWireConfig(**dict)`) is untested.
- `DirectBackend.poll_event`/`close` when client=None raise CapabilityNotSupportedError / are inert —
  reachable only via ZMQ+wire config sessions; plain direct-mode callers do not touch them (verified).
- T4 pre-pin bookkeeping discrepancy (above): recommend correcting the pin record if this round is
  archived, and not citing 27f30fd… as "worktree-verified" anywhere.

## Evidence index (reviewer-generated)

- `D:\temp\it10mig01-r3-review-20261016-r1\pins\reviewer-hashes.txt` — Check 1 sha256sum output.
- `...\logs\git-status-full.txt` — Check 8 raw status.
- `...\logs\combined-review.log` + `...\junit\combined-review.xml` — Check 6 (exit 0, 17 passed).
- `...\logs\regA-review.log` + `...\junit\regA-review.xml` — Check 7 (exit 0, 76P/1S, skip reason in XML).
- `...\logs\diff-t4-review.txt`, `...\logs\diff-t3-review.txt`, `...\logs\diff-t3-stat.txt`,
  `...\logs\diff-direct-backend-review.txt`, `...\logs\diff-bt-api-review.txt` — Check 3/4/5 captures.
- `...\pytest-tmp`, `...\pytest-tmp-regA` — pytest basetemps (disposable).
