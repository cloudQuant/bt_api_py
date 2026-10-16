"""Durable cutover-marker gate for MIG03 / iteration plan section 11.3 step 6.

The iteration-10 plan requires that, after a new-epoch owner has produced its
first new business write, running the old deployment/epoch against the same
state is refused until an accepted reverse state conversion exists (formal
plan L865: "首次新派发、预算/归属等状态写入后，必须有已验收的反向状态转换、重新对账
及 writer 交接，才可恢复旧版本；没有该方案则止增并向前修复"). Production
currently seals conservatively at the authorized handler-entry/write-attempt
boundary, before exact downstream commit is known. Before the barrier a
controlled rollback with the complete old deployment stays permitted.

This module implements the machine gate as a small durable JSON marker beside
the state it protects (by default the SDK registry database). A persistent
sidecar lock serializes startup initialization and marker transitions, even
before the first marker exists. Registry ``service_epoch`` values remain
per-process lease facts. Production write attempts are bound to the explicit
stable deployment epoch supplied by the Host, never to a random process
epoch.

Lifecycle:

* ``arm_migration_cutover(state, source_epoch=..., new_epoch=...)`` records a
  COMMITTED cutover marker (the state was taken over by ``new_epoch``).
* ``enable_migration_cutover`` durably enables the destination only after the
  operator has checked the new read projection, policies, and readiness.
* ``record_migration_write_attempt(state, deployment_epoch=...,
  fact_kind=...)`` seals the marker immediately before an authorized
  production write handler or internal writer is entered. This is a
  conservative write-attempt barrier; it does not prove that downstream
  storage committed. Sealing is one-way and durable.
* ``record_migration_first_write`` remains as a low-level compatibility
  primitive. Production write boundaries use the epoch-bound write-attempt
  API.
* ``migration_start_guard`` is held through Host resource and Registry
  initialization. With no marker it preserves normal startup. COMMITTED
  admits only the declared source or destination in read-only mode; ENABLED
  and SEALED admit only the destination; REVERSED admits only the separately
  accepted reverse epoch.
* ``rollback_migration_cutover(state, deployment_epoch=...)`` removes only an
  unenabled COMMITTED marker for its exact source epoch. After ENABLED it
  never removes the marker. A separately accepted reverse conversion moves
  SEALED to durable REVERSED instead of reopening the unarmed fast path.
* ``accept_reverse_migration(state, accepted_epoch=..., reference=...)``
  records a reverse conversion approval after the trusted operator has
  verified the conversion. The marker reference is an identifier, not proof
  by itself. A new stable epoch distinct from both earlier epochs is required.

The write-attempt API remains a benign, lock-free no-op on a state that was
never armed. That path alone does not fence an already-running writer from a
first arm; the offline boundary protocol must stop and verify all existing
writers before arming. Startup itself always takes the persistent sidecar
lock, including when the marker is absent, so initial arming cannot race
through an in-progress Host/Registry initialization.
"""

from __future__ import annotations

import errno
import json
import os
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

MIGRATION_MARKER_SUFFIX = ".migration-cutover.json"
MIGRATION_MARKER_SCHEMA_VERSION = 1

STATUS_COMMITTED = "COMMITTED"
STATUS_ENABLED = "ENABLED"
STATUS_SEALED = "SEALED"
STATUS_REVERSED = "REVERSED"

BUSINESS_WRITE_FACT_KINDS = frozenset(
    {"dispatch", "order", "ownership", "reservation", "idempotency", "cursor"}
)

REFUSAL_REVERSE_MIGRATION_REFUSED = "REVERSE_MIGRATION_REFUSED"
CODE_CUTOVER_MARKER_CONFLICT = "CUTOVER_MARKER_CONFLICT"
CODE_CUTOVER_MARKER_UNREADABLE = "CUTOVER_MARKER_UNREADABLE"
CODE_NO_ACTIVE_CUTOVER = "NO_ACTIVE_CUTOVER"
CODE_INVALID_MIGRATION_GATE_ARGUMENT = "INVALID_MIGRATION_GATE_ARGUMENT"
CODE_MIGRATION_GATE_LOCK_TIMEOUT = "MIGRATION_GATE_LOCK_TIMEOUT"
CODE_MIGRATION_GATE_LOCK_FAILURE = "MIGRATION_GATE_LOCK_FAILURE"
CODE_MARKER_DURABILITY_UNCERTAIN = "CUTOVER_MARKER_DURABILITY_UNCERTAIN"
CODE_MIGRATION_GATE_EPOCH_MISMATCH = "MIGRATION_GATE_EPOCH_MISMATCH"
CODE_MIGRATION_WRITE_ENABLEMENT_INVALID = "MIGRATION_WRITE_ENABLEMENT_INVALID"

_MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0
_MIGRATION_LOCK_POLL_SECONDS = 0.01
_START_GUARD_CAPABILITY = object()


class MigrationGateError(RuntimeError):
    """Finite, value-free marker gate error carrying a machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MigrationCutoverMarker:
    """Read model of one durable cutover marker document."""

    migration_id: str
    source_epoch: str
    destination_epoch: str
    status: str
    first_write_fact_kind: str | None = None
    first_write_recorded_at: str | None = None
    accepted_reverse_epoch: str | None = None
    accepted_reverse_reference: str | None = None

    @property
    def sealed(self) -> bool:
        """True once the authorized write-attempt barrier has been crossed."""

        return self.status in {STATUS_SEALED, STATUS_REVERSED}


@dataclass(frozen=True, slots=True)
class MigrationStartSnapshot:
    """Active proof token for one guarded Host/Registry startup interval."""

    state_path: str
    deployment_epoch: str | None
    marker: MigrationCutoverMarker | None
    _capability: object = field(repr=False)
    _active_state: list[bool] = field(repr=False)

    def require_active(
        self,
        state_path: str | os.PathLike[str],
        deployment_epoch: str | None,
    ) -> MigrationCutoverMarker | None:
        """Reject detached or foreign tokens before Registry initialization."""

        if (
            type(self) is not MigrationStartSnapshot
            or self._capability is not _START_GUARD_CAPABILITY
            or self._active_state != [True]
            or self.state_path != _normalized_state_path(state_path)
            or self.deployment_epoch != deployment_epoch
        ):
            raise MigrationGateError(CODE_MIGRATION_WRITE_ENABLEMENT_INVALID)
        return self.marker


def marker_path(state_path: str | os.PathLike[str]) -> Path:
    """Return the durable marker location beside one protected state file."""

    return Path(str(state_path) + MIGRATION_MARKER_SUFFIX)


def _normalized_state_path(state_path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(state_path)))


def read_migration_marker(state_path: str | os.PathLike[str]) -> MigrationCutoverMarker | None:
    """Read the marker, or ``None`` when this state was never armed.

    A present-but-unreadable marker fails closed with
    ``CUTOVER_MARKER_UNREADABLE``: a corrupted cutover record must never be
    interpreted as "no migration happened".
    """

    path = marker_path(state_path)
    if not _marker_exists(path):
        return None
    with _marker_state_lock(state_path):
        return _read_migration_marker_unlocked(state_path)


def _read_migration_marker_unlocked(
    state_path: str | os.PathLike[str],
) -> MigrationCutoverMarker | None:
    """Read marker bytes while the caller already owns the transition lock."""

    path = marker_path(state_path)
    if not _marker_exists(path):
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # A concurrent, successful rollback may remove the marker between the
        # existence check and open. That is an atomic state transition, not a
        # corrupt marker.
        return None
    except Exception:
        raise MigrationGateError(CODE_CUTOVER_MARKER_UNREADABLE) from None
    return _marker_from_document(document)


def _marker_exists(path: Path) -> bool:
    """Distinguish a genuinely missing marker from an inaccessible one.

    ``Path.exists`` may suppress operating-system errors on supported Python
    versions, which would turn an unreadable armed marker into the unarmed
    fast path. Only ``FileNotFoundError`` represents absence here; all other
    stat failures fail closed with the existing finite unreadable code.
    """

    try:
        path.stat()
    except FileNotFoundError:
        return False
    except OSError:
        raise MigrationGateError(CODE_CUTOVER_MARKER_UNREADABLE) from None
    return True


def arm_migration_cutover(
    state_path: str | os.PathLike[str],
    *,
    source_epoch: str,
    new_epoch: str,
    migration_id: str | None = None,
) -> MigrationCutoverMarker:
    """Record the cutover of ``state_path`` from ``source_epoch`` to ``new_epoch``.

    Re-arming the same epoch pair is an idempotent read (a sealed marker is
    never reset).  A different active marker refuses
    ``CUTOVER_MARKER_CONFLICT``, mirroring the journal cutover's
    destination-exists semantics.
    """

    _require_epoch(source_epoch)
    _require_epoch(new_epoch)
    if source_epoch == new_epoch:
        raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
    if migration_id is not None:
        _require_text(migration_id)
    with _marker_state_lock(state_path):
        existing = _read_migration_marker_unlocked(state_path)
        if existing is not None:
            if (
                existing.source_epoch == source_epoch
                and existing.destination_epoch == new_epoch
                and (migration_id is None or existing.migration_id == migration_id)
            ):
                _fsync_parent_directory(marker_path(state_path))
                return existing
            raise MigrationGateError(CODE_CUTOVER_MARKER_CONFLICT)
        marker = MigrationCutoverMarker(
            migration_id=migration_id or uuid.uuid4().hex,
            source_epoch=source_epoch,
            destination_epoch=new_epoch,
            status=STATUS_COMMITTED,
        )
        _write_marker(state_path, marker)
        return marker


def record_migration_first_write(
    state_path: str | os.PathLike[str],
    *,
    fact_kind: str,
    recorded_at: str | None = None,
) -> bool:
    """Seal an explicitly enabled marker through the legacy API.

    Returns ``True`` only for the call that performed ENABLED -> SEALED;
    repeated calls are idempotent and keep the first recorded fact. A
    COMMITTED marker is deliberately rejected: otherwise this compatibility
    API could skip the destination's durable read-ready/explicit-enable
    barrier and a restarted Host would treat SEALED as writable. Without an
    armed marker this remains a benign no-op returning ``False``. Production
    boundaries use the epoch-bound :func:`record_migration_write_attempt`.
    """

    if fact_kind not in BUSINESS_WRITE_FACT_KINDS:
        raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
    if recorded_at is not None:
        _require_text(recorded_at)
    # Keep never-armed calls side-effect free; if the marker exists, the
    # authoritative decision is made only after re-reading it under the lock.
    if not _marker_exists(marker_path(state_path)):
        return False
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None or marker.sealed:
            return False
        if marker.status != STATUS_ENABLED:
            raise MigrationGateError(CODE_MIGRATION_WRITE_ENABLEMENT_INVALID)
        sealed = MigrationCutoverMarker(
            migration_id=marker.migration_id,
            source_epoch=marker.source_epoch,
            destination_epoch=marker.destination_epoch,
            status=STATUS_SEALED,
            first_write_fact_kind=fact_kind,
            first_write_recorded_at=recorded_at or _utc_now_text(),
            accepted_reverse_epoch=marker.accepted_reverse_epoch,
            accepted_reverse_reference=marker.accepted_reverse_reference,
        )
        _write_marker(state_path, sealed)
        return True


def record_migration_write_attempt(
    state_path: str | os.PathLike[str],
    *,
    deployment_epoch: str | None,
    fact_kind: str,
    recorded_at: str | None = None,
) -> bool:
    """Seal immediately before entering a production writer, epoch-bound.

    The marker-absent path remains a side-effect-free no-op for deployments
    that have never armed MIG03. Once a marker exists, its state is reread
    while holding the transition lock and the caller must name either the
    declared destination deployment or an explicitly accepted reverse
    deployment. A process-local registry epoch is not a substitute.

    ``True`` means this call published ENABLED -> SEALED before the writer
    was entered. It records an attempted write boundary only; downstream
    handler or journal commit requires separate service evidence.

    A later attempt against an already SEALED marker reconfirms parent-
    directory durability before allowing the caller to proceed. This also
    covers a prior replace that became visible before its directory fsync
    reported failure.
    """

    if fact_kind not in BUSINESS_WRITE_FACT_KINDS:
        raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
    if recorded_at is not None:
        _require_text(recorded_at)
    # Preserve the ordinary unarmed fast path. It is not a substitute for the
    # offline quiescence proof required before a first arm.
    if not _marker_exists(marker_path(state_path)):
        return False
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None:
            return False
        expected_epoch = (
            marker.accepted_reverse_epoch
            if marker.status == STATUS_REVERSED
            else marker.destination_epoch
        )
        if (
            type(deployment_epoch) is not str
            or not deployment_epoch
            or deployment_epoch != expected_epoch
        ):
            raise MigrationGateError(CODE_MIGRATION_GATE_EPOCH_MISMATCH)
        if marker.status in {STATUS_SEALED, STATUS_REVERSED}:
            # A prior replace may have made SEALED visible but failed the
            # parent-directory fsync. Reconfirm durability on every later
            # writer attempt before using the existing one-way seal to admit
            # the handler.
            _fsync_parent_directory(marker_path(state_path))
            return False
        if marker.status != STATUS_ENABLED:
            raise MigrationGateError(CODE_MIGRATION_WRITE_ENABLEMENT_INVALID)
        sealed = MigrationCutoverMarker(
            migration_id=marker.migration_id,
            source_epoch=marker.source_epoch,
            destination_epoch=marker.destination_epoch,
            status=STATUS_SEALED,
            first_write_fact_kind=fact_kind,
            first_write_recorded_at=recorded_at or _utc_now_text(),
            accepted_reverse_epoch=marker.accepted_reverse_epoch,
            accepted_reverse_reference=marker.accepted_reverse_reference,
        )
        _write_marker(state_path, sealed)
        return True


def enable_migration_cutover(
    state_path: str | os.PathLike[str],
    *,
    migration_id: str,
    deployment_epoch: str,
) -> MigrationCutoverMarker:
    """Durably enable the declared destination under the marker transition lock.

    The durable ENABLED phase closes the pre-write rollback window before a
    Registry opens its process-local write latch. It is intentionally a
    one-way phase: a later first write seals it, while rollback cannot remove
    an ENABLED marker.
    """

    _require_text(migration_id)
    _require_epoch(deployment_epoch)
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if (
            marker is None
            or marker.migration_id != migration_id
            or marker.destination_epoch != deployment_epoch
        ):
            raise MigrationGateError(CODE_MIGRATION_WRITE_ENABLEMENT_INVALID)
        if marker.status == STATUS_ENABLED:
            # A prior replace may have made ENABLED visible before directory
            # fsync failed. Never trust that phase until durability is
            # confirmed again.
            _fsync_parent_directory(marker_path(state_path))
            return marker
        if marker.status != STATUS_COMMITTED:
            raise MigrationGateError(CODE_MIGRATION_WRITE_ENABLEMENT_INVALID)
        enabled = MigrationCutoverMarker(
            migration_id=marker.migration_id,
            source_epoch=marker.source_epoch,
            destination_epoch=marker.destination_epoch,
            status=STATUS_ENABLED,
            accepted_reverse_epoch=marker.accepted_reverse_epoch,
            accepted_reverse_reference=marker.accepted_reverse_reference,
        )
        _write_marker(state_path, enabled)
        return enabled


def enforce_migration_start(
    state_path: str | os.PathLike[str],
    *,
    deployment_epoch: str | None = None,
) -> None:
    """Refuse starting an old deployment/epoch against a sealed cutover state.

    * no marker: return (the state was never armed; nothing changes);
    * COMMITTED: only the declared source or destination may start, read-only;
    * ENABLED or SEALED: only the declared destination epoch may start;
    * REVERSED: only the newly accepted reverse epoch may start.
    """

    if deployment_epoch is not None:
        _require_epoch(deployment_epoch)
    with migration_start_guard(state_path, deployment_epoch=deployment_epoch):
        return


@contextmanager
def migration_start_guard(
    state_path: str | os.PathLike[str], *, deployment_epoch: str | None
) -> Iterator[MigrationStartSnapshot]:
    """Serialize Host/Registry initialization against arm, enable and rollback.

    The persistent sidecar is acquired even when no marker exists. This is
    necessary for a first arm to wait until an already-starting old Host has
    initialized its Registry epoch. It does not claim to stop an already-live
    owner; the migration procedure must still prove real owner quiescence.
    """

    if deployment_epoch is not None:
        _require_epoch(deployment_epoch)
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        _validate_start_epoch(marker, deployment_epoch)
        if marker is not None:
            # Never allow a visible marker whose parent-directory durability
            # is uncertain to authorize process startup.
            _fsync_parent_directory(marker_path(state_path))
        active_state = [True]
        token = MigrationStartSnapshot(
            state_path=_normalized_state_path(state_path),
            deployment_epoch=deployment_epoch,
            marker=marker,
            _capability=_START_GUARD_CAPABILITY,
            _active_state=active_state,
        )
        try:
            yield token
        finally:
            active_state[0] = False


def _validate_start_epoch(
    marker: MigrationCutoverMarker | None, deployment_epoch: str | None
) -> None:
    if marker is None:
        return
    if marker.status == STATUS_COMMITTED:
        if deployment_epoch in {marker.source_epoch, marker.destination_epoch}:
            return
        raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
    if marker.status == STATUS_ENABLED:
        if deployment_epoch == marker.destination_epoch:
            return
        raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
    if marker.status == STATUS_SEALED and deployment_epoch == marker.destination_epoch:
        return
    if (
        marker.status == STATUS_REVERSED
        and marker.accepted_reverse_epoch is not None
        and deployment_epoch == marker.accepted_reverse_epoch
    ):
        return
    raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)


def rollback_migration_cutover(
    state_path: str | os.PathLike[str],
    *,
    deployment_epoch: str | None = None,
) -> bool:
    """Rollback before enable, or durably switch to a verified reverse epoch.

    Only the exact source epoch may remove a COMMITTED pre-enable marker.
    ENABLED is irreversible through this API. SEALED may transition to
    REVERSED only after a trusted caller records a verified reverse conversion
    and supplies its new stable epoch; the marker remains durable. Repeating
    that exact REVERSED request is an idempotent no-op returning ``False``.
    """

    if deployment_epoch is not None:
        _require_epoch(deployment_epoch)
    if not _marker_exists(marker_path(state_path)):
        return False
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None:
            return False
        if marker.status == STATUS_COMMITTED:
            if deployment_epoch != marker.source_epoch:
                raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
            _remove_marker(state_path)
            return True
        if marker.status == STATUS_ENABLED:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        if marker.status == STATUS_REVERSED:
            if deployment_epoch != marker.accepted_reverse_epoch:
                raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
            _fsync_parent_directory(marker_path(state_path))
            return False
        if (
            marker.status != STATUS_SEALED
            or marker.accepted_reverse_epoch is None
            or marker.accepted_reverse_epoch in {marker.source_epoch, marker.destination_epoch}
        ):
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        if deployment_epoch != marker.accepted_reverse_epoch:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        reversed_marker = MigrationCutoverMarker(
            migration_id=marker.migration_id,
            source_epoch=marker.source_epoch,
            destination_epoch=marker.destination_epoch,
            status=STATUS_REVERSED,
            first_write_fact_kind=marker.first_write_fact_kind,
            first_write_recorded_at=marker.first_write_recorded_at,
            accepted_reverse_epoch=marker.accepted_reverse_epoch,
            accepted_reverse_reference=marker.accepted_reverse_reference,
        )
        _write_marker(state_path, reversed_marker)
        return True


def accept_reverse_migration(
    state_path: str | os.PathLike[str],
    *,
    accepted_epoch: str,
    reference: str,
) -> MigrationCutoverMarker:
    """Record a trusted operator's verified reverse conversion on SEALED.

    The caller must complete and verify reverse state conversion before this
    call. ``reference`` is an audit identifier only; the API cannot establish
    that the referenced work actually occurred. The accepted epoch must be a
    new stable epoch, distinct from both source and destination.
    """

    _require_epoch(accepted_epoch)
    _require_text(reference)
    if not _marker_exists(marker_path(state_path)):
        raise MigrationGateError(CODE_NO_ACTIVE_CUTOVER)
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None:
            raise MigrationGateError(CODE_NO_ACTIVE_CUTOVER)
        if not marker.sealed:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        if accepted_epoch == marker.destination_epoch:
            raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
        if accepted_epoch in {marker.source_epoch, marker.destination_epoch}:
            raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
        if marker.status == STATUS_REVERSED:
            if (
                marker.accepted_reverse_epoch == accepted_epoch
                and marker.accepted_reverse_reference == reference
            ):
                _fsync_parent_directory(marker_path(state_path))
                return marker
            raise MigrationGateError(CODE_CUTOVER_MARKER_CONFLICT)
        if marker.status != STATUS_SEALED:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        if marker.accepted_reverse_epoch is not None:
            if (
                marker.accepted_reverse_epoch == accepted_epoch
                and marker.accepted_reverse_reference == reference
            ):
                _fsync_parent_directory(marker_path(state_path))
                return marker
            raise MigrationGateError(CODE_CUTOVER_MARKER_CONFLICT)
        updated = MigrationCutoverMarker(
            migration_id=marker.migration_id,
            source_epoch=marker.source_epoch,
            destination_epoch=marker.destination_epoch,
            status=marker.status,
            first_write_fact_kind=marker.first_write_fact_kind,
            first_write_recorded_at=marker.first_write_recorded_at,
            accepted_reverse_epoch=accepted_epoch,
            accepted_reverse_reference=reference,
        )
        _write_marker(state_path, updated)
        return updated


def _utc_now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_text(value: Any) -> None:
    if type(value) is not str or not value:
        raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)


def _require_epoch(value: Any) -> None:
    _require_text(value)


def _marker_document(marker: MigrationCutoverMarker) -> dict[str, Any]:
    first_write = (
        None
        if marker.first_write_fact_kind is None
        else {
            "fact_kind": marker.first_write_fact_kind,
            "recorded_at": marker.first_write_recorded_at,
        }
    )
    accepted_reverse = (
        None
        if marker.accepted_reverse_epoch is None
        else {
            "accepted_epoch": marker.accepted_reverse_epoch,
            "reference": marker.accepted_reverse_reference,
        }
    )
    return {
        "schema_version": MIGRATION_MARKER_SCHEMA_VERSION,
        "migration_id": marker.migration_id,
        "source_epoch": marker.source_epoch,
        "destination_epoch": marker.destination_epoch,
        "status": marker.status,
        "first_write": first_write,
        "accepted_reverse_migration": accepted_reverse,
    }


def _marker_from_document(document: Any) -> MigrationCutoverMarker:
    try:
        if (
            type(document) is not dict
            or type(document.get("schema_version")) is not int
            or document.get("schema_version") != MIGRATION_MARKER_SCHEMA_VERSION
        ):
            raise ValueError
        status = document["status"]
        if status not in {STATUS_COMMITTED, STATUS_ENABLED, STATUS_SEALED, STATUS_REVERSED}:
            raise ValueError
        migration_id = document["migration_id"]
        source_epoch = document["source_epoch"]
        destination_epoch = document["destination_epoch"]
        for value in (migration_id, source_epoch, destination_epoch):
            if type(value) is not str or not value:
                raise ValueError
        if source_epoch == destination_epoch:
            raise ValueError
        first_write = document.get("first_write")
        if first_write is None:
            fact_kind = None
            recorded_at = None
        elif type(first_write) is dict:
            fact_kind = first_write["fact_kind"]
            recorded_at = first_write["recorded_at"]
            if fact_kind not in BUSINESS_WRITE_FACT_KINDS:
                raise ValueError
            if type(recorded_at) is not str or not recorded_at:
                raise ValueError
        else:
            raise ValueError
        if (fact_kind is None) != (status in {STATUS_COMMITTED, STATUS_ENABLED}):
            raise ValueError
        accepted_reverse = document.get("accepted_reverse_migration")
        if accepted_reverse is None:
            accepted_epoch = None
            reference = None
        elif type(accepted_reverse) is dict:
            accepted_epoch = accepted_reverse["accepted_epoch"]
            reference = accepted_reverse["reference"]
            if type(accepted_epoch) is not str or not accepted_epoch:
                raise ValueError
            if accepted_epoch == destination_epoch:
                raise ValueError
            if type(reference) is not str or not reference:
                raise ValueError
        else:
            raise ValueError
        if status in {STATUS_COMMITTED, STATUS_ENABLED} and accepted_epoch is not None:
            raise ValueError
        if status == STATUS_REVERSED and (
            accepted_epoch is None
            or accepted_epoch in {source_epoch, destination_epoch}
        ):
            raise ValueError
    except Exception:
        raise MigrationGateError(CODE_CUTOVER_MARKER_UNREADABLE) from None
    return MigrationCutoverMarker(
        migration_id=migration_id,
        source_epoch=source_epoch,
        destination_epoch=destination_epoch,
        status=status,
        first_write_fact_kind=fact_kind,
        first_write_recorded_at=recorded_at,
        accepted_reverse_epoch=accepted_epoch,
        accepted_reverse_reference=reference,
    )


def _write_marker(state_path: str | os.PathLike[str], marker: MigrationCutoverMarker) -> None:
    _atomic_write_json(marker_path(state_path), _marker_document(marker))


@contextmanager
def _marker_state_lock(state_path: str | os.PathLike[str]) -> Iterator[None]:
    """Serialize migration-marker transitions across processes.

    The sidecar is intentionally persistent. Removing a locked sidecar could
    let another process create and lock a different inode while a waiter still
    owns the original one.
    """

    marker = marker_path(state_path)
    lock_path = marker.with_name(marker.name + ".lock")
    owner_file = None
    acquired = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        owner_file = open(lock_path, "a+b")  # noqa: SIM115 -- held through the transition.
        if os.name == "nt":
            owner_file.seek(0, os.SEEK_END)
            if owner_file.tell() == 0:
                owner_file.write(b"\0")
                owner_file.flush()
        owner_file.seek(0)
        deadline = time.monotonic() + _MIGRATION_LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(owner_file.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise MigrationGateError(CODE_MIGRATION_GATE_LOCK_FAILURE) from None
                    if time.monotonic() >= deadline:
                        raise MigrationGateError(CODE_MIGRATION_GATE_LOCK_TIMEOUT) from None
                    time.sleep(_MIGRATION_LOCK_POLL_SECONDS)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MigrationGateError(CODE_MIGRATION_GATE_LOCK_TIMEOUT) from None
                    time.sleep(_MIGRATION_LOCK_POLL_SECONDS)
    except MigrationGateError:
        if owner_file is not None:
            with suppress(Exception):
                owner_file.close()
        raise
    except Exception:
        if owner_file is not None:
            with suppress(Exception):
                owner_file.close()
        raise MigrationGateError(CODE_MIGRATION_GATE_LOCK_FAILURE) from None
    body_failed = False
    release_failed = False
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        if owner_file is not None:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        owner_file.seek(0)
                        msvcrt.locking(owner_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(owner_file.fileno(), fcntl.LOCK_UN)
                except Exception:
                    release_failed = True
            try:
                owner_file.close()
            except Exception:
                release_failed = True
        if release_failed and not body_failed:
            raise MigrationGateError(CODE_MIGRATION_GATE_LOCK_FAILURE) from None


def _remove_marker(state_path: str | os.PathLike[str]) -> None:
    path = marker_path(state_path)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    if os.name != "nt":
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # The unlink already happened; reopening the old marker would
            # reverse an accepted state transition. Report the uncertain
            # directory durability without recreating the file.
            raise MigrationGateError(CODE_MARKER_DURABILITY_UNCERTAIN) from None


def _atomic_write_json(path: Path, value: Any) -> None:
    """Same durability shape as the execution-journal cutover transaction."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent_directory(path)
    except BaseException:
        with suppress(FileNotFoundError):
            try:
                temporary.unlink()
            except OSError:
                # Preserve the primary write/replace failure; an orphaned
                # unique temp file is safer than replacing the original error.
                pass
        raise
    else:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _fsync_parent_directory(path: Path) -> None:
    """Confirm a marker directory entry using the writer's durability rule."""

    if os.name == "nt":
        return
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # Atomic replace is the visible commit point. Do not restore old bytes
        # after it: a rollback could reopen an older epoch.
        raise MigrationGateError(CODE_MARKER_DURABILITY_UNCERTAIN) from None
