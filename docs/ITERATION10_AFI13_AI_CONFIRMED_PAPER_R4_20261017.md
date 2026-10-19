# Iteration 10 AFI13: AI explicit confirmation to managed Paper

Candidate delivery is based on SDK `dev` HEAD `473a8bc90ac3fa32472c0897811b12e3686e30f9`.
The selected Paper runtime overlay is
`.codex-tmp/afi13-paper-sdk-extract-r1/selected-paper-runtime.patch`
(24,174 bytes, SHA-256
`c8dd27d6e75b9c260b43f340f2410bfa8b460a336d300f7ffce12952983471dd`). It
changes only `bt_api_py/service_config.py` and `bt_api_py/service_host.py`:
the config accepts three explicit Paper composition modes and permits only the
exact configured Execution/Risk shared order-entry pair; Host construction
validates the trusted Paper domain and matching Risk objects, and the
`execution_risk_monitor` mode relays persisted Execution journal events to the
single exact Monitor append binding on the owner thread, with readiness and
normal cleanup checks. The already accepted Paper resource/registry policy
hunks in `service_components.py` and `service_registry.py` are dependencies in
the base, not part of this selected overlay. Other mixed-file work is excluded.

The SDK overlay also depends on an Execution validator addition present in the
mixed child worktree but absent from its committed `cf6a34c` source. The
selected child change was committed on Execution `dev` as
`4374237705478039d60760e5f68df0e6ed076907`, parent
`cf6a34cd25aea0a42c7814843c63195575bfeb11`; it contains only
`bt_api/bt_api_execution/src/bt_api_execution/service_component.py` (candidate
SHA-256
`5c6ed2a8aab61be0a8e02af34ef25e49853184a8a152f84e22e26ad013caadaf`) and
`bt_api/bt_api_execution/tests/test_service_component.py` (candidate SHA-256
`bc21489e56475759c59654d03df7ea5fe68d54442b8c67f43bb9f2c3539937d1`). It
validates `paper_composition` as a closed account-owner option with exactly
the three SDK modes, allowing `validate_components()` to pass this trusted
server option into Host construction. The focused test covered all three
allowed modes and five rejected values: 8 passed in 0.44 seconds under the
project Python 3.12.10 environment. The child index is empty after commit;
other Execution worktree changes were not staged.

The round adds the R13 integration helper
`tests/unit/test_service_execution_paper_risk_monitor_host_integration.py`
(55,434 bytes, SHA-256
`dd9856f35d3bb15a8a631f2fa5ba016cc13768b29935654d66388193fc61bc77`) and the
AI confirmation HTTP test
`tests/integration/test_afi13_ai_paper_risk_monitor_http.py` (30,437 bytes,
SHA-256 `eb99cd248830b70dcab68c6ed8aee1cdbd3e54ac18a86953238750d52c8860c7`).
The SDK parent update selects Execution
`4374237705478039d60760e5f68df0e6ed076907` and retains Risk
`5434184d35a10418b2fc53e5ec7f805ee5edc884` (which includes
the accepted closable-position readback); all other package pointers remain
unchanged.

## Acceptance evidence

The prior local synthetic R13 Host node passed once:
`tests.unit.test_service_execution_paper_risk_monitor_host_integration::test_real_curve_paper_risk_monitor_buy_and_restart_do_not_duplicate_events`.
It covered a real local CURVE E/R/Monitor Host, BUY and restart without
duplicate Monitor events. Its preserved files are
`D:/temp/afi13-paper-host-author-r13-20261017/stdout.log` (632 bytes,
SHA-256 `085f99c8ac87065cb02a98bb6a8a62c5e3e216fd5288e35787544e2e2969e5fb`)
and `junit.xml` (402 bytes, SHA-256
`58ae12fe3950a82dd597bf384a16f1c9ad4009d41e1f4df19a6df80137a7b29d`).
Result: 1 passed, one existing pytest configuration warning, 10.69 seconds.

The new AI HTTP node passed once with the ai-for-investor project Python
3.12.10, `-p no:asyncio`, and the real application lifespan:
`tests/integration/test_afi13_ai_paper_risk_monitor_http.py::test_ai_explicit_confirmation_reaches_managed_paper_risk_and_monitor_host`.
With a deterministic local intent parser, the request first returned
`PENDING_CONFIRMATION` with no execution result. A separate explicit confirm
then produced a stable intent ID, managed Paper `FILLED` result, and persisted
authenticated order-query readback; Monitor rows matched Execution journal
event IDs, and both spawned Hosts returned normal close acknowledgements and
exit code zero. The fixture uses a synthetic account and local services only.
The corrected fixture sets opening cash to 100 to match its trusted Paper
domain; the earlier R3 cash-mismatch result is preserved and is not a pass.

R4 raw files are under
`D:/temp/afi13-ai-confirm-http-author-20261017-r4-corrected/`: `command.json`
(1,721 bytes, SHA-256
`9a8d992c4583f956ac0dae2d29e022d5439fabd1b92a95b10197c1c9a8584418`),
`stdout.log` (2,008 bytes, SHA-256
`0b8725579067b834d22e415072e33a5ea128bdcef36778c7db3dd2e1eebccfa0`), and
`junit.xml` (415 bytes, SHA-256
`8c7c892ad7b2d7556d62730335f20609a5ad6b9fbe78b0698650afec785d331c`).
Result: 1 passed, five warnings, 68.53 seconds. Warnings were the existing
unknown pytest asyncio option, Backtrader Quandl deprecation, and short
synthetic JWT key warnings. Independent static review returned GO for this
HTTP behavior and its assertions; no QA was repeated for delivery. A separate
static review returned GO for the two-file SDK overlay conditional on the
Execution validator commit above. That review was source-only; R4 was not
repeated against the resulting frozen parent/child source composition.

## Evidence boundary and remaining work

R4 ran in a mixed development checkout. Its command pins the test file and
package search paths but does not record hashes for every imported production
module. Therefore this result is not represented as execution against a
per-file frozen source export. It is local synthetic HTTP/Host evidence, not a
real-account result or formal AFI13 case pass. The earlier R3 WMI/platform
diagnostic was not reproduced in R4; its cause remains unknown.

R4 covers explicit AI confirmation through managed Paper E/R/Monitor and the
existing empty-account cutover in one AFI application process. R13 separately
covers direct SDK E/R/Monitor BUY-to-CLOSE, restart replay, and one-time
outbox delivery; it does not exercise simultaneous requests through two AFI
HTTP workers. AFI07's dual-worker evidence covers the older service-order and
approved-target routes, not this managed Paper adapter. The new focused
manual-order test targets that remaining API concurrency/restart boundary.
AFI12's accepted synthetic non-empty migration is separate from both rounds.
The strategy-start route remains in its own AFI13 acceptance slice. These
results do not establish formal AFI13 completion.

The parent delivery selects only the two SDK Paper files, these two
integration files, this record, and the Execution gitlink. Risk stays at
`5434184d35a10418b2fc53e5ec7f805ee5edc884`; unrelated mixed worktree changes
remain outside the index. The R13 helper's existing final blank line is
preserved byte-for-byte, so `git diff --cached --check` reports that single
known EOF whitespace exception; all other staged paths pass that check.

## New multiworker manual-order candidate (not executed)

`tests/integration/test_afi13_managed_paper_multiworker_http.py` is an
unexecuted candidate, 27,045 bytes / SHA-256
`65a3b90a8d9bab8e531c6b35581093f7bb205ee46c5c171382236ef851383910`. It
proposes to race the same authenticated manual Paper operation key through
two separate normal AFI HTTP worker processes, require one stable managed
fill, reject a changed body, then replay/query through a restarted worker and
verify the persisted Paper book and Execution-to-Monitor event identities.
The synthetic quote and local E/R/Monitor Host helpers come from the R13
fixture; no provider or live account is used. The candidate has not run and
has no HTTP/Host acceptance result. Neither the mixed-source R4 HTTP result
nor R13's direct-SDK result covers this two-AFI-worker API case.

The candidate now reads the production error envelope's `details.code`,
clears `SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON` in both the parent lifespan
and each spawned app process, and places database-engine disposal around all
database setup as well as the Host/test body. These are source corrections,
not test evidence.

The offline preflight used Git archive exports for parent HEAD
`030feb156471c2668eaac254b8c412d26e2e2fc5`, AFI HEAD
`cdd8225059021c6ab33576c455895cafce9452c0`, and the package gitlinks in that
parent (Execution `4374237705478039d60760e5f68df0e6ed076907`, Risk
`5434184d35a10418b2fc53e5ec7f805ee5edc884`, Monitor
`02ee0c42a1e63a9cecbb666376e70c6f72b0d6e8`, Base
`6f314acda4cec224843d63ca5bc07d8cea5bdf46`, Gateway
`a60a1eb4331e0bf733164cf408d120f02487fc73`, and transport-zmq
`ee4cacaee04182fda2d1b9124c2f3a1675aed07b`). It imported the application,
router, service-read, Paper adapters, SDK Host/registry/runtime, package
contracts, Paper worker fixtures, and the candidate test, then stopped while
parsing the trusted profile: the committed AFI deployment parser raises
`PAPER_RISK_MONITOR_UNSUPPORTED` for `execution_risk_monitor` and accepts only
`execution_only`. The candidate therefore remains blocked on the accepted
AFI source closure for the selected E/R/Monitor Paper binding; the preflight
did not start a Host or issue an HTTP request. The initial preflight also
found one omitted committed test helper; that dependency was added to the
export before the second attempt. The Windows Git archive used the checkout's
configured text conversion; the omitted helper was then materialized from its
raw committed blob. This origin/config preflight is not per-file byte-bound
runtime evidence. Raw outputs are preserved at
`.codex-tmp/afi13-manual-fixed-source-preflight-cdd822-20261017/offline-preflight-r1.log`
(1,947 bytes, SHA-256
`515a3e846cca64ffd444f388df40c53f37152b41af5db62c511db0d6fc764a48`) and
`.../offline-preflight-r2.log` (1,238 bytes, SHA-256
`d42553fa0235d2b669f59435a6243ffcd23ff1577c31fe2f882fd9d68651eda8`). This
does not claim per-file production pins from the prior R4 or R13 runs.
