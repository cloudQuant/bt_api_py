"""Local durable recovery authority for managed execution.

The execution, risk, and monitor packages intentionally keep independent data
models.  This coordinator does *not* turn their local transactions, or a
provider request, into one distributed transaction.  Instead it owns a small,
local work journal that closes the dangerous recovery gap:

* the exact intent/scope/fingerprint is durable before the provider port runs;
* an interrupted prepared dispatch is never replayed blindly;
* a provider result has a deterministic monitor fact which can be re-appended
  after a crash; and
* a confirmed dispatch freeze is resolved only after that fact is durable.

Every provider effect remains at-least-unknown across a process crash.  The
journal is deliberately provider-free and stores only redacted identifiers and
execution state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManagedRecoveryCoordinatorError(RuntimeError):
    """The local managed-recovery authority cannot prove a safe transition."""


@dataclass(frozen=True)
class ManagedRecoveryWork:
    """One immutable provider-dispatch work item in the local authority journal."""

    scope_key: str
    intent_id: str
    payload_sha256: str
    phase: str
    record_state: str | None
    review_required: bool
    freeze_status: str
    risk_settlement_status: str
    recovery_reason: str | None
    created_at_ns: int
    updated_at_ns: int

    @property
    def reconciliation_required(self) -> bool:
        """Whether a provider read/reconciliation is required before a retry."""

        return self.phase == "RECONCILIATION_REQUIRED"


@dataclass(frozen=True)
class ManagedRecoveryEvent:
    """One deterministic monitor fact that may be appended idempotently."""

    event_id: str
    scope_key: str
    intent_id: str
    state: str
    data: dict[str, str]
    occurred_at: float
    emitted: bool


@dataclass(frozen=True)
class ManagedRecoveryReport:
    """Result of a local no-provider recovery pass."""

    recovered_unknown_intent_ids: tuple[str, ...]
    reconciliation_required_intent_ids: tuple[str, ...]
    emitted_event_ids: tuple[str, ...]
    resolved_freeze_intent_ids: tuple[str, ...]


class DurableManagedRecoveryCoordinator:
    """SQLite authority journal for one or more managed execution scopes.

    ``ManagedExecutionFacade`` continues to own the durable single provider
    dispatch claim and its writer lease.  Callers must hold that facade lease
    while mutating this coordinator.  The coordinator deliberately has no
    provider client and never performs network I/O.
    """

    _SCHEMA_VERSION = 2
    _PREPARED = "DISPATCH_PREPARED"
    _RESULT_RECORDED = "RESULT_RECORDED"
    _RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    _FREEZE_PENDING = "PENDING"
    _FREEZE_ATTEMPTED = "RELEASE_ATTEMPTED"
    _FREEZE_RESOLVED = "RESOLVED"
    _FREEZE_NOT_APPLICABLE = "NOT_APPLICABLE"
    # A confirmed execution record and the risk reservation live in separate
    # local databases.  This explicit journal state is the proof boundary that
    # prevents a restart from releasing a dispatch freeze merely because the
    # execution record is known.  The permit must be durably settled first.
    _SETTLEMENT_PENDING = "PENDING"
    _SETTLEMENT_CONFIRMED = "SETTLED"
    _SETTLEMENT_NOT_APPLICABLE = "NOT_APPLICABLE"
    _CONFIRMED_STATES = frozenset(
        {"ACKED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"}
    )

    def __init__(
        self, database_path: str | Path, *, timeout_seconds: float = 5.0
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._database_path = Path(database_path)
        self._timeout_seconds = float(timeout_seconds)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def database_path(self) -> Path:
        """Return the one local authority journal path."""

        return self._database_path

    def close(self) -> None:
        """Keep lifecycle symmetry; this coordinator owns no open connection."""

    def prepare_dispatch(self, intent: Any) -> ManagedRecoveryWork:
        """Durably record the sole provider-dispatch work item before provider I/O.

        The facade invokes this through its pre-dispatch hook only after its
        execution claim and risk dispatch latch are durable.  Re-entering a
        prepared work item is rejected so a corrupted or alternate caller
        cannot reinterpret a restart as permission to send again.
        """

        scope_key, intent_id, fingerprint = self._intent_identity(intent)
        now_ns = time.time_ns()
        with self._transaction() as connection:
            existing = self._work_row(connection, scope_key, intent_id)
            if existing is not None:
                self._assert_work_identity(existing, fingerprint)
                raise ManagedRecoveryCoordinatorError(
                    "provider dispatch is already durably prepared; reconciliation is required"
                )
            connection.execute(
                """
                INSERT INTO managed_recovery_work(
                    scope_key, intent_id, payload_sha256, phase, record_state,
                    review_required, freeze_status, risk_settlement_status,
                    created_at_ns, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_key,
                    intent_id,
                    fingerprint,
                    self._PREPARED,
                    "DISPATCHING",
                    0,
                    self._FREEZE_NOT_APPLICABLE,
                    self._SETTLEMENT_NOT_APPLICABLE,
                    now_ns,
                    now_ns,
                ),
            )
            row = self._work_row(connection, scope_key, intent_id)
            assert row is not None
            return self._work_from_row(row)

    def record_result(self, intent: Any, record: Any) -> ManagedRecoveryWork:
        """Persist an execution result and its deterministic monitor event.

        This operation contains no outbox call.  Therefore a crash after this
        commit leaves a replayable local fact instead of losing the monitor
        transition.  Repeating the same result preserves the first immutable
        event payload and cannot create another event id.
        """

        scope_key, intent_id, fingerprint = self._intent_identity(intent)
        record_intent_id = getattr(record, "intent_id", None)
        record_scope_key = getattr(record, "scope_key", None)
        record_fingerprint = getattr(record, "payload_sha256", None)
        if (
            record_intent_id != intent_id
            or record_scope_key != scope_key
            or record_fingerprint != fingerprint
        ):
            raise ManagedRecoveryCoordinatorError(
                "execution result identity does not match intent"
            )
        state_value = self._state_value(record)
        review_required = bool(getattr(record, "review_required", False))
        permit_reference = getattr(record, "permit_reference", None)
        occurred_at = self._record_occurred_at(record)
        event_id = self.event_id(scope_key, intent_id, state_value)
        event_data = {
            "intent_id": intent_id,
            "scope_digest": self._scope_digest(scope_key),
            "state": state_value,
        }
        now_ns = time.time_ns()
        freeze_status = self._freeze_status_for_result(
            state_value, review_required, permit_reference
        )
        settlement_status = self._settlement_status_for_result(
            state_value, review_required, permit_reference
        )
        phase = (
            self._RECONCILIATION_REQUIRED
            if state_value == "UNKNOWN" or review_required
            else self._RESULT_RECORDED
        )
        recovery_reason = (
            "unknown_provider_outcome"
            if state_value == "UNKNOWN"
            else "review_required"
            if review_required
            else None
        )
        with self._transaction() as connection:
            existing = self._work_row(connection, scope_key, intent_id)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO managed_recovery_work(
                        scope_key, intent_id, payload_sha256, phase, record_state,
                        review_required, freeze_status, risk_settlement_status,
                        recovery_reason, created_at_ns, updated_at_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope_key,
                        intent_id,
                        fingerprint,
                        phase,
                        state_value,
                        int(review_required),
                        freeze_status,
                        settlement_status,
                        recovery_reason,
                        now_ns,
                        now_ns,
                    ),
                )
            else:
                self._assert_work_identity(existing, fingerprint)
                # A resolved confirmed dispatch must never regress because a
                # repeated submit only rereads the same immutable execution record.
                prior_freeze = str(existing["freeze_status"])
                effective_freeze = (
                    prior_freeze
                    if prior_freeze in {self._FREEZE_ATTEMPTED, self._FREEZE_RESOLVED}
                    else freeze_status
                )
                effective_phase = (
                    self._RECONCILIATION_REQUIRED
                    if phase == self._RECONCILIATION_REQUIRED
                    else str(existing["phase"])
                    if str(existing["phase"]) == self._FREEZE_RESOLVED
                    else phase
                )
                prior_settlement = str(existing["risk_settlement_status"])
                effective_settlement = (
                    self._SETTLEMENT_CONFIRMED
                    if prior_settlement == self._SETTLEMENT_CONFIRMED
                    else settlement_status
                )
                connection.execute(
                    """
                    UPDATE managed_recovery_work
                    SET phase = ?, record_state = ?, review_required = ?, freeze_status = ?,
                        risk_settlement_status = ?, recovery_reason = ?, updated_at_ns = ?
                    WHERE scope_key = ? AND intent_id = ?
                    """,
                    (
                        effective_phase,
                        state_value,
                        int(review_required),
                        effective_freeze,
                        effective_settlement,
                        recovery_reason
                        if recovery_reason is not None
                        else existing["recovery_reason"],
                        now_ns,
                        scope_key,
                        intent_id,
                    ),
                )
            self._insert_event_if_absent(
                connection,
                event_id=event_id,
                scope_key=scope_key,
                intent_id=intent_id,
                payload_sha256=fingerprint,
                state=state_value,
                data=event_data,
                occurred_at=occurred_at,
                created_at_ns=now_ns,
            )
            row = self._work_row(connection, scope_key, intent_id)
            assert row is not None
            return self._work_from_row(row)

    def require_reconciliation(
        self,
        *,
        scope_key: str,
        intent_id: str,
        reason: str,
    ) -> ManagedRecoveryWork:
        """Latch an existing prepared work item for typed provider reconciliation."""

        if not isinstance(reason, str) or not reason.replace("_", "").isalnum():
            raise ValueError("invalid recovery reason")
        with self._transaction() as connection:
            row = self._work_row(connection, scope_key, intent_id)
            if row is None:
                raise ManagedRecoveryCoordinatorError("unknown recovery work item")
            connection.execute(
                """
                UPDATE managed_recovery_work
                SET phase = ?, recovery_reason = ?, updated_at_ns = ?
                WHERE scope_key = ? AND intent_id = ?
                """,
                (
                    self._RECONCILIATION_REQUIRED,
                    reason,
                    time.time_ns(),
                    scope_key,
                    intent_id,
                ),
            )
            updated = self._work_row(connection, scope_key, intent_id)
            assert updated is not None
            return self._work_from_row(updated)

    def work_for(self, scope_key: str, intent_id: str) -> ManagedRecoveryWork | None:
        """Read one local work item without provider or outbox activity."""

        with self._connection() as connection:
            row = self._work_row(connection, scope_key, intent_id)
        return None if row is None else self._work_from_row(row)

    def pending_work(self, scope_key: str) -> tuple[ManagedRecoveryWork, ...]:
        """Return all recoverable local work for exactly one execution scope."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_recovery_work
                WHERE scope_key = ?
                ORDER BY created_at_ns ASC, intent_id ASC
                """,
                (scope_key,),
            ).fetchall()
        return tuple(self._work_from_row(row) for row in rows)

    def append_pending_monitor_events(
        self, scope_key: str, outbox: Any, event_type: Any
    ) -> tuple[str, ...]:
        """Append pending facts through the monitor package's idempotent outbox.

        The outbox owns delivery semantics.  A crash after ``append`` but before
        this journal marks the item emitted is safe because the next append uses
        the same immutable event id and the monitor outbox de-duplicates it.
        """

        emitted: list[str] = []
        for event in self.pending_events(scope_key):
            try:
                monitor_event = event_type(
                    event_id=event.event_id,
                    scope=event.scope_key,
                    event_type="execution_state",
                    data=event.data,
                    occurred_at=event.occurred_at,
                )
                outbox.append(monitor_event)
            except Exception as error:
                raise ManagedRecoveryCoordinatorError(
                    "monitor outbox did not confirm the deterministic execution fact"
                ) from error
            self._mark_event_emitted(event.event_id)
            emitted.append(event.event_id)
        return tuple(emitted)

    def pending_events(self, scope_key: str) -> tuple[ManagedRecoveryEvent, ...]:
        """Return monitor facts not yet locally confirmed in the monitor outbox."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_recovery_events
                WHERE scope_key = ? AND emitted = 0
                ORDER BY created_at_ns ASC, event_id ASC
                """,
                (scope_key,),
            ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def freeze_status_for(self, scope_key: str, intent_id: str) -> str:
        """Return the durable auto-resolution status for one confirmed work item."""

        with self._connection() as connection:
            row = self._work_row(connection, scope_key, intent_id)
        if row is None:
            raise ManagedRecoveryCoordinatorError("unknown recovery work item")
        return str(row["freeze_status"])

    def mark_freeze_release_attempted(self, scope_key: str, intent_id: str) -> None:
        """Persist that recovery may safely finish an interrupted local release."""

        self._update_freeze_status(
            scope_key,
            intent_id,
            expected={self._FREEZE_PENDING, self._FREEZE_ATTEMPTED},
            target=self._FREEZE_ATTEMPTED,
        )

    def mark_freeze_resolved(self, scope_key: str, intent_id: str) -> None:
        """Persist completion after the named local risk latch was resolved."""

        self._update_freeze_status(
            scope_key,
            intent_id,
            expected={
                self._FREEZE_PENDING,
                self._FREEZE_ATTEMPTED,
                self._FREEZE_RESOLVED,
            },
            target=self._FREEZE_RESOLVED,
        )

    def risk_settlement_status_for(self, scope_key: str, intent_id: str) -> str:
        """Return whether the execution-linked risk permit is durably settled."""

        with self._connection() as connection:
            row = self._work_row(connection, scope_key, intent_id)
        if row is None:
            raise ManagedRecoveryCoordinatorError("unknown recovery work item")
        return str(row["risk_settlement_status"])

    def mark_risk_settlement_confirmed(self, scope_key: str, intent_id: str) -> None:
        """Persist proof that a confirmed dispatch's permit cannot later expire.

        This must occur only after the risk owner has atomically reported the
        permit as settled (or already settled).  It is intentionally separate
        from the execution result because those two component stores cannot
        share one transaction.
        """

        with self._transaction() as connection:
            row = self._work_row(connection, scope_key, intent_id)
            if row is None:
                raise ManagedRecoveryCoordinatorError("unknown recovery work item")
            actual = str(row["risk_settlement_status"])
            if actual not in {self._SETTLEMENT_PENDING, self._SETTLEMENT_CONFIRMED}:
                raise ManagedRecoveryCoordinatorError(
                    "recovery work is not eligible for risk-settlement confirmation"
                )
            connection.execute(
                """
                UPDATE managed_recovery_work
                SET risk_settlement_status = ?, updated_at_ns = ?
                WHERE scope_key = ? AND intent_id = ?
                """,
                (self._SETTLEMENT_CONFIRMED, time.time_ns(), scope_key, intent_id),
            )

    @staticmethod
    def event_id(scope_key: str, intent_id: str, state: str) -> str:
        """Return a scope-qualified deterministic monitor event id."""

        digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:24]
        return "managed.execution." + digest + "." + intent_id + "." + state.lower()

    def _update_freeze_status(
        self,
        scope_key: str,
        intent_id: str,
        *,
        expected: set[str],
        target: str,
    ) -> None:
        with self._transaction() as connection:
            row = self._work_row(connection, scope_key, intent_id)
            if row is None:
                raise ManagedRecoveryCoordinatorError("unknown recovery work item")
            actual = str(row["freeze_status"])
            if actual not in expected:
                raise ManagedRecoveryCoordinatorError(
                    "recovery work is not eligible for dispatch-freeze resolution"
                )
            if (
                target == self._FREEZE_RESOLVED
                and str(row["risk_settlement_status"]) != self._SETTLEMENT_CONFIRMED
            ):
                raise ManagedRecoveryCoordinatorError(
                    "dispatch freeze cannot resolve before risk settlement is proven"
                )
            phase = (
                self._FREEZE_RESOLVED
                if target == self._FREEZE_RESOLVED
                else str(row["phase"])
            )
            connection.execute(
                """
                UPDATE managed_recovery_work
                SET freeze_status = ?, phase = ?, updated_at_ns = ?
                WHERE scope_key = ? AND intent_id = ?
                """,
                (target, phase, time.time_ns(), scope_key, intent_id),
            )

    def _mark_event_emitted(self, event_id: str) -> None:
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE managed_recovery_events SET emitted = 1 WHERE event_id = ?",
                (event_id,),
            )
            if result.rowcount != 1:
                raise ManagedRecoveryCoordinatorError("unknown recovery monitor event")

    def _insert_event_if_absent(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        scope_key: str,
        intent_id: str,
        payload_sha256: str,
        state: str,
        data: dict[str, str],
        occurred_at: float,
        created_at_ns: int,
    ) -> None:
        data_json = self._canonical_json(data)
        fingerprint = self._event_fingerprint(scope_key, state, data_json, occurred_at)
        existing = connection.execute(
            "SELECT fingerprint FROM managed_recovery_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["fingerprint"]) != fingerprint:
                raise ManagedRecoveryCoordinatorError(
                    "deterministic monitor event id conflicts with another result"
                )
            return
        connection.execute(
            """
            INSERT INTO managed_recovery_events(
                event_id, scope_key, intent_id, payload_sha256, state, data_json,
                occurred_at, fingerprint, emitted, created_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                event_id,
                scope_key,
                intent_id,
                payload_sha256,
                state,
                data_json,
                occurred_at,
                fingerprint,
                created_at_ns,
            ),
        )

    def _initialize_schema(self) -> None:
        # sqlite3.executescript() manages its own transaction boundary.  Do
        # not wrap it in _transaction(), otherwise SQLite commits before our
        # context manager reaches COMMIT and reports "no transaction is active".
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS managed_recovery_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS managed_recovery_work (
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    record_state TEXT,
                    review_required INTEGER NOT NULL DEFAULT 0,
                    freeze_status TEXT NOT NULL,
                    risk_settlement_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                    recovery_reason TEXT,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL,
                    PRIMARY KEY(scope_key, intent_id)
                );
                CREATE TABLE IF NOT EXISTS managed_recovery_events (
                    event_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    fingerprint TEXT NOT NULL,
                    emitted INTEGER NOT NULL DEFAULT 0,
                    created_at_ns INTEGER NOT NULL,
                    FOREIGN KEY(scope_key, intent_id)
                        REFERENCES managed_recovery_work(scope_key, intent_id)
                );
                CREATE INDEX IF NOT EXISTS managed_recovery_pending_events
                    ON managed_recovery_events(scope_key, emitted, created_at_ns);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(managed_recovery_work)").fetchall()
            }
            if "risk_settlement_status" not in columns:
                connection.execute(
                    """
                    ALTER TABLE managed_recovery_work
                    ADD COLUMN risk_settlement_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'
                    """
                )
            connection.execute(
                "INSERT OR IGNORE INTO managed_recovery_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(self._SCHEMA_VERSION)),
            )
            row = connection.execute(
                "SELECT value FROM managed_recovery_meta WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            if row is None:
                raise ManagedRecoveryCoordinatorError("unsupported managed recovery schema")
            if str(row["value"]) == "1":
                # Version 1 has no durable proof that a confirmed record's
                # permit was settled.  Treat every non-review confirmed record
                # as pending, which is conservative: restart must re-check the
                # risk owner before it may release any freeze.
                connection.execute(
                    """
                    UPDATE managed_recovery_work
                    SET risk_settlement_status = CASE
                        WHEN record_state IN ('ACKED', 'PARTIALLY_FILLED', 'FILLED',
                                              'CANCELLED', 'REJECTED')
                             AND review_required = 0
                        THEN ?
                        ELSE ?
                    END
                    """,
                    (self._SETTLEMENT_PENDING, self._SETTLEMENT_NOT_APPLICABLE),
                )
                connection.execute(
                    "UPDATE managed_recovery_meta SET value = ? WHERE key = ?",
                    (str(self._SCHEMA_VERSION), "schema_version"),
                )
            elif str(row["value"]) != str(self._SCHEMA_VERSION):
                raise ManagedRecoveryCoordinatorError(
                    "unsupported managed recovery schema"
                )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self._database_path),
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        except sqlite3.Error as error:
            raise ManagedRecoveryCoordinatorError(
                "managed recovery journal is unavailable"
            ) from error
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
                try:
                    connection.execute("COMMIT")
                except sqlite3.Error as error:
                    connection.execute("ROLLBACK")
                    raise ManagedRecoveryCoordinatorError(
                        "managed recovery journal commit failed"
                    ) from error

    @staticmethod
    def _work_row(
        connection: sqlite3.Connection, scope_key: str, intent_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM managed_recovery_work
            WHERE scope_key = ? AND intent_id = ?
            """,
            (scope_key, intent_id),
        ).fetchone()

    @classmethod
    def _work_from_row(cls, row: sqlite3.Row) -> ManagedRecoveryWork:
        return ManagedRecoveryWork(
            scope_key=str(row["scope_key"]),
            intent_id=str(row["intent_id"]),
            payload_sha256=str(row["payload_sha256"]),
            phase=str(row["phase"]),
            record_state=None
            if row["record_state"] is None
            else str(row["record_state"]),
            review_required=bool(row["review_required"]),
            freeze_status=str(row["freeze_status"]),
            risk_settlement_status=str(row["risk_settlement_status"]),
            recovery_reason=(
                None if row["recovery_reason"] is None else str(row["recovery_reason"])
            ),
            created_at_ns=int(row["created_at_ns"]),
            updated_at_ns=int(row["updated_at_ns"]),
        )

    @classmethod
    def _event_from_row(cls, row: sqlite3.Row) -> ManagedRecoveryEvent:
        try:
            data = json.loads(str(row["data_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise ManagedRecoveryCoordinatorError(
                "stored monitor event is unreadable"
            ) from error
        if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
            raise ManagedRecoveryCoordinatorError("stored monitor event is invalid")
        return ManagedRecoveryEvent(
            event_id=str(row["event_id"]),
            scope_key=str(row["scope_key"]),
            intent_id=str(row["intent_id"]),
            state=str(row["state"]),
            data={str(key): str(value) for key, value in data.items()},
            occurred_at=float(row["occurred_at"]),
            emitted=bool(row["emitted"]),
        )

    @staticmethod
    def _intent_identity(intent: Any) -> tuple[str, str, str]:
        scope = getattr(intent, "scope", None)
        scope_key = getattr(scope, "key", None)
        intent_id = getattr(intent, "intent_id", None)
        fingerprint = getattr(intent, "fingerprint", None)
        if (
            not isinstance(scope_key, str)
            or not scope_key.strip()
            or not isinstance(intent_id, str)
            or not intent_id.strip()
            or not isinstance(fingerprint, str)
            or not fingerprint.strip()
        ):
            raise ManagedRecoveryCoordinatorError(
                "invalid managed execution intent identity"
            )
        if len(fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in fingerprint
        ):
            raise ManagedRecoveryCoordinatorError(
                "invalid managed execution intent fingerprint"
            )
        return scope_key, intent_id, fingerprint

    @staticmethod
    def _state_value(record: Any) -> str:
        state = getattr(record, "state", None)
        value = getattr(state, "value", state)
        if not isinstance(value, str) or not value.strip() or value != value.upper():
            raise ManagedRecoveryCoordinatorError("invalid durable execution state")
        return value

    @staticmethod
    def _record_occurred_at(record: Any) -> float:
        updated_at_ns = getattr(record, "updated_at_ns", None)
        if not isinstance(updated_at_ns, int) or updated_at_ns <= 0:
            raise ManagedRecoveryCoordinatorError(
                "execution result lacks durable update time"
            )
        return updated_at_ns / 1_000_000_000

    @classmethod
    def _freeze_status_for_result(
        cls,
        state_value: str,
        review_required: bool,
        permit_reference: Any,
    ) -> str:
        if (
            state_value in cls._CONFIRMED_STATES
            and not review_required
            and isinstance(permit_reference, str)
            and permit_reference.strip()
        ):
            return cls._FREEZE_PENDING
        return cls._FREEZE_NOT_APPLICABLE

    @classmethod
    def _settlement_status_for_result(
        cls,
        state_value: str,
        review_required: bool,
        permit_reference: Any,
    ) -> str:
        if (
            state_value in cls._CONFIRMED_STATES
            and not review_required
            and isinstance(permit_reference, str)
            and permit_reference.strip()
        ):
            return cls._SETTLEMENT_PENDING
        return cls._SETTLEMENT_NOT_APPLICABLE

    @staticmethod
    def _scope_digest(scope_key: str) -> str:
        """Strip the display-only scope prefix on Python 3.8 and newer."""

        return scope_key[6:] if scope_key.startswith("scope:") else scope_key

    @staticmethod
    def _assert_work_identity(row: sqlite3.Row, fingerprint: str) -> None:
        if str(row["payload_sha256"]) != fingerprint:
            raise ManagedRecoveryCoordinatorError(
                "intent id was reused with another payload"
            )

    @staticmethod
    def _canonical_json(data: dict[str, str]) -> str:
        try:
            return json.dumps(
                data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        except (TypeError, ValueError) as error:
            raise ManagedRecoveryCoordinatorError(
                "monitor event data is not serializable"
            ) from error

    @staticmethod
    def _event_fingerprint(
        scope_key: str, state: str, data_json: str, occurred_at: float
    ) -> str:
        canonical = json.dumps(
            {
                "scope_key": scope_key,
                "state": state,
                "data": json.loads(data_json),
                "occurred_at": occurred_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DurableManagedRecoveryCoordinator",
    "ManagedRecoveryCoordinatorError",
    "ManagedRecoveryEvent",
    "ManagedRecoveryReport",
    "ManagedRecoveryWork",
]
