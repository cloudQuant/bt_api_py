"""Fail-closed operator control for managed unknown-dispatch reconciliation.

The execution facade deliberately owns only an immutable local order journal.
It can record provider evidence, but it has no operator identity, authorization,
or monitor-delivery authority.  This module is the small composition-layer
control port that joins those independent concerns without adding an execution
package import edge to the risk or monitor packages.

It accepts only an already composed managed runtime, a typed provider
observation, and a code-owned authorization callback.  Reconciliation evidence,
authorization decisions, and release attempts are durably audited locally.  A
per-intent dispatch freeze remains active whenever evidence, audit, monitor
outbox, authorization, or release confirmation is unavailable.

The port never opens a socket or obtains provider evidence itself.  The caller
must supply evidence obtained through an independently controlled reconciliation
adapter.  Calling ``ManagedExecutionFacade.reconcile`` directly remains a
ledger-only operation and never clears a dispatch freeze.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .catalog import RuntimePluginError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ControlAuditError(RuntimeError):
    """Base error for the local reconciliation-control audit ledger."""


class ControlAuditConflictError(ControlAuditError):
    """An immutable evidence or control-command id was reused differently."""


class ControlCommandStatus(StrEnum):
    """Durable lifecycle of one intent-freeze release command."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    RELEASED = "released"
    EXPIRED = "expired"


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid " + field_name)
    return value


def _reason_code(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _REASON_CODE.fullmatch(value):
        raise ValueError("invalid " + field_name)
    return value


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("invalid " + field_name)
    return value


def _timestamp(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid " + field_name)
    return float(value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decimal_text(value: object, field_name: str) -> str:
    try:
        result = format(value, "f")
    except (TypeError, ValueError) as error:
        raise ValueError("invalid " + field_name) from error
    return result


def _observation_projection(observation: object) -> dict[str, object]:
    """Create the only redacted observation form that enters the audit ledger."""

    try:
        state = observation.state.value  # type: ignore[attr-defined]
        intent_id = observation.intent_id  # type: ignore[attr-defined]
        provider_order_id = observation.provider_order_id  # type: ignore[attr-defined]
        filled_quantity = observation.filled_quantity  # type: ignore[attr-defined]
        average_price = observation.average_price  # type: ignore[attr-defined]
        reason_code = observation.reason_code  # type: ignore[attr-defined]
    except AttributeError as error:
        raise ValueError("invalid typed provider observation") from error
    return {
        "intent_id": _identifier(intent_id, "observation intent_id"),
        "state": _identifier(state, "observation state"),
        "provider_order_id": (
            None
            if provider_order_id is None
            else _identifier(provider_order_id, "provider_order_id")
        ),
        "filled_quantity": _decimal_text(filled_quantity, "filled_quantity"),
        "average_price": (
            None if average_price is None else _decimal_text(average_price, "average_price")
        ),
        "reason_code": (
            None if reason_code is None else _reason_code(reason_code, "observation reason_code")
        ),
    }


def _record_projection(record: object) -> dict[str, object]:
    """Return a non-secret execution-record projection for audit comparisons."""

    try:
        state = record.state.value  # type: ignore[attr-defined]
        provider_order_id = record.provider_order_id  # type: ignore[attr-defined]
        filled_quantity = record.filled_quantity  # type: ignore[attr-defined]
        average_price = record.average_price  # type: ignore[attr-defined]
        review_required = record.review_required  # type: ignore[attr-defined]
    except AttributeError as error:
        raise ValueError("invalid execution record") from error
    if type(review_required) is not bool:
        raise ValueError("invalid execution record review_required")
    return {
        "state": _identifier(state, "record state"),
        "provider_order_id": (
            None
            if provider_order_id is None
            else _identifier(provider_order_id, "record provider_order_id")
        ),
        "filled_quantity": _decimal_text(filled_quantity, "record filled_quantity"),
        "average_price": (
            None if average_price is None else _decimal_text(average_price, "record average_price")
        ),
        "review_required": review_required,
    }


@dataclass(frozen=True)
class ReconciliationEvidence:
    """Typed, externally obtained evidence for one previously unknown dispatch.

    ``source_receipt_digest`` is an opaque SHA-256 reference to the reviewed
    provider response retained by the deployment's evidence system.  This
    module deliberately stores no raw provider payload or credential.
    """

    evidence_id: str
    intent_id: str
    observation: Any
    source_receipt_digest: str
    observed_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _identifier(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "intent_id", _identifier(self.intent_id, "intent_id"))
        object.__setattr__(
            self,
            "source_receipt_digest",
            _digest(self.source_receipt_digest, "source_receipt_digest"),
        )
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))

    @property
    def fingerprint(self) -> str:
        """Return a deterministic identity that binds typed evidence to its receipt."""

        return _sha256(
            {
                "evidence_id": self.evidence_id,
                "intent_id": self.intent_id,
                "observation": _observation_projection(self.observation),
                "observed_at": self.observed_at,
                "source_receipt_digest": self.source_receipt_digest,
            }
        )


@dataclass(frozen=True)
class ReleaseIntentFreezeCommand:
    """One operator request to release a single durable dispatch freeze."""

    command_id: str
    scope: str
    intent_id: str
    evidence_id: str
    evidence_fingerprint: str
    issuer_id: str
    reason_code: str
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        for name in ("command_id", "scope", "intent_id", "evidence_id", "issuer_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        object.__setattr__(
            self,
            "evidence_fingerprint",
            _digest(self.evidence_fingerprint, "evidence_fingerprint"),
        )
        object.__setattr__(self, "reason_code", _reason_code(self.reason_code, "reason_code"))
        object.__setattr__(self, "issued_at", _timestamp(self.issued_at, "issued_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")

    @property
    def fingerprint(self) -> str:
        return _sha256(
            {
                "command_id": self.command_id,
                "evidence_fingerprint": self.evidence_fingerprint,
                "evidence_id": self.evidence_id,
                "expires_at": self.expires_at,
                "intent_id": self.intent_id,
                "issued_at": self.issued_at,
                "issuer_id": self.issuer_id,
                "reason_code": self.reason_code,
                "scope": self.scope,
            }
        )


@dataclass(frozen=True)
class AuthorizationDecision:
    """The only accepted result from a code-owned release authorizer."""

    approved: bool
    subject_id: str
    receipt_digest: str
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.approved) is not bool:
            raise ValueError("approved must be a boolean")
        object.__setattr__(self, "subject_id", _identifier(self.subject_id, "subject_id"))
        object.__setattr__(self, "receipt_digest", _digest(self.receipt_digest, "receipt_digest"))
        object.__setattr__(self, "reason_code", _reason_code(self.reason_code, "reason_code"))


@dataclass(frozen=True)
class ReleaseAuthorizationRequest:
    """Redacted context supplied to the deployment's authorization callback."""

    command: ReleaseIntentFreezeCommand
    evidence: ReconciliationEvidence
    reconciled_record: Mapping[str, object]


@dataclass(frozen=True)
class ReconciliationAudit:
    """Durable local proof of one reconciliation fact and its monitor delivery."""

    evidence_id: str
    fingerprint: str
    scope: str
    intent_id: str
    observation: Mapping[str, object]
    record: Mapping[str, object]
    source_receipt_digest: str
    observed_at: float
    monitor_event_id: str
    monitor_published: bool


@dataclass(frozen=True)
class ControlCommandAudit:
    """Durable audit projection for a release command."""

    command: ReleaseIntentFreezeCommand
    status: ControlCommandStatus
    authorization_subject_id: str | None
    authorization_receipt_digest: str | None
    authorization_reason_code: str | None
    release_applied_at: float | None
    released_at: float | None
    outcome_code: str | None


@dataclass(frozen=True)
class ControlledReconciliationResult:
    """Result of a runtime-owned reconcile plus monitor publication."""

    record: Any
    audit: ReconciliationAudit


@dataclass(frozen=True)
class FreezeReleaseResult:
    """Result of a reviewed operator release operation."""

    command_id: str
    intent_id: str
    released: bool
    idempotent: bool


class DurableReconciliationControlAudit:
    """SQLite audit ledger for evidence and reviewed freeze-release commands.

    This is intentionally a separate local ledger from execution, risk, and
    monitor.  It does not invent distributed atomicity: callers persist audit
    and monitor facts before clearing the risk latch, and reassert the latch if
    a post-clear confirmation cannot be persisted.
    """

    def __init__(
        self,
        database_path: Path | str,
        *,
        clock: Callable[[], float] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._database_path = Path(database_path)
        self._clock = clock or time.time
        self._timeout_seconds = timeout_seconds
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def close(self) -> None:
        """Keep a symmetric lifecycle hook; each SQLite operation owns its connection."""

    def get_reconciliation(self, evidence_id: str) -> ReconciliationAudit | None:
        evidence_id = _identifier(evidence_id, "evidence_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reconciliation_control_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return None if row is None else self._reconciliation_from_row(row)

    def record_reconciliation(
        self,
        evidence: ReconciliationEvidence,
        *,
        scope: str,
        observation: Mapping[str, object],
        record: Mapping[str, object],
        monitor_event_id: str,
    ) -> ReconciliationAudit:
        """Persist exact reconciliation evidence before it can release a latch."""

        scope = _identifier(scope, "scope")
        monitor_event_id = _identifier(monitor_event_id, "monitor_event_id")
        fingerprint = evidence.fingerprint
        observation_json = _canonical_json(observation)
        record_json = _canonical_json(record)
        now = self._clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM reconciliation_control_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["fingerprint"]) != fingerprint
                    or str(existing["scope"]) != scope
                    or str(existing["intent_id"]) != evidence.intent_id
                    or str(existing["monitor_event_id"]) != monitor_event_id
                ):
                    raise ControlAuditConflictError("evidence_id was reused with different content")
                return self._reconciliation_from_row(existing)
            connection.execute(
                """
                INSERT INTO reconciliation_control_evidence (
                    evidence_id, fingerprint, scope, intent_id, observation_json, record_json,
                    source_receipt_digest, observed_at, monitor_event_id, monitor_published,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    fingerprint,
                    scope,
                    evidence.intent_id,
                    observation_json,
                    record_json,
                    evidence.source_receipt_digest,
                    evidence.observed_at,
                    monitor_event_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reconciliation_control_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            assert row is not None
            return self._reconciliation_from_row(row)

    def mark_reconciliation_monitor_published(
        self,
        evidence_id: str,
        monitor_event_id: str,
    ) -> ReconciliationAudit:
        """Record that the exact reconcile fact reached the durable monitor outbox."""

        evidence_id = _identifier(evidence_id, "evidence_id")
        monitor_event_id = _identifier(monitor_event_id, "monitor_event_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM reconciliation_control_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise ControlAuditError("reconciliation evidence does not exist")
            if str(row["monitor_event_id"]) != monitor_event_id:
                raise ControlAuditConflictError("monitor event differs from reconciled evidence")
            connection.execute(
                """
                UPDATE reconciliation_control_evidence
                SET monitor_published = 1, updated_at = ? WHERE evidence_id = ?
                """,
                (self._clock(), evidence_id),
            )
            updated = connection.execute(
                "SELECT * FROM reconciliation_control_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            assert updated is not None
            return self._reconciliation_from_row(updated)

    def get_command(self, command_id: str) -> ControlCommandAudit | None:
        command_id = _identifier(command_id, "command_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reconciliation_control_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return None if row is None else self._command_from_row(row)

    def record_command(self, command: ReleaseIntentFreezeCommand) -> ControlCommandAudit:
        """Persist an immutable command before authorization or risk mutation."""

        now = self._clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM reconciliation_control_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["fingerprint"]) != command.fingerprint:
                    raise ControlAuditConflictError("command_id was reused with different content")
                return self._command_from_row(existing)
            connection.execute(
                """
                INSERT INTO reconciliation_control_commands (
                    command_id, fingerprint, scope, intent_id, evidence_id, evidence_fingerprint,
                    issuer_id, reason_code, issued_at, expires_at, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.fingerprint,
                    command.scope,
                    command.intent_id,
                    command.evidence_id,
                    command.evidence_fingerprint,
                    command.issuer_id,
                    command.reason_code,
                    command.issued_at,
                    command.expires_at,
                    ControlCommandStatus.PENDING.value,
                    now,
                ),
            )
            self._append_attempt(
                connection,
                command.command_id,
                "command_recorded",
                "command_recorded",
                now,
            )
            row = connection.execute(
                "SELECT * FROM reconciliation_control_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            assert row is not None
            return self._command_from_row(row)

    def record_authorization(
        self,
        command_id: str,
        decision: AuthorizationDecision,
    ) -> ControlCommandAudit:
        """Persist the identity-bound authorization decision before any release."""

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            status = ControlCommandStatus(str(row["status"]))
            if status in {ControlCommandStatus.RELEASED, ControlCommandStatus.EXPIRED}:
                raise ControlAuditError("command is no longer authorizable")
            next_status = (
                ControlCommandStatus.AUTHORIZED
                if decision.approved
                else ControlCommandStatus.DENIED
            )
            connection.execute(
                """
                UPDATE reconciliation_control_commands
                SET status = ?, authorization_subject_id = ?, authorization_receipt_digest = ?,
                    authorization_reason_code = ?, authorization_at = ?, outcome_code = ?,
                    updated_at = ?
                WHERE command_id = ?
                """,
                (
                    next_status.value,
                    decision.subject_id,
                    decision.receipt_digest,
                    decision.reason_code,
                    now,
                    decision.reason_code,
                    now,
                    command_id,
                ),
            )
            self._append_attempt(
                connection,
                command_id,
                "authorization_approved" if decision.approved else "authorization_denied",
                decision.reason_code,
                now,
            )
            updated = self._command_row(connection, command_id)
            return self._command_from_row(updated)

    def mark_command_expired(self, command_id: str) -> ControlCommandAudit:
        """Durably refuse an expired command without touching the risk latch."""

        return self._set_command_status(
            command_id,
            ControlCommandStatus.EXPIRED,
            "command_expired",
            allowed={ControlCommandStatus.PENDING, ControlCommandStatus.AUTHORIZED},
        )

    def record_release_applied(self, command_id: str) -> ControlCommandAudit:
        """Persist a release preparation before mutating the risk latch.

        ``release_applied_at`` is retained as a stable command-local event
        timestamp even if a later failure reasserts the latch.  It is a durable
        *preparation*, never proof that the freeze was released.  Recording it
        before ``resolve_freeze`` lets a restarted control port detect a crash
        in that cross-store interval and restore the fail-closed latch.
        """

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            if ControlCommandStatus(str(row["status"])) is not ControlCommandStatus.AUTHORIZED:
                raise ControlAuditError("command is not authorized")
            applied_at = row["release_applied_at"]
            if applied_at is None:
                connection.execute(
                    """
                    UPDATE reconciliation_control_commands
                    SET release_applied_at = ?, outcome_code = ?, updated_at = ?
                    WHERE command_id = ?
                    """,
                    (now, "freeze_release_prepared", now, command_id),
                )
                self._append_attempt(
                    connection,
                    command_id,
                    "freeze_release_prepared",
                    "freeze_release_prepared",
                    now,
                )
            updated = self._command_row(connection, command_id)
            return self._command_from_row(updated)

    def mark_command_reasserted(self, command_id: str, outcome_code: str) -> ControlCommandAudit:
        """Return a failed release to pending after the dispatch freeze was restored."""

        outcome_code = _reason_code(outcome_code, "outcome_code")
        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            if ControlCommandStatus(str(row["status"])) is ControlCommandStatus.RELEASED:
                raise ControlAuditError("released command cannot be reasserted")
            connection.execute(
                """
                UPDATE reconciliation_control_commands
                SET status = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (ControlCommandStatus.PENDING.value, outcome_code, now, command_id),
            )
            self._append_attempt(connection, command_id, "freeze_reasserted", outcome_code, now)
            updated = self._command_row(connection, command_id)
            return self._command_from_row(updated)

    def unconfirmed_release_commands(self, scope: str) -> list[ControlCommandAudit]:
        """Return prepared, non-final releases that must be re-frozen on restart.

        A row here is deliberately not a success record.  It says only that a
        process durably intended to clear a latch before it could atomically
        confirm monitor delivery and the final audit.  Callers must reassert
        the matching freeze before allowing any retry.
        """

        scope = _identifier(scope, "scope")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reconciliation_control_commands
                WHERE scope = ? AND status IN (?, ?) AND release_applied_at IS NOT NULL
                ORDER BY command_id ASC
                """,
                (scope, ControlCommandStatus.PENDING.value, ControlCommandStatus.AUTHORIZED.value),
            ).fetchall()
        return [self._command_from_row(row) for row in rows]

    def mark_command_released(self, command_id: str) -> ControlCommandAudit:
        """Commit the final successful release after monitor publication succeeds."""

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            status = ControlCommandStatus(str(row["status"]))
            if status is ControlCommandStatus.RELEASED:
                return self._command_from_row(row)
            if status is not ControlCommandStatus.AUTHORIZED or row["release_applied_at"] is None:
                raise ControlAuditError("command release confirmation is not ready")
            connection.execute(
                """
                UPDATE reconciliation_control_commands
                SET status = ?, released_at = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (ControlCommandStatus.RELEASED.value, now, "freeze_released", now, command_id),
            )
            self._append_attempt(connection, command_id, "freeze_released", "freeze_released", now)
            updated = self._command_row(connection, command_id)
            return self._command_from_row(updated)

    def record_pending_failure(self, command_id: str, outcome_code: str) -> None:
        """Add a durable failed-attempt fact while retaining a retryable pending command."""

        command_id = _identifier(command_id, "command_id")
        outcome_code = _reason_code(outcome_code, "outcome_code")
        now = self._clock()
        with self._transaction() as connection:
            self._command_row(connection, command_id)
            connection.execute(
                """
                UPDATE reconciliation_control_commands SET outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (outcome_code, now, command_id),
            )
            self._append_attempt(connection, command_id, "release_not_applied", outcome_code, now)

    def _set_command_status(
        self,
        command_id: str,
        status: ControlCommandStatus,
        outcome_code: str,
        *,
        allowed: set[ControlCommandStatus],
    ) -> ControlCommandAudit:
        command_id = _identifier(command_id, "command_id")
        outcome_code = _reason_code(outcome_code, "outcome_code")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            current = ControlCommandStatus(str(row["status"]))
            if current is status:
                return self._command_from_row(row)
            if current not in allowed:
                raise ControlAuditError("command has an incompatible lifecycle state")
            connection.execute(
                """
                UPDATE reconciliation_control_commands SET status = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (status.value, outcome_code, now, command_id),
            )
            self._append_attempt(connection, command_id, status.value, outcome_code, now)
            updated = self._command_row(connection, command_id)
            return self._command_from_row(updated)

    @staticmethod
    def _command_row(connection: sqlite3.Connection, command_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM reconciliation_control_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            raise ControlAuditError("control command does not exist")
        return row

    @staticmethod
    def _append_attempt(
        connection: sqlite3.Connection,
        command_id: str,
        event_type: str,
        outcome_code: str,
        occurred_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO reconciliation_control_attempts
                (command_id, event_type, outcome_code, occurred_at)
            VALUES (?, ?, ?, ?)
            """,
            (command_id, event_type, outcome_code, occurred_at),
        )

    @staticmethod
    def _reconciliation_from_row(row: sqlite3.Row) -> ReconciliationAudit:
        return ReconciliationAudit(
            evidence_id=str(row["evidence_id"]),
            fingerprint=str(row["fingerprint"]),
            scope=str(row["scope"]),
            intent_id=str(row["intent_id"]),
            observation=MappingProxyType(json.loads(str(row["observation_json"]))),
            record=MappingProxyType(json.loads(str(row["record_json"]))),
            source_receipt_digest=str(row["source_receipt_digest"]),
            observed_at=float(row["observed_at"]),
            monitor_event_id=str(row["monitor_event_id"]),
            monitor_published=bool(row["monitor_published"]),
        )

    @staticmethod
    def _command_from_row(row: sqlite3.Row) -> ControlCommandAudit:
        command = ReleaseIntentFreezeCommand(
            command_id=str(row["command_id"]),
            scope=str(row["scope"]),
            intent_id=str(row["intent_id"]),
            evidence_id=str(row["evidence_id"]),
            evidence_fingerprint=str(row["evidence_fingerprint"]),
            issuer_id=str(row["issuer_id"]),
            reason_code=str(row["reason_code"]),
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
        )
        return ControlCommandAudit(
            command=command,
            status=ControlCommandStatus(str(row["status"])),
            authorization_subject_id=(
                None
                if row["authorization_subject_id"] is None
                else str(row["authorization_subject_id"])
            ),
            authorization_receipt_digest=(
                None
                if row["authorization_receipt_digest"] is None
                else str(row["authorization_receipt_digest"])
            ),
            authorization_reason_code=(
                None
                if row["authorization_reason_code"] is None
                else str(row["authorization_reason_code"])
            ),
            release_applied_at=(
                None if row["release_applied_at"] is None else float(row["release_applied_at"])
            ),
            released_at=None if row["released_at"] is None else float(row["released_at"]),
            outcome_code=None if row["outcome_code"] is None else str(row["outcome_code"]),
        )

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reconciliation_control_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    source_receipt_digest TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    monitor_event_id TEXT NOT NULL,
                    monitor_published INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_control_evidence_fingerprint
                    ON reconciliation_control_evidence(fingerprint);
                CREATE TABLE IF NOT EXISTS reconciliation_control_commands (
                    command_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL,
                    issuer_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    authorization_subject_id TEXT,
                    authorization_receipt_digest TEXT,
                    authorization_reason_code TEXT,
                    authorization_at REAL,
                    release_applied_at REAL,
                    released_at REAL,
                    outcome_code TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliation_control_attempts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    outcome_code TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reconciliation_control_attempts_command
                    ON reconciliation_control_attempts(command_id, sequence);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self._database_path), timeout=self._timeout_seconds, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")


class ManagedReconciliationControlPort:
    """Runtime-owned reconciliation and reviewed per-intent freeze release.

    A deployment must pass an authorization callback implemented by trusted
    operator code.  It may verify a signature, identity provider decision, or
    offline dual-review receipt, but must return :class:`AuthorizationDecision`.
    There is intentionally no allow-by-default mode.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        state_directory: Path | str,
        authorize: Callable[[ReleaseAuthorizationRequest], AuthorizationDecision],
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not callable(authorize):
            raise ValueError("authorize callback is required")
        self._runtime = runtime
        self._authorize = authorize
        self._clock = clock or time.time
        self._require_runtime_surface()
        state_directory = Path(state_directory).resolve(strict=False)
        self._audit = DurableReconciliationControlAudit(
            state_directory / "operator_control.sqlite3", clock=self._clock
        )
        self._recover_unconfirmed_releases()

    @property
    def audit(self) -> DurableReconciliationControlAudit:
        """Expose read-only durable audit methods to a code-owned monitor adapter."""

        return self._audit

    def close(self) -> None:
        """Close the local audit resource; the composed runtime remains caller-owned."""

        self._audit.close()

    def reconcile(self, evidence: ReconciliationEvidence) -> ControlledReconciliationResult:
        """Record typed unknown-dispatch evidence and publish its monitor fact.

        The method never calls the risk gate's ``resolve_freeze``.  A direct
        execution-facade reconcile also remains unable to release a freeze.
        """

        observation = self._validated_observation(evidence)
        try:
            fingerprint = evidence.fingerprint
        except ValueError as error:
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_INVALID",
                "typed reconciliation evidence could not be canonicalized",
            ) from error
        monitor_event_id = "reconcile:" + fingerprint
        existing = self._audit.get_reconciliation(evidence.evidence_id)
        record = self._runtime.facade.get(evidence.intent_id)
        if record is None:
            raise RuntimePluginError(
                "RECONCILIATION_INTENT_UNKNOWN", "reconciliation intent is absent from this runtime"
            )
        if existing is None:
            if record.state.value == "UNKNOWN":
                try:
                    record = self._runtime.facade.reconcile(observation)
                except Exception as error:
                    raise RuntimePluginError(
                        "RECONCILIATION_PERSISTENCE_FAILED",
                        "typed reconciliation evidence could not be applied",
                    ) from error
            elif not self._record_matches_observation(record, observation):
                raise RuntimePluginError(
                    "RECONCILIATION_REQUIRES_UNKNOWN",
                    "control reconciliation requires an unknown record or its exact crash recovery",
                )
            if not self._record_matches_observation(record, observation):
                raise RuntimePluginError(
                    "RECONCILIATION_RESULT_MISMATCH",
                    "reconciled record does not match the typed provider evidence",
                )
            try:
                existing = self._audit.record_reconciliation(
                    evidence,
                    scope=self._runtime.scope.key,
                    observation=_observation_projection(observation),
                    record=_record_projection(record),
                    monitor_event_id=monitor_event_id,
                )
            except ControlAuditConflictError as error:
                raise RuntimePluginError(
                    "RECONCILIATION_AUDIT_CONFLICT",
                    "reconciliation evidence identity conflicts with durable audit",
                ) from error
            except Exception as error:
                raise RuntimePluginError(
                    "RECONCILIATION_AUDIT_UNCONFIRMED",
                    "reconciliation evidence could not be durably audited",
                ) from error
        else:
            if existing.fingerprint != fingerprint or existing.scope != self._runtime.scope.key:
                raise RuntimePluginError(
                    "RECONCILIATION_AUDIT_CONFLICT",
                    "reconciliation evidence differs from the durable audit",
                )
            if not self._record_matches_projection(record, existing.record):
                raise RuntimePluginError(
                    "RECONCILIATION_EVIDENCE_STALE",
                    "current execution record differs from reviewed reconciliation evidence",
                )
        audit = self._publish_reconciliation_fact(evidence, record, existing)
        return ControlledReconciliationResult(record=record, audit=audit)

    def release_intent_freeze(self, command: ReleaseIntentFreezeCommand) -> FreezeReleaseResult:
        """Release exactly one freeze after evidence, audit, monitor, and authorization pass."""

        if command.scope != self._runtime.scope.key:
            raise RuntimePluginError(
                "CONTROL_SCOPE_MISMATCH", "control command scope does not match the managed runtime"
            )
        try:
            command_audit = self._audit.record_command(command)
        except ControlAuditConflictError as error:
            raise RuntimePluginError(
                "CONTROL_COMMAND_CONFLICT", "control command identity conflicts with durable audit"
            ) from error
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED", "control command could not be durably recorded"
            ) from error
        if command_audit.status is ControlCommandStatus.RELEASED:
            self._assert_freeze_inactive(command.intent_id)
            return FreezeReleaseResult(command.command_id, command.intent_id, True, True)
        if command_audit.status is ControlCommandStatus.DENIED:
            raise RuntimePluginError(
                "CONTROL_AUTHORIZATION_DENIED", "this control command was durably denied"
            )
        if command_audit.status is ControlCommandStatus.EXPIRED:
            raise RuntimePluginError("CONTROL_COMMAND_EXPIRED", "this control command is expired")
        if command.expires_at <= self._clock():
            try:
                self._audit.mark_command_expired(command.command_id)
            except Exception as error:
                raise RuntimePluginError(
                    "CONTROL_AUDIT_UNCONFIRMED", "expired command refusal could not be audited"
                ) from error
            raise RuntimePluginError("CONTROL_COMMAND_EXPIRED", "this control command is expired")

        evidence = self._require_published_evidence(command)
        record = self._runtime.facade.get(command.intent_id)
        if record is None or not self._record_matches_projection(record, evidence.record):
            self._record_pending_failure(command.command_id, "reconciliation_evidence_stale")
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_STALE",
                "current execution record differs from the reconciled evidence",
            )
        request = ReleaseAuthorizationRequest(
            command=command,
            evidence=ReconciliationEvidence(
                evidence_id=evidence.evidence_id,
                intent_id=evidence.intent_id,
                observation=self._observation_from_audit(evidence),
                source_receipt_digest=evidence.source_receipt_digest,
                observed_at=evidence.observed_at,
            ),
            reconciled_record=evidence.record,
        )
        decision = self._authorize_command(command, request)
        if not decision.approved:
            raise RuntimePluginError(
                "CONTROL_AUTHORIZATION_DENIED", "control command was rejected by the authorizer"
            )
        self._publish_authorization_fact(command, decision, evidence)
        self._assert_freeze_active(command.intent_id)
        try:
            applied = self._audit.record_release_applied(command.command_id)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED",
                "dispatch freeze release preparation could not be durably audited",
            ) from error
        try:
            self._runtime.risk_gate.resolve_freeze(
                self._runtime.risk_scope, self._dispatch_freeze_cause(command.intent_id)
            )
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.intent_id, "freeze_resolution_failed"
            )
            raise RuntimePluginError(
                "CONTROL_FREEZE_RESOLUTION_FAILED", "dispatch freeze could not be resolved"
            ) from error
        try:
            self._assert_freeze_inactive(command.intent_id)
        except RuntimePluginError as error:
            self._reassert_after_failed_release(
                command.command_id, command.intent_id, "freeze_resolution_unconfirmed"
            )
            raise RuntimePluginError(
                "CONTROL_FREEZE_RESOLUTION_UNCONFIRMED",
                "dispatch freeze release could not be verified",
            ) from error
        try:
            self._append_monitor_event(
                event_id="freeze-release:" + command.command_id,
                event_type="execution_freeze_released",
                data={
                    "command_id": command.command_id,
                    "intent_id": command.intent_id,
                    "evidence_digest": command.evidence_fingerprint,
                    "issuer_id": command.issuer_id,
                    "release_applied_at": applied.release_applied_at,
                    "state": evidence.record["state"],
                },
                occurred_at=applied.release_applied_at,
            )
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.intent_id, "release_outbox_failed"
            )
            raise RuntimePluginError(
                "CONTROL_MONITOR_OUTBOX_UNCONFIRMED",
                "freeze release monitor fact was not durable; dispatch remains frozen",
            ) from error
        try:
            self._audit.mark_command_released(command.command_id)
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.intent_id, "release_audit_failed"
            )
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED",
                "freeze release confirmation could not be durably audited",
            ) from error
        return FreezeReleaseResult(command.command_id, command.intent_id, True, False)

    def _require_runtime_surface(self) -> None:
        required = (
            "facade",
            "outbox",
            "risk_gate",
            "risk_scope",
            "scope",
            "execution",
            "outbox_event_type",
        )
        if any(not hasattr(self._runtime, name) for name in required):
            raise ValueError("runtime does not expose the managed control surface")
        if not isinstance(getattr(self._runtime.scope, "key", None), str):
            raise ValueError("runtime has an invalid execution scope")

    def _recover_unconfirmed_releases(self) -> None:
        """Reassert every prepared-but-unconfirmed latch after process restart."""

        try:
            commands = self._audit.unconfirmed_release_commands(self._runtime.scope.key)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED",
                "unconfirmed release audit records could not be loaded",
            ) from error
        for audit in commands:
            command = audit.command
            self._reassert_after_failed_release(
                command.command_id, command.intent_id, "restart_release_unconfirmed"
            )

    def _validated_observation(self, evidence: ReconciliationEvidence) -> Any:
        if not isinstance(evidence, ReconciliationEvidence):
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_INVALID", "ReconciliationEvidence is required"
            )
        observation_type = getattr(self._runtime.execution, "ProviderObservation", None)
        if not isinstance(observation_type, type) or not isinstance(
            evidence.observation, observation_type
        ):
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_INVALID",
                "evidence must contain the runtime execution ProviderObservation type",
            )
        observation = evidence.observation
        if observation.intent_id != evidence.intent_id:
            raise RuntimePluginError(
                "RECONCILIATION_IDENTITY_MISMATCH", "provider observation belongs to another intent"
            )
        if observation.state.value == "REJECTED":
            if observation.reason_code is None:
                raise RuntimePluginError(
                    "RECONCILIATION_EVIDENCE_INCOMPLETE",
                    "rejected reconciliation requires a provider reason code",
                )
        elif observation.provider_order_id is None:
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_INCOMPLETE",
                "non-rejected reconciliation requires a provider order identity",
            )
        return observation

    def _publish_reconciliation_fact(
        self,
        evidence: ReconciliationEvidence,
        record: Any,
        audit: ReconciliationAudit,
    ) -> ReconciliationAudit:
        if audit.monitor_published:
            return audit
        try:
            self._append_monitor_event(
                event_id=audit.monitor_event_id,
                event_type="execution_reconciled",
                data={
                    "evidence_digest": audit.fingerprint,
                    "intent_id": evidence.intent_id,
                    "provider_order_id": _record_projection(record)["provider_order_id"],
                    "review_required": _record_projection(record)["review_required"],
                    "state": _record_projection(record)["state"],
                },
                occurred_at=evidence.observed_at,
            )
            return self._audit.mark_reconciliation_monitor_published(
                evidence.evidence_id, audit.monitor_event_id
            )
        except Exception as error:
            raise RuntimePluginError(
                "RECONCILIATION_MONITOR_OUTBOX_UNCONFIRMED",
                "reconciliation monitor fact was not durably published",
            ) from error

    def _require_published_evidence(
        self, command: ReleaseIntentFreezeCommand
    ) -> ReconciliationAudit:
        evidence = self._audit.get_reconciliation(command.evidence_id)
        if (
            evidence is None
            or evidence.scope != self._runtime.scope.key
            or evidence.intent_id != command.intent_id
            or evidence.fingerprint != command.evidence_fingerprint
        ):
            self._record_pending_failure(command.command_id, "reconciliation_evidence_missing")
            raise RuntimePluginError(
                "RECONCILIATION_EVIDENCE_REQUIRED",
                "control release requires matching durable reconciliation evidence",
            )
        if not evidence.monitor_published:
            self._record_pending_failure(command.command_id, "reconciliation_outbox_unconfirmed")
            raise RuntimePluginError(
                "RECONCILIATION_MONITOR_OUTBOX_UNCONFIRMED",
                "control release requires durable reconciliation monitor delivery",
            )
        return evidence

    def _authorize_command(
        self,
        command: ReleaseIntentFreezeCommand,
        request: ReleaseAuthorizationRequest,
    ) -> AuthorizationDecision:
        try:
            decision = self._authorize(request)
        except Exception as error:
            self._record_pending_failure(command.command_id, "authorization_unavailable")
            raise RuntimePluginError(
                "CONTROL_AUTHORIZATION_UNAVAILABLE", "control authorization could not be verified"
            ) from error
        if not isinstance(decision, AuthorizationDecision):
            self._record_pending_failure(command.command_id, "authorization_invalid")
            raise RuntimePluginError(
                "CONTROL_AUTHORIZATION_INVALID",
                "authorizer must return AuthorizationDecision",
            )
        if decision.subject_id != command.issuer_id:
            self._record_pending_failure(command.command_id, "authorization_identity_mismatch")
            raise RuntimePluginError(
                "CONTROL_AUTHORIZATION_IDENTITY_MISMATCH",
                "authorization identity does not match command issuer",
            )
        try:
            self._audit.record_authorization(command.command_id, decision)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED", "authorization decision could not be durably audited"
            ) from error
        return decision

    def _publish_authorization_fact(
        self,
        command: ReleaseIntentFreezeCommand,
        decision: AuthorizationDecision,
        evidence: ReconciliationAudit,
    ) -> None:
        try:
            self._append_monitor_event(
                event_id="freeze-release-authorized:" + command.command_id,
                event_type="execution_freeze_release_authorized",
                data={
                    "authorization_receipt_digest": decision.receipt_digest,
                    "command_id": command.command_id,
                    "evidence_digest": evidence.fingerprint,
                    "intent_id": command.intent_id,
                    "issuer_id": command.issuer_id,
                    "state": evidence.record["state"],
                },
                occurred_at=command.issued_at,
            )
        except Exception as error:
            self._record_pending_failure(command.command_id, "authorization_outbox_failed")
            raise RuntimePluginError(
                "CONTROL_MONITOR_OUTBOX_UNCONFIRMED",
                "authorization monitor fact was not durable; dispatch remains frozen",
            ) from error

    def _append_monitor_event(
        self,
        *,
        event_id: str,
        event_type: str,
        data: Mapping[str, object],
        occurred_at: float | None,
    ) -> None:
        if occurred_at is None:
            raise ValueError("monitor occurred_at is required")
        self._runtime.outbox.append(
            self._runtime.outbox_event_type(
                event_id=event_id,
                scope=self._runtime.scope.key,
                event_type=event_type,
                data=dict(data),
                occurred_at=occurred_at,
            )
        )

    def _assert_freeze_active(self, intent_id: str) -> None:
        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            active_reasons = self._runtime.risk_gate.active_freeze_reasons(self._runtime.risk_scope)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_FREEZE_STATE_UNAVAILABLE", "dispatch freeze state could not be verified"
            ) from error
        if cause_id not in active_reasons:
            raise RuntimePluginError(
                "CONTROL_FREEZE_NOT_ACTIVE", "dispatch freeze is not active for this intent"
            )

    def _assert_freeze_inactive(self, intent_id: str) -> None:
        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            active_reasons = self._runtime.risk_gate.active_freeze_reasons(self._runtime.risk_scope)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_FREEZE_STATE_UNAVAILABLE", "dispatch freeze state could not be verified"
            ) from error
        if cause_id in active_reasons:
            raise RuntimePluginError(
                "CONTROL_RELEASE_STATE_INCONSISTENT",
                "audit says released while the intent dispatch freeze is active",
            )

    def _reassert_after_failed_release(
        self,
        command_id: str,
        intent_id: str,
        outcome_code: str,
    ) -> None:
        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            self._runtime.risk_gate.freeze(self._runtime.risk_scope, cause_id, cause_id)
            self._assert_freeze_active(intent_id)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_FREEZE_REASSERT_FAILED",
                "release outcome is uncertain and the dispatch freeze could not be restored",
            ) from error
        try:
            self._audit.mark_command_reasserted(command_id, outcome_code)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED",
                "reasserted dispatch freeze could not be durably audited",
            ) from error

    def _record_pending_failure(self, command_id: str, outcome_code: str) -> None:
        try:
            self._audit.record_pending_failure(command_id, outcome_code)
        except Exception as error:
            raise RuntimePluginError(
                "CONTROL_AUDIT_UNCONFIRMED", "control refusal could not be durably audited"
            ) from error

    def _dispatch_freeze_cause(self, intent_id: str) -> str:
        return "dispatch-inflight:" + _identifier(intent_id, "intent_id")

    @staticmethod
    def _record_matches_observation(record: Any, observation: Any) -> bool:
        try:
            projection = _record_projection(record)
            observed = _observation_projection(observation)
        except ValueError:
            return False
        if projection["state"] != observed["state"]:
            return False
        if projection["filled_quantity"] != observed["filled_quantity"]:
            return False
        if projection["average_price"] != observed["average_price"]:
            return False
        provider_order_id = observed["provider_order_id"]
        return provider_order_id is None or projection["provider_order_id"] == provider_order_id

    @staticmethod
    def _record_matches_projection(record: Any, expected: Mapping[str, object]) -> bool:
        try:
            return _record_projection(record) == dict(expected)
        except ValueError:
            return False

    def _observation_from_audit(self, evidence: ReconciliationAudit) -> Any:
        """Rebuild the package-owned typed observation for the authorizer's context only."""

        observation = evidence.observation
        provider_observation = self._runtime.execution.ProviderObservation
        return provider_observation(
            intent_id=observation["intent_id"],
            state=observation["state"],
            provider_order_id=observation["provider_order_id"],
            filled_quantity=Decimal(observation["filled_quantity"]),
            average_price=(
                None
                if observation["average_price"] is None
                else Decimal(observation["average_price"])
            ),
            reason_code=observation["reason_code"],
        )


__all__ = [
    "AuthorizationDecision",
    "ControlAuditConflictError",
    "ControlAuditError",
    "ControlCommandAudit",
    "ControlCommandStatus",
    "ControlledReconciliationResult",
    "DurableReconciliationControlAudit",
    "FreezeReleaseResult",
    "ManagedReconciliationControlPort",
    "ReconciliationAudit",
    "ReconciliationEvidence",
    "ReleaseAuthorizationRequest",
    "ReleaseIntentFreezeCommand",
]
