"""Durable cutover-marker gate for MIG03 / iteration plan section 11.3 step 6.

The iteration-10 plan requires that, after a new-epoch owner has produced its
first new business write, running the old deployment/epoch against the same
state is refused until an accepted reverse state conversion exists (formal
plan L865: "首次新派发、预算/归属等状态写入后，必须有已验收的反向状态转换、重新对账
及 writer 交接，才可恢复旧版本；没有该方案则止增并向前修复").  Before that first
write a controlled rollback with the complete old deployment stays permitted
and unchanged.

This module implements the missing machine gate as a small durable JSON
marker beside the state it protects (by default the SDK registry database),
shaped after the execution-journal cutover transaction in
``bt_api_py/_execution_session.py``: an explicit COMMITTED status, atomic
publish, and a destination-exists style conflict refusal.  It reuses the
existing registry ``service_epoch`` facts (``bt_api_py/service_registry.py``)
as the old/new owner identity and invents no parallel fencing system.

Lifecycle:

* ``arm_migration_cutover(state, source_epoch=..., new_epoch=...)`` records a
  COMMITTED cutover marker (the state was taken over by ``new_epoch``).
* ``record_migration_first_write(state, fact_kind=...)`` seals the marker on
  the first new business write (dispatch/order/ownership/reservation/
  idempotency/cursor fact).  Sealing is one-way and durable.
* ``enforce_migration_start(state, deployment_epoch=...)`` is the start gate:
  with no marker nothing changes; a COMMITTED marker keeps the rollback window
  open; a SEALED marker refuses every deployment/epoch except the accepted
  cutover epoch or an explicitly accepted reverse migration, raising
  ``MigrationGateError`` with code ``REVERSE_MIGRATION_REFUSED`` ("no accepted
  reverse migration exists" is the default refusal).
* ``rollback_migration_cutover(state, deployment_epoch=...)`` removes the
  marker before sealing (controlled rollback; afterwards behavior is fully
  unchanged) and refuses after sealing unless
  ``accept_reverse_migration`` recorded an accepted reverse conversion.
* ``accept_reverse_migration(state, accepted_epoch=..., reference=...)``
  records the operator-approved reverse conversion (absent by default).

Every function is benign on a state that was never armed: no marker file, no
behavior change, and the first-write recorder is a silent no-op so it can be
wired into ordinary write paths without migration ever being in effect.
"""

from __future__ import annotations

import errno
import json
import os
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

MIGRATION_MARKER_SUFFIX = ".migration-cutover.json"
MIGRATION_MARKER_SCHEMA_VERSION = 1

STATUS_COMMITTED = "COMMITTED"
STATUS_SEALED = "SEALED"

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

_MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0
_MIGRATION_LOCK_POLL_SECONDS = 0.01


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
        """True once the new owner recorded its first new business write."""

        return self.status == STATUS_SEALED


def marker_path(state_path: str | os.PathLike[str]) -> Path:
    """Return the durable marker location beside one protected state file."""

    return Path(str(state_path) + MIGRATION_MARKER_SUFFIX)


def read_migration_marker(state_path: str | os.PathLike[str]) -> MigrationCutoverMarker | None:
    """Read the marker, or ``None`` when this state was never armed.

    A present-but-unreadable marker fails closed with
    ``CUTOVER_MARKER_UNREADABLE``: a corrupted cutover record must never be
    interpreted as "no migration happened".
    """

    path = marker_path(state_path)
    if not path.exists():
        return None
    with _marker_state_lock(state_path):
        return _read_migration_marker_unlocked(state_path)


def _read_migration_marker_unlocked(
    state_path: str | os.PathLike[str],
) -> MigrationCutoverMarker | None:
    """Read marker bytes while the caller already owns the transition lock."""

    path = marker_path(state_path)
    if not path.exists():
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
    """Seal the cutover marker on the first new business write.

    Returns ``True`` only for the call that performed the COMMITTED -> SEALED
    transition; repeated calls are idempotent and keep the first recorded
    fact.  Without an armed marker this is a benign no-op returning ``False``
    so the recorder can be wired into ordinary write paths.
    """

    if fact_kind not in BUSINESS_WRITE_FACT_KINDS:
        raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
    if recorded_at is not None:
        _require_text(recorded_at)
    # Keep never-armed calls side-effect free; if the marker exists, the
    # authoritative decision is made only after re-reading it under the lock.
    if not marker_path(state_path).exists():
        return False
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None or marker.sealed:
            return False
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


def enforce_migration_start(
    state_path: str | os.PathLike[str],
    *,
    deployment_epoch: str | None = None,
) -> None:
    """Refuse starting an old deployment/epoch against a sealed cutover state.

    * no marker: return (the state was never armed; nothing changes);
    * COMMITTED (pre-first-write): return (controlled rollback window is
      unchanged; any deployment may still return);
    * SEALED: only ``destination_epoch`` or an explicitly accepted reverse
      epoch is allowed; everything else, including an undeclared start,
      raises ``MigrationGateError(REVERSE_MIGRATION_REFUSED)`` because no
      accepted reverse migration exists by default.
    """

    if deployment_epoch is not None:
        _require_epoch(deployment_epoch)
    marker = read_migration_marker(state_path)
    if marker is None or not marker.sealed:
        return
    if deployment_epoch == marker.destination_epoch:
        return
    if (
        marker.accepted_reverse_epoch is not None
        and deployment_epoch == marker.accepted_reverse_epoch
    ):
        return
    raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)


def rollback_migration_cutover(
    state_path: str | os.PathLike[str],
    *,
    deployment_epoch: str | None = None,
) -> bool:
    """Complete a controlled rollback by removing the cutover marker.

    Before the first new write this is always permitted and returns ``True``
    (the old complete deployment restores the state; afterwards no marker
    exists and behavior is fully unchanged).  After sealing it returns
    ``False`` when no active marker exists, and refuses
    ``REVERSE_MIGRATION_REFUSED`` unless ``accept_reverse_migration`` recorded
    an accepted reverse epoch (optionally named by ``deployment_epoch``).
    """

    if deployment_epoch is not None:
        _require_epoch(deployment_epoch)
    if not marker_path(state_path).exists():
        return False
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None:
            return False
        if not marker.sealed:
            _remove_marker(state_path)
            return True
        if marker.accepted_reverse_epoch is None:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        if deployment_epoch is not None and deployment_epoch != marker.accepted_reverse_epoch:
            raise MigrationGateError(REFUSAL_REVERSE_MIGRATION_REFUSED)
        _remove_marker(state_path)
        return True


def accept_reverse_migration(
    state_path: str | os.PathLike[str],
    *,
    accepted_epoch: str,
    reference: str,
) -> MigrationCutoverMarker:
    """Record an accepted reverse state conversion for the sealed state.

    ``reference`` names the accepted reverse conversion evidence.  Recording
    it (an operator decision, absent by default) is what allows the named
    epoch to start again or roll back after sealing.
    """

    _require_epoch(accepted_epoch)
    _require_text(reference)
    if not marker_path(state_path).exists():
        raise MigrationGateError(CODE_NO_ACTIVE_CUTOVER)
    with _marker_state_lock(state_path):
        marker = _read_migration_marker_unlocked(state_path)
        if marker is None:
            raise MigrationGateError(CODE_NO_ACTIVE_CUTOVER)
        if accepted_epoch == marker.destination_epoch:
            raise MigrationGateError(CODE_INVALID_MIGRATION_GATE_ARGUMENT)
        if marker.accepted_reverse_epoch is not None:
            if (
                marker.accepted_reverse_epoch == accepted_epoch
                and marker.accepted_reverse_reference == reference
            ):
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
        if status not in {STATUS_COMMITTED, STATUS_SEALED}:
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
        if (fact_kind is None) != (status == STATUS_COMMITTED):
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
        if os.name != "nt":
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Atomic replace is the visible commit point. Do not restore
                # old bytes after it: a rollback could reopen an older epoch.
                raise MigrationGateError(CODE_MARKER_DURABILITY_UNCERTAIN) from None
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
