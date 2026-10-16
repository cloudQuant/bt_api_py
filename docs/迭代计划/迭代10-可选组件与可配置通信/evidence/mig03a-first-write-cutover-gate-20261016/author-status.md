# MIG03 increment-A status (iteration-10, 2026-10-16) — FINAL

Task: minimal production gate for 11.3 step-6: "after the first new write, refuse running the old version
against the new state" (recon: .codex-tmp/mig03-recon.md, L865/L867, §2 row I, §3.2 steps 1-7, §3.3).
No commit/stage (verified: `git status` shows only ` M bt_api_py/service_host.py` + two untracked new files;
other concurrent changes of other agents left untouched). Evidence: D:\temp\it10mig03a-20261016-r1.
Interpreter: C:/anaconda3/python.exe (py 3.11.5).

## Resume points (checklist)

- [x] R0 recon read; repo state probed (branch dev; other agents' in-flight files noted and avoided)
- [x] R1 status file created (this file)
- [x] R2 module bt_api_py/service_migration_gate.py implemented
- [x] R3 wiring into bt_api_py/service_host.py (optional kwarg + first-action start check; +24 lines, 0 deletions)
- [x] R4 tests/unit/test_service_migration_gate.py (new focused suite, 12 items)
- [x] R5 serialization protocol honored: `ps -W | grep -i python | grep -v grep` before every run
      (files run-NN-precheck.processes.txt); only two pre-existing idle daemons (PID 128016/132224,
      started Oct 13) observed every time; no active other-agent pytest runner appeared; zero waits needed
- [x] R6 new suite x2 on final bytes (run-17/run-18 EXIT=0, 12 passed each; earlier byte revisions also run:
      run-04/05/14/15) + collect-only (run-20, 12 items)
- [x] R7 focused regression on final bytes: host (run-16, 32 passed), registry (run-08, 21 passed),
      spawned host integration (run-09, 1 passed), REG03 durable spawn lifecycle (run-10, 1 passed),
      execution integration (run-11, 1 passed) — all EXIT=0; ruff clean (run-19)
- [x] R8 sha256 of every new/changed file (below); evidence files written incl. 00-INDEX.txt
- [x] R9 design note + exact wiring diff summary below

## DESIGN NOTE (mechanism chosen, why, where wired)

Mechanism: durable journal-cutover-shaped marker + default-refusal start gate, reusing existing registry
service_epoch facts (no parallel fencing system).

1. New module `bt_api_py/service_migration_gate.py` keeps a JSON marker beside the protected state file:
   `<registry_path>.migration-cutover.json` (same state the old deployment would open). Atomic write
   (O_CREAT|O_EXCL tmp + fsync + os.replace, POSIX dir fsync) mirrors `_execution_session.py`
   `_atomic_write_json`; explicit COMMITTED status and a destination-exists style conflict refusal mirror
   the journal cutover transaction (recon §3.3 explicitly suggests this shape).
2. Marker fields: `schema_version`, `migration_id`, `source_epoch` (old deployment's registry
   service_epoch), `destination_epoch` (cutover/new owner epoch), `status` ∈ {COMMITTED, SEALED},
   `first_write` {fact_kind, recorded_at}, `accepted_reverse_migration` {accepted_epoch, reference}
   (null by default).
3. API:
   - `arm_migration_cutover(state_path, source_epoch=..., new_epoch=...)` → COMMITTED. Same epoch pair is
     idempotent and never unseals; different active marker refuses `CUTOVER_MARKER_CONFLICT`.
   - `record_migration_first_write(state_path, fact_kind=...)` → COMMITTED→SEALED one-way, records the
     FIRST business-write fact; benign `False` no-op when no marker (safe to wire into write paths);
     idempotent; first fact preserved; fact_kind ∈ {dispatch, order, ownership, reservation,
     idempotency, cursor} (recon §3.2 step-1 facts, else INVALID_MIGRATION_GATE_ARGUMENT).
   - `enforce_migration_start(state_path, deployment_epoch=...)` → the machine gate: no marker → no-op
     (no behavior change); COMMITTED → no-op (pre-first-write controlled rollback window unchanged for
     any deployment); SEALED → only `destination_epoch` or an explicitly accepted reverse epoch may
     proceed; everything else (old epoch, undeclared None, unknown) raises
     `MigrationGateError(code="REVERSE_MIGRATION_REFUSED")` — "no accepted reverse migration exists" is
     the default refusal.
   - `rollback_migration_cutover(state_path, deployment_epoch=...)` → pre-seal removes the marker
     (complete restore by the old complete deployment; afterwards fully unchanged; returns True);
     post-seal refuses `REVERSE_MIGRATION_REFUSED` unless `accept_reverse_migration` recorded an accepted
     reverse conversion (wrong epoch still refused).
   - `accept_reverse_migration(state_path, accepted_epoch=..., reference=...)` → operator-approved
     reverse conversion record (L865 "已验收的反向状态转换"), absent by default; no marker →
     `NO_ACTIVE_CUTOVER`; re-record conflict → `CUTOVER_MARKER_CONFLICT`.
   - Present-but-corrupt/foreign marker → fail closed `CUTOVER_MARKER_UNREADABLE` (never interpreted as
     "no migration happened").
4. Wiring (minimal, service_host.py only, +24/-0):
   - L44 import `from .service_migration_gate import MigrationGateError, enforce_migration_start`
   - L225 keyword-only ctor kwarg `migration_deployment_epoch: str | None = None`; L243-245 validation
     (`INVALID_DEPLOYMENT_EPOCH`); L256 stored.
   - L346 `start()` first action inside try: `self._enforce_migration_gate()` — BEFORE
     `_validate_authority_references` and before any resource/registry step (critical: the registry open
     itself writes a new service_epoch, drops sessions, and releases write claims, so a sealed state must
     refuse before it).
   - L782-792 `_enforce_migration_gate` maps `MigrationGateError.code` → `ServiceHostError(code)`, so the
     refusal is machine-checkable as `ServiceHostError.code == "REVERSE_MIGRATION_REFUSED"`.
   - Existing callers pass nothing and never arm markers → pure passthrough (regressions below prove it).
   Exact diff: D:\temp\it10mig03a-20261016-r1\service_host.wiring.diff.txt (24 insertions, 0 deletions).

Why this and not more: increment-A scope is the gate itself; the two-epoch drill (drain → fence →
import per-ID conservation → new epoch → re-register → refusal) is incr-B and will drive the gate via
`migration_deployment_epoch` and `record_migration_first_write` at its first new dispatch/ownership fact.

## Open questions (kept from planning; not guessed)

1. Real-old-binary enforcement boundary: an old release that does not consult the gate cannot be refused
   by cooperating code; the new start path refuses based on the deployment's declared epoch identity. The
   local drill (incr-B) therefore simulates the old deployment with
   `ServiceHost(..., migration_deployment_epoch=<old service_epoch>)`. Whether 11.3 step-6 additionally
   wants a state-format incompatibility (so a genuinely old binary cannot even open the new registry) is
   not decidable from recon §2 row I + §3.3 alone → acceptance-scope question for the drill node.
2. New-owner restart after seal must present `destination_epoch` (readable from the marker). Whether an
   accepted "forward re-adoption" successor-epoch record is needed is not stated in the recon; not
   implemented (kept minimal; rollback after seal already requires the accepted reverse record).

## RUN LOG (all evidence in D:\temp\it10mig03a-20261016-r1; see 00-INDEX.txt)

- run-01 compile/import smoke: EXIT=0 ("COMPILE-OK 1 REVERSE_MIGRATION_REFUSED").
- run-02 new tests attempt with --timeout=300: EXIT=4 (pytest-timeout not installed; kept).
- run-03 new tests plain: EXIT=2 (collection error: pytest-asyncio 0.23 + `--import-mode=importlib`
  conflict `'Package' object has no attribute 'obj'`; kept). Fix convention (used for every later run):
  `-p no:asyncio -p no:cacheprovider`.
- run-04/run-05 new suite (pre-lint-fix bytes): EXIT=0, 12 passed each, 2.36s/2.29s.
- run-06 collect-only (pre-v2 bytes): EXIT=0, 12 items.
- run-07..run-11 regressions (pre-final gate-module bytes; results reconfirmed on final bytes where the
  module is imported):
  - run-07 test_service_host.py: EXIT=0, 32 passed.
  - run-08 test_service_registry.py: EXIT=0, 21 passed.
  - run-09 test_service_host_integration.py: EXIT=0, 1 passed (spawned host over synthetic SQLite).
  - run-10 test_service_registration_durable_business_lifecycle.py (REG03 spawn drill): EXIT=0, 1 passed.
  - run-11 test_service_execution_integration.py: EXIT=0, 1 passed.
- run-12 ruff (pre-fix): EXIT=1 (SIM105 in new module, SIM300 in new test) → fixed.
- run-13 ruff after fixes: EXIT=0.
- run-14/run-15 new suite (final bytes): EXIT=0, 12 passed each, 2.42s/2.29s.
- run-16 test_service_host.py (final bytes): EXIT=0, 32 passed, 3.15s.
- run-17/run-18 new suite (final bytes incl. sealed re-arm assertion): EXIT=0, 12 passed each.
- run-19 ruff final: EXIT=0 ("All checks passed!").
- run-20 collect-only final: EXIT=0, 12 items.
- Waits: none (no competing python test runner observed in any precheck; precheck files run-NN-precheck.processes.txt).

## SHA256 (final bytes)

- 70700cd648da85d9dcca4ff0e55022cd44f2f5e12090a0734a852b5f3f0cc39d  bt_api_py/service_migration_gate.py (16721 B)
- 07e95f92b5782dd18412b0397ab1c50e3ecf8482c720392314d93f4cbf9d0be1  bt_api_py/service_host.py (66489 B)
- afaca97a071d5f7069203d3a2f4aaa907c2d71607bd440d76b2cf3b58eb263cb  tests/unit/test_service_migration_gate.py (17406 B)
- (status file itself is excluded from the deliverable hashes; also see sha256.txt in the evidence dir)

## Housekeeping notes

- Nothing committed/staged. Other agents' dirty files (bt_api_risk gitlink, load01 test, market-integration
  test, dim02 files, service_market_legs.py) were not touched.
- Frozen/in-flight files per mission were not edited: service_risk_order_entry.py, service_risk_order_journal.py,
  bt_api_risk/**, service_execution_group*.py, service_group_reservations.py, group_order_contracts/
  group_coordinator/group_store, dim02/market_legs/reg04 test files.
- New tests prove: (1) no marker → no behavior change (module + ServiceHost passthrough to the pre-existing
  UNSUPPORTED_PUBLIC_PORT failure); (2) pre-first-write old-epoch start allowed + rollback removes marker,
  state unchanged; (3) post-first-write old-epoch/undeclared/unknown start refused with
  REVERSE_MIGRATION_REFUSED incl. via ServiceHostError, refusal precedes all side effects, state bytes and
  sealed marker untouched; (4) refusal durable across a fresh interpreter (subprocess probe) and rollback
  refusal durable too; (5) accepted reverse migration is the only way back after seal; (6) corrupt marker
  fails closed.
