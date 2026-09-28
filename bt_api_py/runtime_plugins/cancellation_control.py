"""Fail-closed control for reconciling an unknown managed cancellation.

The Backtrader bridge creates an account freeze whenever a provider cancellation
attempt has an unknown outcome.  A normal cancellation reconciliation records
typed evidence but intentionally cannot clear that latch.  This composition
module provides the only local release path: immutable evidence, a durable
audit entry, a durable monitor-outbox fact, and an identity-bound authorization
decision must all be present before one exact cancellation freeze can clear.

It owns no provider client and does not acquire provider evidence.  Callers
must supply a typed ``CancelObservation`` obtained by independently controlled
reconciliation code.  Any failure after a local release reasserts the same
freeze; an ``UNKNOWN`` cancellation never releases automatically.
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
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .catalog import RuntimePluginError
from .reconcile_control import AuthorizationDecision, ControlCommandStatus

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CancellationControlAuditError(RuntimeError):
    """Base error for the local cancellation-control audit ledger."""


class CancellationControlAuditConflictError(CancellationControlAuditError):
    """An immutable cancellation evidence or command identity was reused."""


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


def _observation_projection(observation: object) -> dict[str, object]:
    """Return the only redacted cancellation evidence persisted by this module."""

    try:
        state = observation.state.value  # type: ignore[attr-defined]
        cancel_id = observation.cancel_id  # type: ignore[attr-defined]
        target_intent_id = observation.target_intent_id  # type: ignore[attr-defined]
        provider_order_id = observation.provider_order_id  # type: ignore[attr-defined]
        reason_code = observation.reason_code  # type: ignore[attr-defined]
    except AttributeError as error:
        raise ValueError("invalid typed cancellation observation") from error
    return {
        "cancel_id": _identifier(cancel_id, "observation cancel_id"),
        "target_intent_id": _identifier(target_intent_id, "observation target_intent_id"),
        "provider_order_id": _identifier(provider_order_id, "observation provider_order_id"),
        "state": _identifier(state, "observation state"),
        "reason_code": (
            None if reason_code is None else _reason_code(reason_code, "observation reason_code")
        ),
    }


def _record_projection(record: object) -> dict[str, object]:
    """Return a stable, non-secret cancellation record projection for audit checks."""

    try:
        state = record.state.value  # type: ignore[attr-defined]
        cancel_id = record.cancel_id  # type: ignore[attr-defined]
        target_intent_id = record.target_intent_id  # type: ignore[attr-defined]
        provider_order_id = record.provider_order_id  # type: ignore[attr-defined]
        review_required = record.review_required  # type: ignore[attr-defined]
    except AttributeError as error:
        raise ValueError("invalid cancellation record") from error
    if type(review_required) is not bool:
        raise ValueError("invalid cancellation record review_required")
    return {
        "cancel_id": _identifier(cancel_id, "record cancel_id"),
        "target_intent_id": _identifier(target_intent_id, "record target_intent_id"),
        "provider_order_id": _identifier(provider_order_id, "record provider_order_id"),
        "state": _identifier(state, "record state"),
        "review_required": review_required,
    }


@dataclass(frozen=True)
class CancellationReconciliationEvidence:
    """Externally obtained typed evidence for one unknown cancellation attempt."""

    evidence_id: str
    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    observation: Any
    source_receipt_digest: str
    observed_at: float

    def __post_init__(self) -> None:
        for name in ("evidence_id", "cancel_id", "target_intent_id", "provider_order_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        object.__setattr__(
            self,
            "source_receipt_digest",
            _digest(self.source_receipt_digest, "source_receipt_digest"),
        )
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))

    @property
    def fingerprint(self) -> str:
        """Return immutable identity binding typed evidence to its receipt digest."""

        return _sha256(
            {
                "cancel_id": self.cancel_id,
                "evidence_id": self.evidence_id,
                "observation": _observation_projection(self.observation),
                "observed_at": self.observed_at,
                "provider_order_id": self.provider_order_id,
                "source_receipt_digest": self.source_receipt_digest,
                "target_intent_id": self.target_intent_id,
            }
        )


@dataclass(frozen=True)
class ReleaseCancellationFreezeCommand:
    """One reviewed request to clear exactly one unknown-cancel freeze."""

    command_id: str
    scope: str
    cancel_id: str
    evidence_id: str
    evidence_fingerprint: str
    issuer_id: str
    reason_code: str
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        for name in ("command_id", "scope", "cancel_id", "evidence_id", "issuer_id"):
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
                "cancel_id": self.cancel_id,
                "command_id": self.command_id,
                "evidence_fingerprint": self.evidence_fingerprint,
                "evidence_id": self.evidence_id,
                "expires_at": self.expires_at,
                "issued_at": self.issued_at,
                "issuer_id": self.issuer_id,
                "reason_code": self.reason_code,
                "scope": self.scope,
            }
        )


@dataclass(frozen=True)
class CancellationReleaseAuthorizationRequest:
    """Redacted context passed to a deployment-owned authorization callback."""

    command: ReleaseCancellationFreezeCommand
    evidence: CancellationReconciliationEvidence
    reconciled_record: Mapping[str, object]


@dataclass(frozen=True)
class CancellationReconciliationAudit:
    """Durable local evidence and monitor-outbox status for one cancellation."""

    evidence_id: str
    fingerprint: str
    scope: str
    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    observation: Mapping[str, object]
    record: Mapping[str, object]
    source_receipt_digest: str
    observed_at: float
    monitor_event_id: str
    monitor_published: bool


@dataclass(frozen=True)
class CancellationControlCommandAudit:
    """Durable state for a requested cancellation-freeze release."""

    command: ReleaseCancellationFreezeCommand
    status: ControlCommandStatus
    authorization_subject_id: str | None
    authorization_receipt_digest: str | None
    authorization_reason_code: str | None
    release_applied_at: float | None
    released_at: float | None
    outcome_code: str | None


@dataclass(frozen=True)
class ControlledCancellationReconciliationResult:
    """Result of reconciling a cancellation and recording its monitor fact."""

    record: Any
    audit: CancellationReconciliationAudit


@dataclass(frozen=True)
class CancellationFreezeReleaseResult:
    """Result of a reviewed cancellation-freeze release."""

    command_id: str
    cancel_id: str
    released: bool
    idempotent: bool


class DurableCancellationControlAudit:
    """SQLite audit ledger for cancellation evidence and release commands."""

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

    def get_reconciliation(self, evidence_id: str) -> CancellationReconciliationAudit | None:
        evidence_id = _identifier(evidence_id, "evidence_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM cancellation_control_evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        return None if row is None else self._reconciliation_from_row(row)

    def record_reconciliation(
        self,
        evidence: CancellationReconciliationEvidence,
        *,
        scope: str,
        observation: Mapping[str, object],
        record: Mapping[str, object],
        monitor_event_id: str,
    ) -> CancellationReconciliationAudit:
        """Persist immutable evidence before it can release a risk latch."""

        scope = _identifier(scope, "scope")
        monitor_event_id = _identifier(monitor_event_id, "monitor_event_id")
        fingerprint = evidence.fingerprint
        observation_json = _canonical_json(observation)
        record_json = _canonical_json(record)
        now = self._clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM cancellation_control_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["fingerprint"]) != fingerprint
                    or str(existing["scope"]) != scope
                    or str(existing["cancel_id"]) != evidence.cancel_id
                    or str(existing["target_intent_id"]) != evidence.target_intent_id
                    or str(existing["provider_order_id"]) != evidence.provider_order_id
                    or str(existing["monitor_event_id"]) != monitor_event_id
                ):
                    raise CancellationControlAuditConflictError(
                        "evidence_id was reused with different content"
                    )
                return self._reconciliation_from_row(existing)
            connection.execute(
                """
                INSERT INTO cancellation_control_evidence (
                    evidence_id, fingerprint, scope, cancel_id, target_intent_id, provider_order_id,
                    observation_json, record_json, source_receipt_digest, observed_at,
                    monitor_event_id, monitor_published, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    fingerprint,
                    scope,
                    evidence.cancel_id,
                    evidence.target_intent_id,
                    evidence.provider_order_id,
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
                "SELECT * FROM cancellation_control_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            assert row is not None
            return self._reconciliation_from_row(row)

    def mark_reconciliation_monitor_published(
        self, evidence_id: str, monitor_event_id: str
    ) -> CancellationReconciliationAudit:
        """Record that the exact cancellation fact reached the durable outbox."""

        evidence_id = _identifier(evidence_id, "evidence_id")
        monitor_event_id = _identifier(monitor_event_id, "monitor_event_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM cancellation_control_evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if row is None:
                raise CancellationControlAuditError("cancellation evidence does not exist")
            if str(row["monitor_event_id"]) != monitor_event_id:
                raise CancellationControlAuditConflictError(
                    "monitor event differs from cancellation evidence"
                )
            connection.execute(
                """
                UPDATE cancellation_control_evidence
                SET monitor_published = 1, updated_at = ? WHERE evidence_id = ?
                """,
                (self._clock(), evidence_id),
            )
            updated = connection.execute(
                "SELECT * FROM cancellation_control_evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            assert updated is not None
            return self._reconciliation_from_row(updated)

    def get_command(self, command_id: str) -> CancellationControlCommandAudit | None:
        command_id = _identifier(command_id, "command_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM cancellation_control_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return None if row is None else self._command_from_row(row)

    def record_command(
        self, command: ReleaseCancellationFreezeCommand
    ) -> CancellationControlCommandAudit:
        """Persist an immutable release command before authorization or risk mutation."""

        now = self._clock()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM cancellation_control_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["fingerprint"]) != command.fingerprint:
                    raise CancellationControlAuditConflictError(
                        "command_id was reused with different content"
                    )
                return self._command_from_row(existing)
            connection.execute(
                """
                INSERT INTO cancellation_control_commands (
                    command_id, fingerprint, scope, cancel_id, evidence_id, evidence_fingerprint,
                    issuer_id, reason_code, issued_at, expires_at, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.fingerprint,
                    command.scope,
                    command.cancel_id,
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
                connection, command.command_id, "command_recorded", "command_recorded", now
            )
            return self._command_from_row(self._command_row(connection, command.command_id))

    def record_authorization(
        self, command_id: str, decision: AuthorizationDecision
    ) -> CancellationControlCommandAudit:
        """Durably audit an identity-bound authorization before a latch release."""

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            status = ControlCommandStatus(str(row["status"]))
            if status in {ControlCommandStatus.RELEASED, ControlCommandStatus.EXPIRED}:
                raise CancellationControlAuditError("command is no longer authorizable")
            next_status = (
                ControlCommandStatus.AUTHORIZED
                if decision.approved
                else ControlCommandStatus.DENIED
            )
            connection.execute(
                """
                UPDATE cancellation_control_commands
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
            return self._command_from_row(self._command_row(connection, command_id))

    def mark_command_expired(self, command_id: str) -> CancellationControlCommandAudit:
        """Durably refuse an expired command without touching the risk latch."""

        return self._set_command_status(
            command_id,
            ControlCommandStatus.EXPIRED,
            "command_expired",
            allowed={ControlCommandStatus.PENDING, ControlCommandStatus.AUTHORIZED},
        )

    def record_release_applied(self, command_id: str) -> CancellationControlCommandAudit:
        """Persist a release preparation before mutating the risk latch.

        The stable ``release_applied_at`` timestamp records only a durable
        preparation.  It is not a successful release and survives a later
        reassertion so a retry uses the same monitor event identity.  A new
        control port treats every prepared, non-final row as a restart recovery
        obligation and restores the cancellation freeze before retrying.
        """

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            if ControlCommandStatus(str(row["status"])) is not ControlCommandStatus.AUTHORIZED:
                raise CancellationControlAuditError("command is not authorized")
            if row["release_applied_at"] is None:
                connection.execute(
                    """
                    UPDATE cancellation_control_commands
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
            return self._command_from_row(self._command_row(connection, command_id))

    def mark_command_reasserted(
        self, command_id: str, outcome_code: str
    ) -> CancellationControlCommandAudit:
        """Return a failed release to pending after its safety latch was restored."""

        command_id = _identifier(command_id, "command_id")
        outcome_code = _reason_code(outcome_code, "outcome_code")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            if ControlCommandStatus(str(row["status"])) is ControlCommandStatus.RELEASED:
                raise CancellationControlAuditError("released command cannot be reasserted")
            connection.execute(
                """
                UPDATE cancellation_control_commands
                SET status = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (ControlCommandStatus.PENDING.value, outcome_code, now, command_id),
            )
            self._append_attempt(connection, command_id, "freeze_reasserted", outcome_code, now)
            return self._command_from_row(self._command_row(connection, command_id))

    def unconfirmed_release_commands(self, scope: str) -> list[CancellationControlCommandAudit]:
        """Return non-final prepared releases that must re-freeze on restart."""

        scope = _identifier(scope, "scope")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM cancellation_control_commands
                WHERE scope = ? AND status IN (?, ?) AND release_applied_at IS NOT NULL
                ORDER BY command_id ASC
                """,
                (scope, ControlCommandStatus.PENDING.value, ControlCommandStatus.AUTHORIZED.value),
            ).fetchall()
        return [self._command_from_row(row) for row in rows]

    def mark_command_released(self, command_id: str) -> CancellationControlCommandAudit:
        """Commit a final release only after its monitor fact is durable."""

        command_id = _identifier(command_id, "command_id")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            status = ControlCommandStatus(str(row["status"]))
            if status is ControlCommandStatus.RELEASED:
                return self._command_from_row(row)
            if status is not ControlCommandStatus.AUTHORIZED or row["release_applied_at"] is None:
                raise CancellationControlAuditError("command release confirmation is not ready")
            connection.execute(
                """
                UPDATE cancellation_control_commands
                SET status = ?, released_at = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (ControlCommandStatus.RELEASED.value, now, "freeze_released", now, command_id),
            )
            self._append_attempt(connection, command_id, "freeze_released", "freeze_released", now)
            return self._command_from_row(self._command_row(connection, command_id))

    def record_pending_failure(self, command_id: str, outcome_code: str) -> None:
        """Record a failed attempt while retaining a retryable command and freeze."""

        command_id = _identifier(command_id, "command_id")
        outcome_code = _reason_code(outcome_code, "outcome_code")
        now = self._clock()
        with self._transaction() as connection:
            self._command_row(connection, command_id)
            connection.execute(
                """
                UPDATE cancellation_control_commands SET outcome_code = ?, updated_at = ?
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
    ) -> CancellationControlCommandAudit:
        command_id = _identifier(command_id, "command_id")
        outcome_code = _reason_code(outcome_code, "outcome_code")
        now = self._clock()
        with self._transaction() as connection:
            row = self._command_row(connection, command_id)
            current = ControlCommandStatus(str(row["status"]))
            if current is status:
                return self._command_from_row(row)
            if current not in allowed:
                raise CancellationControlAuditError("command has an incompatible lifecycle state")
            connection.execute(
                """
                UPDATE cancellation_control_commands SET status = ?, outcome_code = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (status.value, outcome_code, now, command_id),
            )
            self._append_attempt(connection, command_id, status.value, outcome_code, now)
            return self._command_from_row(self._command_row(connection, command_id))

    @staticmethod
    def _command_row(connection: sqlite3.Connection, command_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM cancellation_control_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            raise CancellationControlAuditError("control command does not exist")
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
            INSERT INTO cancellation_control_attempts
                (command_id, event_type, outcome_code, occurred_at)
            VALUES (?, ?, ?, ?)
            """,
            (command_id, event_type, outcome_code, occurred_at),
        )

    @staticmethod
    def _reconciliation_from_row(row: sqlite3.Row) -> CancellationReconciliationAudit:
        return CancellationReconciliationAudit(
            evidence_id=str(row["evidence_id"]),
            fingerprint=str(row["fingerprint"]),
            scope=str(row["scope"]),
            cancel_id=str(row["cancel_id"]),
            target_intent_id=str(row["target_intent_id"]),
            provider_order_id=str(row["provider_order_id"]),
            observation=MappingProxyType(json.loads(str(row["observation_json"]))),
            record=MappingProxyType(json.loads(str(row["record_json"]))),
            source_receipt_digest=str(row["source_receipt_digest"]),
            observed_at=float(row["observed_at"]),
            monitor_event_id=str(row["monitor_event_id"]),
            monitor_published=bool(row["monitor_published"]),
        )

    @staticmethod
    def _command_from_row(row: sqlite3.Row) -> CancellationControlCommandAudit:
        command = ReleaseCancellationFreezeCommand(
            command_id=str(row["command_id"]),
            scope=str(row["scope"]),
            cancel_id=str(row["cancel_id"]),
            evidence_id=str(row["evidence_id"]),
            evidence_fingerprint=str(row["evidence_fingerprint"]),
            issuer_id=str(row["issuer_id"]),
            reason_code=str(row["reason_code"]),
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
        )
        return CancellationControlCommandAudit(
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
                CREATE TABLE IF NOT EXISTS cancellation_control_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cancel_id TEXT NOT NULL,
                    target_intent_id TEXT NOT NULL,
                    provider_order_id TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    source_receipt_digest TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    monitor_event_id TEXT NOT NULL,
                    monitor_published INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_control_evidence_fingerprint
                    ON cancellation_control_evidence(fingerprint);
                CREATE TABLE IF NOT EXISTS cancellation_control_commands (
                    command_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cancel_id TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS cancellation_control_attempts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    outcome_code TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cancellation_control_attempts_command
                    ON cancellation_control_attempts(command_id, sequence);
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


class ManagedCancellationReconciliationControlPort:
    """Reconcile a cancellation then release only its audited unknown-outcome latch."""

    def __init__(
        self,
        runtime: Any,
        cancellation_facade: Any,
        *,
        state_directory: Path | str,
        authorize: Callable[[CancellationReleaseAuthorizationRequest], AuthorizationDecision],
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not callable(authorize):
            raise ValueError("authorize callback is required")
        self._runtime = runtime
        self._cancellation_facade = cancellation_facade
        self._authorize = authorize
        self._clock = clock or time.time
        self._require_runtime_surface()
        state_directory = Path(state_directory).resolve(strict=False)
        self._audit = DurableCancellationControlAudit(
            state_directory / "cancellation_operator_control.sqlite3", clock=self._clock
        )
        self._recover_unconfirmed_releases()

    @property
    def audit(self) -> DurableCancellationControlAudit:
        """Expose read-only audit methods to a deployment-owned monitor adapter."""

        return self._audit

    def close(self) -> None:
        """Close local audit resources; the composed runtime remains caller-owned."""

        self._audit.close()

    def reconcile(
        self, evidence: CancellationReconciliationEvidence
    ) -> ControlledCancellationReconciliationResult:
        """Apply typed cancellation evidence and publish an immutable monitor fact.

        This method cannot release the unknown-cancel freeze.  It accepts only
        terminal ``CANCELLED`` or ``REJECTED`` observations; ``ACKED`` means a
        cancellation is still in progress and must keep the account frozen.
        """

        observation = self._validated_observation(evidence)
        try:
            fingerprint = evidence.fingerprint
        except ValueError as error:
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_INVALID",
                "typed cancellation evidence could not be canonicalized",
            ) from error
        monitor_event_id = "cancel-reconcile:" + fingerprint
        existing = self._audit.get_reconciliation(evidence.evidence_id)
        record = self._cancellation_facade.get(evidence.cancel_id)
        if record is None:
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_UNKNOWN",
                "cancellation intent is absent from this runtime",
            )
        if existing is None:
            if record.state.value == "UNKNOWN":
                try:
                    record = self._cancellation_facade.reconcile(observation)
                except Exception as error:
                    raise RuntimePluginError(
                        "CANCELLATION_RECONCILIATION_PERSISTENCE_FAILED",
                        "typed cancellation evidence could not be applied",
                    ) from error
            elif not self._record_matches_observation(record, observation):
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_REQUIRES_UNKNOWN",
                    "control reconciliation requires an unknown cancel or its exact crash recovery",
                )
            if not self._is_reviewed_terminal(record):
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_INCOMPLETE",
                    "only terminal cancellation evidence can be reviewed for freeze release",
                )
            if not self._record_matches_observation(record, observation):
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_RESULT_MISMATCH",
                    "reconciled cancellation record does not match typed provider evidence",
                )
            try:
                existing = self._audit.record_reconciliation(
                    evidence,
                    scope=self._runtime.scope.key,
                    observation=_observation_projection(observation),
                    record=_record_projection(record),
                    monitor_event_id=monitor_event_id,
                )
            except CancellationControlAuditConflictError as error:
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_AUDIT_CONFLICT",
                    "cancellation evidence identity conflicts with durable audit",
                ) from error
            except Exception as error:
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_AUDIT_UNCONFIRMED",
                    "cancellation evidence could not be durably audited",
                ) from error
        else:
            if (
                existing.fingerprint != fingerprint
                or existing.scope != self._runtime.scope.key
                or existing.cancel_id != evidence.cancel_id
            ):
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_AUDIT_CONFLICT",
                    "cancellation evidence differs from the durable audit",
                )
            if not self._record_matches_projection(record, existing.record):
                raise RuntimePluginError(
                    "CANCELLATION_RECONCILIATION_EVIDENCE_STALE",
                    "current cancellation record differs from reviewed evidence",
                )
        audit = self._publish_reconciliation_fact(evidence, record, existing)
        return ControlledCancellationReconciliationResult(record=record, audit=audit)

    def release_cancel_freeze(
        self, command: ReleaseCancellationFreezeCommand
    ) -> CancellationFreezeReleaseResult:
        """Release exactly one unknown-cancel freeze after all control gates pass."""

        if command.scope != self._runtime.scope.key:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_SCOPE_MISMATCH",
                "control command scope does not match the managed runtime",
            )
        try:
            command_audit = self._audit.record_command(command)
        except CancellationControlAuditConflictError as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_COMMAND_CONFLICT",
                "control command identity conflicts with durable audit",
            ) from error
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "control command could not be durably recorded",
            ) from error
        if command_audit.status is ControlCommandStatus.RELEASED:
            self._assert_freeze_inactive(command.cancel_id)
            return CancellationFreezeReleaseResult(
                command.command_id, command.cancel_id, True, True
            )
        if command_audit.status is ControlCommandStatus.DENIED:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUTHORIZATION_DENIED",
                "this cancellation control command was durably denied",
            )
        if command_audit.status is ControlCommandStatus.EXPIRED:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_COMMAND_EXPIRED", "this control command is expired"
            )
        if command.expires_at <= self._clock():
            try:
                self._audit.mark_command_expired(command.command_id)
            except Exception as error:
                raise RuntimePluginError(
                    "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                    "expired command refusal could not be audited",
                ) from error
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_COMMAND_EXPIRED", "this control command is expired"
            )

        evidence = self._require_published_evidence(command)
        record = self._cancellation_facade.get(command.cancel_id)
        if record is None or not self._record_matches_projection(record, evidence.record):
            self._record_pending_failure(command.command_id, "cancellation_evidence_stale")
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_STALE",
                "current cancellation record differs from reviewed evidence",
            )
        request = CancellationReleaseAuthorizationRequest(
            command=command,
            evidence=CancellationReconciliationEvidence(
                evidence_id=evidence.evidence_id,
                cancel_id=evidence.cancel_id,
                target_intent_id=evidence.target_intent_id,
                provider_order_id=evidence.provider_order_id,
                observation=self._observation_from_audit(evidence),
                source_receipt_digest=evidence.source_receipt_digest,
                observed_at=evidence.observed_at,
            ),
            reconciled_record=evidence.record,
        )
        decision = self._authorize_command(command, request)
        if not decision.approved:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUTHORIZATION_DENIED",
                "cancellation control command was rejected by the authorizer",
            )
        self._publish_authorization_fact(command, decision, evidence)
        self._assert_freeze_active(command.cancel_id)
        try:
            applied = self._audit.record_release_applied(command.command_id)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "cancellation release preparation could not be durably audited",
            ) from error
        try:
            self._runtime.risk_gate.resolve_freeze(
                self._runtime.risk_scope, self._freeze_cause(command.cancel_id)
            )
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.cancel_id, "freeze_resolution_failed"
            )
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_RESOLUTION_FAILED",
                "cancellation freeze could not be resolved",
            ) from error
        try:
            self._assert_freeze_inactive(command.cancel_id)
        except RuntimePluginError as error:
            self._reassert_after_failed_release(
                command.command_id, command.cancel_id, "freeze_resolution_unconfirmed"
            )
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_RESOLUTION_UNCONFIRMED",
                "cancellation freeze release could not be verified",
            ) from error
        try:
            self._append_monitor_event(
                event_id="cancel-freeze-release:" + command.command_id,
                event_type="cancellation_freeze_released",
                data={
                    "cancel_id": command.cancel_id,
                    "command_id": command.command_id,
                    "evidence_digest": command.evidence_fingerprint,
                    "issuer_id": command.issuer_id,
                    "release_applied_at": applied.release_applied_at,
                    "state": evidence.record["state"],
                },
                occurred_at=applied.release_applied_at,
            )
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.cancel_id, "release_outbox_failed"
            )
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_MONITOR_OUTBOX_UNCONFIRMED",
                "cancellation release monitor fact was not durable; dispatch remains frozen",
            ) from error
        try:
            self._audit.mark_command_released(command.command_id)
        except Exception as error:
            self._reassert_after_failed_release(
                command.command_id, command.cancel_id, "release_audit_failed"
            )
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "cancellation release confirmation could not be durably audited",
            ) from error
        return CancellationFreezeReleaseResult(command.command_id, command.cancel_id, True, False)

    def _require_runtime_surface(self) -> None:
        required = (
            "outbox",
            "risk_gate",
            "risk_scope",
            "scope",
            "execution",
            "outbox_event_type",
        )
        if any(not hasattr(self._runtime, name) for name in required):
            raise ValueError("runtime does not expose the managed cancellation control surface")
        if not isinstance(getattr(self._runtime.scope, "key", None), str):
            raise ValueError("runtime has an invalid execution scope")
        if not callable(getattr(self._cancellation_facade, "get", None)) or not callable(
            getattr(self._cancellation_facade, "reconcile", None)
        ):
            raise ValueError("cancellation facade lacks reconciliation surface")

    def _recover_unconfirmed_releases(self) -> None:
        """Reassert prepared-but-unconfirmed cancellation freezes after restart."""

        try:
            commands = self._audit.unconfirmed_release_commands(self._runtime.scope.key)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "unconfirmed cancellation release records could not be loaded",
            ) from error
        for audit in commands:
            command = audit.command
            self._reassert_after_failed_release(
                command.command_id, command.cancel_id, "restart_release_unconfirmed"
            )

    def _validated_observation(self, evidence: CancellationReconciliationEvidence) -> Any:
        if not isinstance(evidence, CancellationReconciliationEvidence):
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_INVALID",
                "CancellationReconciliationEvidence is required",
            )
        observation_type = getattr(self._runtime.execution, "CancelObservation", None)
        if not isinstance(observation_type, type) or not isinstance(
            evidence.observation, observation_type
        ):
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_INVALID",
                "evidence must contain the runtime execution CancelObservation type",
            )
        observation = evidence.observation
        if (
            observation.cancel_id != evidence.cancel_id
            or observation.target_intent_id != evidence.target_intent_id
            or observation.provider_order_id != evidence.provider_order_id
        ):
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_IDENTITY_MISMATCH",
                "cancellation observation belongs to another durable target",
            )
        if observation.state.value not in {"CANCELLED", "REJECTED"}:
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_INCOMPLETE",
                "only terminal cancellation evidence can support reviewed release",
            )
        if observation.state.value == "REJECTED" and observation.reason_code is None:
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_INCOMPLETE",
                "rejected cancellation reconciliation requires a provider reason code",
            )
        return observation

    def _publish_reconciliation_fact(
        self,
        evidence: CancellationReconciliationEvidence,
        record: Any,
        audit: CancellationReconciliationAudit,
    ) -> CancellationReconciliationAudit:
        if audit.monitor_published:
            return audit
        try:
            projection = _record_projection(record)
            self._append_monitor_event(
                event_id=audit.monitor_event_id,
                event_type="cancellation_reconciled",
                data={
                    "cancel_id": evidence.cancel_id,
                    "evidence_digest": audit.fingerprint,
                    "provider_order_id": projection["provider_order_id"],
                    "state": projection["state"],
                    "target_intent_id": evidence.target_intent_id,
                },
                occurred_at=evidence.observed_at,
            )
            return self._audit.mark_reconciliation_monitor_published(
                evidence.evidence_id, audit.monitor_event_id
            )
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_MONITOR_OUTBOX_UNCONFIRMED",
                "cancellation reconciliation monitor fact was not durably published",
            ) from error

    def _require_published_evidence(
        self, command: ReleaseCancellationFreezeCommand
    ) -> CancellationReconciliationAudit:
        evidence = self._audit.get_reconciliation(command.evidence_id)
        if (
            evidence is None
            or evidence.scope != self._runtime.scope.key
            or evidence.cancel_id != command.cancel_id
            or evidence.fingerprint != command.evidence_fingerprint
        ):
            self._record_pending_failure(command.command_id, "cancellation_evidence_missing")
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_EVIDENCE_REQUIRED",
                "cancellation freeze release requires matching durable evidence",
            )
        if not evidence.monitor_published:
            self._record_pending_failure(command.command_id, "cancellation_outbox_unconfirmed")
            raise RuntimePluginError(
                "CANCELLATION_RECONCILIATION_MONITOR_OUTBOX_UNCONFIRMED",
                "cancellation release requires durable reconciliation monitor delivery",
            )
        return evidence

    def _authorize_command(
        self,
        command: ReleaseCancellationFreezeCommand,
        request: CancellationReleaseAuthorizationRequest,
    ) -> AuthorizationDecision:
        try:
            decision = self._authorize(request)
        except Exception as error:
            self._record_pending_failure(command.command_id, "authorization_unavailable")
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUTHORIZATION_UNAVAILABLE",
                "cancellation control authorization could not be verified",
            ) from error
        if not isinstance(decision, AuthorizationDecision):
            self._record_pending_failure(command.command_id, "authorization_invalid")
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUTHORIZATION_INVALID",
                "authorizer must return AuthorizationDecision",
            )
        if decision.subject_id != command.issuer_id:
            self._record_pending_failure(command.command_id, "authorization_identity_mismatch")
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUTHORIZATION_IDENTITY_MISMATCH",
                "authorization identity does not match command issuer",
            )
        try:
            self._audit.record_authorization(command.command_id, decision)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "authorization decision could not be durably audited",
            ) from error
        return decision

    def _publish_authorization_fact(
        self,
        command: ReleaseCancellationFreezeCommand,
        decision: AuthorizationDecision,
        evidence: CancellationReconciliationAudit,
    ) -> None:
        try:
            self._append_monitor_event(
                event_id="cancel-freeze-release-authorized:" + command.command_id,
                event_type="cancellation_freeze_release_authorized",
                data={
                    "authorization_receipt_digest": decision.receipt_digest,
                    "cancel_id": command.cancel_id,
                    "command_id": command.command_id,
                    "evidence_digest": evidence.fingerprint,
                    "issuer_id": command.issuer_id,
                    "state": evidence.record["state"],
                },
                occurred_at=command.issued_at,
            )
        except Exception as error:
            self._record_pending_failure(command.command_id, "authorization_outbox_failed")
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_MONITOR_OUTBOX_UNCONFIRMED",
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

    def _assert_freeze_active(self, cancel_id: str) -> None:
        cause_id = self._freeze_cause(cancel_id)
        try:
            active_reasons = self._runtime.risk_gate.active_freeze_reasons(self._runtime.risk_scope)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_STATE_UNAVAILABLE",
                "cancellation freeze state could not be verified",
            ) from error
        if cause_id not in active_reasons:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_NOT_ACTIVE",
                "unknown cancellation freeze is not active for this cancellation",
            )

    def _assert_freeze_inactive(self, cancel_id: str) -> None:
        cause_id = self._freeze_cause(cancel_id)
        try:
            active_reasons = self._runtime.risk_gate.active_freeze_reasons(self._runtime.risk_scope)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_STATE_UNAVAILABLE",
                "cancellation freeze state could not be verified",
            ) from error
        if cause_id in active_reasons:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_RELEASE_STATE_INCONSISTENT",
                "audit says released while the cancellation freeze is active",
            )

    def _reassert_after_failed_release(
        self, command_id: str, cancel_id: str, outcome_code: str
    ) -> None:
        cause_id = self._freeze_cause(cancel_id)
        try:
            self._runtime.risk_gate.freeze(self._runtime.risk_scope, cause_id, cause_id)
            self._assert_freeze_active(cancel_id)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_FREEZE_REASSERT_FAILED",
                "release outcome is uncertain and cancellation freeze could not be restored",
            ) from error
        try:
            self._audit.mark_command_reasserted(command_id, outcome_code)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "reasserted cancellation freeze could not be durably audited",
            ) from error

    def _record_pending_failure(self, command_id: str, outcome_code: str) -> None:
        try:
            self._audit.record_pending_failure(command_id, outcome_code)
        except Exception as error:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED",
                "control refusal could not be durably audited",
            ) from error

    @staticmethod
    def _is_reviewed_terminal(record: Any) -> bool:
        try:
            projection = _record_projection(record)
        except ValueError:
            return False
        return (
            projection["state"] in {"CANCELLED", "REJECTED"} and not projection["review_required"]
        )

    @staticmethod
    def _record_matches_observation(record: Any, observation: Any) -> bool:
        try:
            return _record_projection(record) == {
                "cancel_id": _observation_projection(observation)["cancel_id"],
                "target_intent_id": _observation_projection(observation)["target_intent_id"],
                "provider_order_id": _observation_projection(observation)["provider_order_id"],
                "state": _observation_projection(observation)["state"],
                "review_required": False,
            }
        except ValueError:
            return False

    @staticmethod
    def _record_matches_projection(record: Any, expected: Mapping[str, object]) -> bool:
        try:
            return _record_projection(record) == dict(expected)
        except ValueError:
            return False

    def _observation_from_audit(self, evidence: CancellationReconciliationAudit) -> Any:
        """Rebuild the package-owned typed observation for the authorizer only."""

        observation = evidence.observation
        return self._runtime.execution.CancelObservation(
            cancel_id=observation["cancel_id"],
            target_intent_id=observation["target_intent_id"],
            provider_order_id=observation["provider_order_id"],
            state=observation["state"],
            reason_code=observation["reason_code"],
        )

    def _freeze_cause(self, cancel_id: str) -> str:
        scope_key = getattr(self._runtime.scope, "key", None)
        if not isinstance(scope_key, str) or not scope_key:
            raise RuntimePluginError(
                "CANCELLATION_CONTROL_SCOPE_MISMATCH",
                "runtime cancellation freeze lacks an execution scope",
            )
        return "cancel-outcome-unknown:" + scope_key + ":" + _identifier(cancel_id, "cancel_id")


__all__ = [
    "CancellationControlAuditConflictError",
    "CancellationControlAuditError",
    "CancellationControlCommandAudit",
    "CancellationFreezeReleaseResult",
    "CancellationReconciliationAudit",
    "CancellationReconciliationEvidence",
    "CancellationReleaseAuthorizationRequest",
    "ControlledCancellationReconciliationResult",
    "DurableCancellationControlAudit",
    "ManagedCancellationReconciliationControlPort",
    "ReleaseCancellationFreezeCommand",
]
