"""Persistent server-side authority for coordinator-owned group order legs.

The deployment control plane owns :class:`ExecutionGroupAuthorityControl` and
activates one exact ServiceRegistry registration at a time.  The service
handler receives only :class:`ExecutionGroupAuthorityGuard`; a trusted SDK
adapter must bind the actual ``ServiceCallContext`` around the synchronous
group-leg call.  No generation or registration id is accepted from RPC data.

Dispatch holds are committed before the guard yields. A normal return removes a
hold only after the injected verifier reads the exact attempt and a known
durable outcome from the account owner's execution store. An exception,
unknown result, or process crash leaves an unresolved hold which blocks retries
and authority changes until a trusted verifier proves the exact attempt safe to
release. Holds have no timeout. SQLite transactions are short and never span
the provider call.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionEvent,
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    GroupLegAuthorization,
    GroupPermitBinding,
    OrderIntent,
    SqliteExecutionStore,
    WriterLease,
    risk_account_scope_fingerprint,
    writer_lease_fingerprint,
)

from .service_registry import ServiceCallContext

_SCHEMA_VERSION = 1
_SQLITE_MAX_INT = (1 << 63) - 1


class ExecutionGroupAuthorityError(RuntimeError):
    """Base error for trusted group-authority operations."""


class GroupCoordinatorDeniedError(ExecutionGroupAuthorityError, PermissionError):
    """The current ServiceRegistry registration has no active delegation."""


class GroupAuthorityBusyError(ExecutionGroupAuthorityError):
    """A dispatch hold prevents activation, revocation, or reconciliation."""


class GroupAuthorityUnavailableError(ExecutionGroupAuthorityError):
    """Persistent authority state is unavailable or has an unresolved hold."""


class GroupAuthorityClosedError(ExecutionGroupAuthorityError):
    """The authority resource has been closed."""


class GroupAuthorityReconciliationRejectedError(ExecutionGroupAuthorityError):
    """A trusted verifier did not establish a safe hold release."""


# Keep the short error names used by the initial internal API draft as aliases.
GroupCoordinatorDenied = GroupCoordinatorDeniedError
GroupAuthorityBusy = GroupAuthorityBusyError
GroupAuthorityUnavailable = GroupAuthorityUnavailableError
GroupAuthorityClosed = GroupAuthorityClosedError
GroupAuthorityReconciliationRejected = GroupAuthorityReconciliationRejectedError


@dataclass(frozen=True, slots=True)
class CoordinatorGeneration:
    """Receipt for a generation allocated by the trusted deployment control."""

    principal_ref: str
    registration_id: str
    generation: int
    portfolio_ref: str
    account_ref: str
    strategy_id: str
    execution_scope_key: str
    risk_scope_fingerprint: str
    active: bool


@dataclass(frozen=True, slots=True)
class GroupDispatchHold:
    """Durable facts needed to reconcile one interrupted group dispatch."""

    hold_id: str
    authority_key: str
    principal_ref: str
    registration_id: str
    coordinator_generation: int
    portfolio_ref: str
    account_ref: str
    strategy_id: str
    execution_scope_key: str
    intent_id: str
    intent_fingerprint: str
    binding_fingerprint: str
    risk_scope_fingerprint: str
    execution_writer_lease_fingerprint: str
    account_writer_epoch: int

    @property
    def authorization_attempt_id(self) -> str:
        """The persisted hold ID is the one exact group authorization attempt."""

        return self.hold_id


class GroupDispatchCompletionVerifier(Protocol):
    """Verify normal completion from the account owner's durable execution store.

    The verifier receives no caller proof. It must read the exact store row,
    scope, immutable binding and authorization attempt named by ``hold``.
    UNKNOWN, missing, mismatched, or unreadable evidence must return false.
    """

    def __call__(self, hold: GroupDispatchHold, /) -> bool: ...


class GroupDispatchReconciliationVerifier(Protocol):
    """Trusted deployment hook that checks current durable execution facts.

    A verifier should accept only an exact, durably observed safe disposition
    for ``hold`` and reject UNKNOWN, missing, stale, or mismatched records.
    """

    def __call__(self, hold: GroupDispatchHold, proof: object, /) -> bool: ...


class ExecutionStoreGroupCompletionVerifier:
    """Read exact durable group-attempt facts from a configured SQLite store.

    Supply the actual borrowed execution store and the immutable target scopes
    captured by trusted resource-provider code. A normal guard exit is accepted
    only when this store contains this hold's exact authorization attempt and
    a known provider-attributed result, or a pre-dispatch terminal rejection.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        *,
        execution_scopes: Iterable[ExecutionScope],
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise TypeError("an actual SqliteExecutionStore is required")
        scopes = tuple(execution_scopes)
        if not scopes or any(type(scope) is not ExecutionScope for scope in scopes):
            raise TypeError("trusted ExecutionScope targets are required")
        by_key = {scope.key: scope for scope in scopes}
        if len(by_key) != len(scopes):
            raise ValueError("duplicate execution scope targets")
        self._store = store
        self._scopes = by_key

    def __call__(self, hold: GroupDispatchHold, /) -> bool:
        if type(hold) is not GroupDispatchHold:
            return False
        exact = self._read_exact_hold(hold)
        if exact is None:
            return False
        scope, record, binding, _authorization = exact
        return self._known_completion(record, binding, scope)

    def inspect_terminal_replay(
        self,
        *,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        execution_scope: ExecutionScope,
        principal_ref: str,
        writer_lease: WriterLease,
    ) -> bool | None:
        """Classify an existing same-ID row without creating a new attempt.

        ``None`` means no row exists and the caller may create a normal durable
        hold. ``True`` means the current trusted caller may receive this exact
        already-known terminal result without Risk or provider work. Every
        other existing row or unreadable fact is denied. Historical
        authorization attempt IDs remain exact; a replay never fabricates a
        replacement persisted attempt.
        """

        if (
            type(intent) is not OrderIntent
            or type(binding) is not GroupPermitBinding
            or type(execution_scope) is not ExecutionScope
            or type(writer_lease) is not WriterLease
            or type(principal_ref) is not str
            or not principal_ref
            or intent.scope != execution_scope
            or execution_scope.key not in self._scopes
            or self._scopes[execution_scope.key] != execution_scope
        ):
            return False
        try:
            self._store.assert_writer_lease(execution_scope, writer_lease)
            record = self._store.get(intent.intent_id, scope=execution_scope)
        except Exception:
            return False
        if record is None:
            return None
        try:
            stored_intent = self._store.get_intent(intent.intent_id, scope=execution_scope)
            attempts = self._store.list_group_authorization_attempts(
                intent.intent_id, scope=execution_scope
            )
        except Exception:
            return False
        if (
            type(record) is not ExecutionRecord
            or record.intent_id != intent.intent_id
            or record.scope_key != execution_scope.key
            or record.payload_sha256 != intent.fingerprint
            or type(stored_intent) is not OrderIntent
            or stored_intent.to_payload() != intent.to_payload()
            or type(record.group_binding) is not GroupPermitBinding
            or record.group_binding != binding
            or record.group_binding.fingerprint != binding.fingerprint
        ):
            return False

        prior_attempts = tuple(
            attempt
            for attempt in attempts
            if self._historical_attempt_matches(
                attempt,
                binding=binding,
                principal_ref=principal_ref,
            )
        )
        if len(prior_attempts) != 1:
            return False
        known = self._known_completion(record, binding, execution_scope)
        try:
            self._store.assert_writer_lease(execution_scope, writer_lease)
        except Exception:
            return False
        return known

    def _read_exact_hold(
        self, hold: GroupDispatchHold
    ) -> tuple[ExecutionScope, ExecutionRecord, GroupPermitBinding, GroupLegAuthorization] | None:
        scope = self._scopes.get(hold.execution_scope_key)
        if type(scope) is not ExecutionScope or scope.key != hold.execution_scope_key:
            return None
        try:
            record = self._store.get(hold.intent_id, scope=scope)
            if (
                type(record) is not ExecutionRecord
                or record.intent_id != hold.intent_id
                or record.scope_key != hold.execution_scope_key
                or record.payload_sha256 != hold.intent_fingerprint
                or type(record.group_binding) is not GroupPermitBinding
                or record.group_binding.fingerprint != hold.binding_fingerprint
            ):
                return None
            binding = record.group_binding
            if (
                binding.portfolio_ref != hold.portfolio_ref
                or binding.account_ref != hold.account_ref
                or binding.strategy_id != hold.strategy_id
                or binding.risk_scope_fingerprint != hold.risk_scope_fingerprint
            ):
                return None
            attempts = self._store.list_group_authorization_attempts(hold.intent_id, scope=scope)
        except Exception:
            return None

        matching = tuple(
            attempt
            for attempt in attempts
            if type(attempt) is GroupLegAuthorization
            and attempt.authorization_attempt_id == hold.hold_id
        )
        if len(matching) != 1:
            return None
        authorization = matching[0]
        if (
            authorization.principal_ref != hold.principal_ref
            or authorization.portfolio_ref != hold.portfolio_ref
            or authorization.account_ref != hold.account_ref
            or authorization.strategy_id != hold.strategy_id
            or authorization.coordinator_epoch != hold.coordinator_generation
            or authorization.group_fingerprint != binding.group_fingerprint
            or authorization.binding_fingerprint != hold.binding_fingerprint
            or authorization.deadline_ns != binding.deadline_ns
            or authorization.account_writer_epoch != hold.account_writer_epoch
            or authorization.risk_scope_fingerprint != hold.risk_scope_fingerprint
            or authorization.execution_writer_lease_fingerprint
            != hold.execution_writer_lease_fingerprint
            or authorization.action != "orders.submit_group_leg"
        ):
            return None
        return scope, record, binding, authorization

    @staticmethod
    def _historical_attempt_matches(
        attempt: GroupLegAuthorization,
        *,
        binding: GroupPermitBinding,
        principal_ref: str,
    ) -> bool:
        if type(attempt) is not GroupLegAuthorization:
            return False
        lease_fingerprint = attempt.execution_writer_lease_fingerprint
        return (
            attempt.principal_ref == principal_ref
            and attempt.portfolio_ref == binding.portfolio_ref
            and attempt.account_ref == binding.account_ref
            and attempt.strategy_id == binding.strategy_id
            and attempt.group_id == binding.group_id
            and attempt.leg_id == binding.leg_id
            and type(attempt.coordinator_epoch) is int
            and attempt.coordinator_epoch > 0
            and attempt.group_fingerprint == binding.group_fingerprint
            and attempt.binding_fingerprint == binding.fingerprint
            and attempt.deadline_ns == binding.deadline_ns
            and type(attempt.account_writer_epoch) is int
            and attempt.account_writer_epoch > 0
            and type(lease_fingerprint) is str
            and re.fullmatch(r"[0-9a-f]{64}", lease_fingerprint) is not None
            and attempt.risk_scope_fingerprint == binding.risk_scope_fingerprint
            and attempt.action == "orders.submit_group_leg"
        )

    def _known_completion(
        self,
        record: ExecutionRecord,
        binding: GroupPermitBinding,
        scope: ExecutionScope,
    ) -> bool:
        known_dispatched_states = {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
        }
        if record.state in known_dispatched_states:
            if (
                record.dispatch_attempts != 1
                or record.unknown_reason is not None
                or record.review_required
            ):
                return False
            if record.state is ExecutionState.REJECTED and record.provider_order_id is None:
                return (
                    record.filled_quantity == 0
                    and record.average_price is None
                    and record.cumulative_commission is None
                    and record.permit_reference == binding.permit_id
                    and self._provider_rejection_provenance(record, binding, scope)
                )
            return (
                type(record.provider_order_id) is str
                and bool(record.provider_order_id)
                and record.provider_order_id == record.provider_order_id.strip()
            )

        # SqliteExecutionStore emits these terminal states only from its
        # PENDING_ADMISSION reject path, which forbids any dispatch or provider
        # identity. A claimed dispatch blocked later is not accepted here.
        return (
            record.state in {ExecutionState.BLOCKED, ExecutionState.REJECTED}
            and record.dispatch_attempts == 0
            and record.provider_order_id is None
            and record.filled_quantity == 0
            and record.average_price is None
            and record.cumulative_commission is None
            and record.unknown_reason is not None
            and not record.review_required
        )

    def _provider_rejection_provenance(
        self,
        record: ExecutionRecord,
        binding: GroupPermitBinding,
        scope: ExecutionScope,
    ) -> bool:
        try:
            bounds = self._store.execution_outbox_bounds()
            count, first, last, high_water = bounds
            if count <= 0 or first != 1 or last != count or high_water != last:
                return False
            incarnation = self._store.journal_source_identity().get("generation")
            if type(incarnation) is not str or not incarnation:
                return False
            matching: list[ExecutionEvent] = []
            after_sequence = 0
            while True:
                page = self._store.read_outbox(
                    after_sequence=after_sequence,
                    limit=10_000,
                    scope=scope,
                )
                if type(page) is not tuple:
                    return False
                if not page:
                    break
                previous_sequence = after_sequence
                for event in page:
                    if (
                        type(event) is not ExecutionEvent
                        or event.sequence <= previous_sequence
                        or event.sequence > high_water
                        or event.scope_key != scope.key
                        or event.journal_incarnation_id != incarnation
                    ):
                        return False
                    previous_sequence = event.sequence
                    if event.intent_id == record.intent_id:
                        matching.append(event)
                after_sequence = previous_sequence
                if len(page) < 10_000:
                    break
            if self._store.execution_outbox_bounds() != bounds:
                return False
            if self._store.journal_source_identity().get("generation") != incarnation:
                return False
        except Exception:
            return False

        by_type: dict[str, list[ExecutionEvent]] = {}
        for event in matching:
            by_type.setdefault(event.event_type, []).append(event)
        if set(by_type) != {
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
        } or any(len(events) != 1 for events in by_type.values()):
            return False
        recorded = by_type["intent_recorded"][0]
        admitted = by_type["intent_admitted"][0]
        claimed = by_type["dispatch_claimed"][0]
        observed = by_type["provider_observation"][0]
        return (
            recorded.sequence < admitted.sequence < claimed.sequence < observed.sequence
            and recorded.state is ExecutionState.PENDING_ADMISSION
            and recorded.payload
            == {
                "payload_sha256": record.payload_sha256,
                "group_binding_sha256": binding.fingerprint,
            }
            and admitted.state is ExecutionState.PENDING_DISPATCH
            and admitted.payload == {"permit_reference": binding.permit_id}
            and claimed.state is ExecutionState.DISPATCHING
            and claimed.payload == {"dispatch_attempt": 1}
            and observed.state is ExecutionState.REJECTED
            and type(observed.payload) is dict
            and set(observed.payload)
            == {
                "provider_order_id",
                "filled_quantity",
                "average_price",
                "cumulative_commission",
                "reason_code",
                "source",
            }
            and observed.payload["provider_order_id"] is None
            and observed.payload["filled_quantity"] == "0"
            and observed.payload["average_price"] is None
            and observed.payload["cumulative_commission"] is None
            and type(observed.payload["reason_code"]) is str
            and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", observed.payload["reason_code"]) is not None
            and observed.payload["source"] == "provider"
        )


class ExecutionGroupAuthority:
    """Persistent authority directory plus separate control and dispatch APIs.

    Construct this from trusted account-owner resource-provider code.  Keep
    ``control`` in deployment/operator code and inject only ``guard`` into the
    execution service.  The guard requires ``bind_service_call(context)`` so
    the SDK adapter can pass the context minted by the live ServiceRegistry;
    it will not infer registration identity from request arguments.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        completion_verifier: GroupDispatchCompletionVerifier | None = None,
        reconciliation_verifier: GroupDispatchReconciliationVerifier | None = None,
        monotonic_clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not callable(completion_verifier) and completion_verifier is not None:
            raise TypeError("completion_verifier must be callable")
        if not callable(reconciliation_verifier) and reconciliation_verifier is not None:
            raise TypeError("reconciliation_verifier must be callable")
        raw_path = os.fspath(database_path)
        if type(raw_path) is not str or not raw_path or raw_path == ":memory:":
            raise ValueError("a persistent authority database path is required")
        if not callable(monotonic_clock_ns):
            raise TypeError("monotonic_clock_ns must be callable")
        self._path = Path(raw_path).resolve()
        self._completion_verifier = completion_verifier
        self._verifier = reconciliation_verifier
        self._monotonic_clock_ns = monotonic_clock_ns
        self._state_lock = threading.RLock()
        self._key_locks_lock = threading.Lock()
        self._key_locks: dict[str, threading.RLock] = {}
        self._closed = False
        self._active_operations = 0
        self._active_hold_ids: set[str] = set()
        self._unresolved_hold_ids: set[str] = set()
        self._call_context: contextvars.ContextVar[ServiceCallContext | None] = (
            contextvars.ContextVar(f"execution_group_authority_call_{id(self)}", default=None)
        )
        self.control = ExecutionGroupAuthorityControl(self)
        self.guard = ExecutionGroupAuthorityGuard(self)
        self._initialize()
        # Holds already present at construction are presumed orphaned.  They
        # are never expired or inferred safe from elapsed time.
        self._unresolved_hold_ids.update(hold.hold_id for hold in self.pending_holds())

    @property
    def ready(self) -> bool:
        """Whether no non-live dispatch hold is awaiting proof."""

        with self._state_lock:
            if self._closed:
                return False
            active = set(self._active_hold_ids)
            unresolved = set(self._unresolved_hold_ids)
        try:
            present = {hold.hold_id for hold in self.pending_holds()}
        except ExecutionGroupAuthorityError:
            return False
        # Another process's live hold is also not verifiably live here, so it
        # keeps this owner unready until the hold is removed or reconciled.
        return present <= active and not (present & unresolved)

    def pending_holds(self) -> tuple[GroupDispatchHold, ...]:
        with self._operation(), self._connection() as connection:
            rows = connection.execute(
                "SELECT hold_json FROM group_dispatch_holds ORDER BY hold_id"
            ).fetchall()
        return tuple(_hold_from_json(row["hold_json"]) for row in rows)

    def close(self) -> None:
        """Close this handle without releasing any persisted dispatch hold."""

        with self._state_lock:
            if self._active_operations:
                raise GroupAuthorityBusyError("authority operations are still active")
            self._closed = True

    @contextmanager
    def _operation(self) -> Iterator[None]:
        with self._state_lock:
            if self._closed:
                raise GroupAuthorityClosedError("execution group authority is closed")
            self._active_operations += 1
        try:
            yield
        finally:
            with self._state_lock:
                self._active_operations -= 1

    @contextmanager
    def _key_lock(self, authority_key: str) -> Iterator[None]:
        with self._key_locks_lock:
            lock = self._key_locks.setdefault(authority_key, threading.RLock())
        with lock:
            yield

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS execution_group_authority_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS execution_group_coordinators (
                        authority_key TEXT PRIMARY KEY,
                        generation INTEGER NOT NULL CHECK(generation > 0),
                        active INTEGER NOT NULL CHECK(active IN (0, 1)),
                        principal_ref TEXT NOT NULL,
                        registration_id TEXT NOT NULL,
                        identity_json TEXT NOT NULL,
                        wire_scope_json TEXT NOT NULL,
                        execution_scope_json TEXT NOT NULL,
                        execution_scope_key TEXT NOT NULL,
                        portfolio_ref TEXT NOT NULL,
                        account_ref TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        risk_scope_fingerprint TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS execution_group_registration_history (
                        authority_key TEXT NOT NULL,
                        registration_id TEXT NOT NULL,
                        generation INTEGER NOT NULL CHECK(generation > 0),
                        PRIMARY KEY(authority_key, registration_id)
                    );
                    CREATE TABLE IF NOT EXISTS group_dispatch_holds (
                        hold_id TEXT PRIMARY KEY,
                        authority_key TEXT NOT NULL,
                        hold_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS group_dispatch_holds_authority
                        ON group_dispatch_holds(authority_key);
                    """
                )
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT value FROM execution_group_authority_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO execution_group_authority_meta(key, value) VALUES(?, ?)",
                        ("schema_version", str(_SCHEMA_VERSION)),
                    )
                elif row["value"] != str(_SCHEMA_VERSION):
                    raise GroupAuthorityUnavailableError("unsupported authority schema")
                connection.commit()
        except ExecutionGroupAuthorityError:
            raise
        except Exception as error:
            raise GroupAuthorityUnavailableError(
                "authority database initialization failed"
            ) from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=10.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
        except Exception as error:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
            raise GroupAuthorityUnavailableError("authority database is unavailable") from error
        assert connection is not None
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                with suppress(Exception):
                    connection.rollback()
                raise

    def _authority_key(
        self,
        context: ServiceCallContext,
    ) -> str:
        scope = context.scope
        value = {
            "execution_domain_ref": scope.execution_domain_ref,
            "portfolio_ref": scope.portfolio_ref,
            "account_ref": scope.account_ref,
            "strategy_ref": scope.strategy_ref,
        }
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()

    def _validate_registered_scope(
        self,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
    ) -> None:
        if type(context) is not ServiceCallContext:
            raise GroupCoordinatorDeniedError("trusted ServiceCallContext required")
        if type(execution_scope) is not ExecutionScope:
            raise GroupCoordinatorDeniedError("trusted execution scope required")
        identity = context.identity
        scope = context.scope
        if (
            type(identity) is not WireIdentity
            or identity.role is not WireRole.SERVICE
            or type(scope) is not WireScope
            or type(context.registration_id) is not str
            or not context.registration_id
            or type(context.principal_ref) is not str
            or context.principal_ref != identity.principal_ref
            or not scope.portfolio_ref
            or not scope.account_ref
            or not scope.strategy_ref
            # Group and leg are per-request child identities. A Registry
            # registration is long-lived and therefore pins only the
            # coordinator's stable account/strategy authority scope.
            or scope.group_id is not None
            or scope.leg_id is not None
            or scope.account_refs
            or scope.manual_book_ref is not None
            or scope.account_ref != execution_scope.account_ref
            or scope.strategy_ref != execution_scope.strategy_id
        ):
            raise GroupCoordinatorDeniedError("invalid coordinator registration context")

    def _stored_coordinator(
        self,
        connection: sqlite3.Connection,
        authority_key: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM execution_group_coordinators WHERE authority_key=?",
            (authority_key,),
        ).fetchone()

    def _has_holds(self, connection: sqlite3.Connection, authority_key: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM group_dispatch_holds WHERE authority_key=? LIMIT 1",
                (authority_key,),
            ).fetchone()
            is not None
        )

    def _release_hold(self, hold: GroupDispatchHold) -> None:
        with self._operation(), self._key_lock(hold.authority_key):  # noqa: SIM117
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT hold_json FROM group_dispatch_holds WHERE hold_id=?",
                    (hold.hold_id,),
                ).fetchone()
                if row is None or _hold_from_json(row["hold_json"]) != hold:
                    raise GroupAuthorityUnavailableError("dispatch hold changed before release")
                connection.execute(
                    "DELETE FROM group_dispatch_holds WHERE hold_id=?", (hold.hold_id,)
                )

    def _mark_hold_active(self, hold_id: str) -> None:
        with self._state_lock:
            self._unresolved_hold_ids.discard(hold_id)
            self._active_hold_ids.add(hold_id)

    def _mark_hold_unresolved(self, hold_id: str) -> None:
        with self._state_lock:
            self._active_hold_ids.discard(hold_id)
            self._unresolved_hold_ids.add(hold_id)

    def _mark_hold_released(self, hold_id: str) -> None:
        with self._state_lock:
            self._active_hold_ids.discard(hold_id)
            self._unresolved_hold_ids.discard(hold_id)


class ExecutionGroupAuthorityControl:
    """Trusted deployment/operator API; do not expose this object over RPC."""

    def __init__(self, owner: ExecutionGroupAuthority) -> None:
        self._owner = owner

    def activate(
        self,
        context: ServiceCallContext,
        *,
        execution_scope: ExecutionScope,
        risk_account_scope_key: str,
    ) -> CoordinatorGeneration:
        """Allocate a new server-side coordinator generation for this mapping.

        ``context`` must be captured from a real ServiceRegistry registration
        by trusted deployment code.  A repeated call is a new generation even
        for the same registration id, so callers must make activation an
        explicit coordinator-start action rather than a request operation.
        """

        owner = self._owner
        owner._validate_registered_scope(context, execution_scope)
        if type(risk_account_scope_key) is not str:
            raise GroupCoordinatorDenied("trusted Risk AccountScope.key required")
        risk_fingerprint = risk_account_scope_fingerprint(risk_account_scope_key)
        authority_key = owner._authority_key(context)
        with owner._operation(), owner._key_lock(authority_key):  # noqa: SIM117
            with owner._transaction() as connection:
                previous = owner._stored_coordinator(connection, authority_key)
                if owner._has_holds(connection, authority_key):
                    raise GroupAuthorityBusyError("active dispatch hold blocks coordinator change")
                if (
                    connection.execute(
                        "SELECT 1 FROM execution_group_registration_history "
                        "WHERE authority_key=? AND registration_id=?",
                        (authority_key, context.registration_id),
                    ).fetchone()
                    is not None
                ):
                    raise GroupCoordinatorDeniedError(
                        "a fresh ServiceRegistry registration is required for activation"
                    )
                generation = 1 if previous is None else int(previous["generation"]) + 1
                if generation > _SQLITE_MAX_INT:
                    raise GroupAuthorityUnavailableError("coordinator generation exhausted")
                identity_json = _json(_identity_payload(context.identity))
                wire_scope_json = _json(_wire_scope_payload(context.scope))
                execution_scope_json = _json(execution_scope.to_payload())
                connection.execute(
                    """
                        INSERT INTO execution_group_coordinators(
                            authority_key, generation, active, principal_ref, registration_id,
                            identity_json, wire_scope_json, execution_scope_json,
                            execution_scope_key, portfolio_ref, account_ref, strategy_id,
                            risk_scope_fingerprint
                        ) VALUES(?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(authority_key) DO UPDATE SET
                            generation=excluded.generation,
                            active=1,
                            principal_ref=excluded.principal_ref,
                            registration_id=excluded.registration_id,
                            identity_json=excluded.identity_json,
                            wire_scope_json=excluded.wire_scope_json,
                            execution_scope_json=excluded.execution_scope_json,
                            execution_scope_key=excluded.execution_scope_key,
                            portfolio_ref=excluded.portfolio_ref,
                            account_ref=excluded.account_ref,
                            strategy_id=excluded.strategy_id,
                            risk_scope_fingerprint=excluded.risk_scope_fingerprint
                        """,
                    (
                        authority_key,
                        generation,
                        context.principal_ref,
                        context.registration_id,
                        identity_json,
                        wire_scope_json,
                        execution_scope_json,
                        execution_scope.key,
                        context.scope.portfolio_ref,
                        execution_scope.account_ref,
                        execution_scope.strategy_id,
                        risk_fingerprint,
                    ),
                )
                connection.execute(
                    "INSERT INTO execution_group_registration_history "
                    "(authority_key, registration_id, generation) VALUES(?, ?, ?)",
                    (authority_key, context.registration_id, generation),
                )
        return CoordinatorGeneration(
            context.principal_ref,
            context.registration_id,
            generation,
            context.scope.portfolio_ref,
            execution_scope.account_ref,
            execution_scope.strategy_id,
            execution_scope.key,
            risk_fingerprint,
            True,
        )

    def revoke(
        self,
        context: ServiceCallContext,
        *,
        execution_scope: ExecutionScope,
    ) -> CoordinatorGeneration:
        """Revoke only the exact currently active registration mapping."""

        owner = self._owner
        owner._validate_registered_scope(context, execution_scope)
        authority_key = owner._authority_key(context)
        with owner._operation(), owner._key_lock(authority_key):  # noqa: SIM117
            with owner._transaction() as connection:
                row = owner._stored_coordinator(connection, authority_key)
                if (
                    row is None
                    or not row["active"]
                    or row["registration_id"] != context.registration_id
                    or row["execution_scope_key"] != execution_scope.key
                    or row["identity_json"] != _json(_identity_payload(context.identity))
                    or row["wire_scope_json"] != _json(_wire_scope_payload(context.scope))
                ):
                    raise GroupCoordinatorDeniedError("registration is stale or already revoked")
                if owner._has_holds(connection, authority_key):
                    raise GroupAuthorityBusyError("active dispatch hold blocks revocation")
                generation = int(row["generation"]) + 1
                if generation > _SQLITE_MAX_INT:
                    raise GroupAuthorityUnavailableError("coordinator generation exhausted")
                connection.execute(
                    "UPDATE execution_group_coordinators SET generation=?, active=0 "
                    "WHERE authority_key=?",
                    (generation, authority_key),
                )
                risk_fingerprint = row["risk_scope_fingerprint"]
        return CoordinatorGeneration(
            context.principal_ref,
            context.registration_id,
            generation,
            context.scope.portfolio_ref,
            execution_scope.account_ref,
            execution_scope.strategy_id,
            execution_scope.key,
            risk_fingerprint,
            False,
        )

    def reconcile_hold(self, hold_id: str, proof: object) -> None:
        """Release one orphan hold only after trusted durable-state proof."""

        owner = self._owner
        if type(hold_id) is not str or not hold_id:
            raise GroupAuthorityReconciliationRejected("hold id is invalid")
        with owner._operation():
            with owner._state_lock:
                if hold_id in owner._active_hold_ids:
                    raise GroupAuthorityBusyError("live dispatch hold cannot be reconciled")
            with owner._connection() as connection:
                row = connection.execute(
                    "SELECT hold_json FROM group_dispatch_holds WHERE hold_id=?", (hold_id,)
                ).fetchone()
            if row is None:
                raise GroupAuthorityReconciliationRejected("dispatch hold does not exist")
            hold = _hold_from_json(row["hold_json"])
            with owner._key_lock(hold.authority_key):
                with owner._state_lock:
                    if hold.hold_id in owner._active_hold_ids:
                        raise GroupAuthorityBusyError("live dispatch hold cannot be reconciled")
                if owner._verifier is None:
                    raise GroupAuthorityReconciliationRejected(
                        "no reconciliation verifier is configured"
                    )
                try:
                    verified = owner._verifier(hold, proof)
                except Exception as error:
                    raise GroupAuthorityReconciliationRejected(
                        "reconciliation verifier failed"
                    ) from error
                if verified is not True:
                    raise GroupAuthorityReconciliationRejected(
                        "execution outcome is not proven safe to release"
                    )
                owner._release_hold(hold)
                owner._mark_hold_released(hold.hold_id)


class ExecutionGroupAuthorityGuard:
    """Execution protocol implementation given to the order-entry service."""

    def __init__(self, owner: ExecutionGroupAuthority) -> None:
        self._owner = owner

    @contextmanager
    def bind_service_call(
        self,
        context: ServiceCallContext,
        *,
        execution_scope: ExecutionScope,
    ) -> Iterator[None]:
        """Bind the actual Registry context around one trusted SDK handler call.

        The execution package's protocol does not carry registration_id.  The
        SDK adapter must call this with its handler's trusted context before
        invoking ``submit_group_leg``; the RPC payload must never supply it.
        """

        self._owner._validate_registered_scope(context, execution_scope)
        if self._owner._call_context.get() is not None:
            raise GroupCoordinatorDenied("nested coordinator service call context")
        token = self._owner._call_context.set(context)
        try:
            yield
        finally:
            self._owner._call_context.reset(token)

    @contextmanager
    def hold_group_leg_authorization(
        self,
        *,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        execution_scope: ExecutionScope,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        writer_lease: WriterLease,
    ) -> Iterator[GroupLegAuthorization]:
        owner = self._owner
        with owner._operation():
            context = owner._call_context.get()
            if context is None:
                raise GroupCoordinatorDenied("trusted ServiceCallContext is not bound")
            owner._validate_registered_scope(context, execution_scope)
            if (
                type(identity) is not WireIdentity
                or type(wire_scope) is not WireScope
                or type(binding) is not GroupPermitBinding
                or type(intent) is not OrderIntent
                or type(writer_lease) is not WriterLease
                or identity != context.identity
                or not _wire_scope_matches_registration(context.scope, wire_scope)
                or principal_ref != context.principal_ref
                or principal_ref != binding.coordinator_principal_ref
                or execution_scope.account_ref != binding.account_ref
                or execution_scope.strategy_id != binding.strategy_id
                or execution_scope.account_ref != wire_scope.account_ref
                or execution_scope.strategy_id != wire_scope.strategy_ref
                or wire_scope.portfolio_ref != binding.portfolio_ref
                or wire_scope.group_id != binding.group_id
                or wire_scope.leg_id != binding.leg_id
                or intent.scope != execution_scope
                # The normal Execution writer lease is account-scoped, while
                # the intent journal row is strategy/day scoped. The service
                # verifies this exact lease with ``store.assert_writer_lease``
                # before entry and again before dispatch; bind the authority
                # hold to the lease's actual account scope here.
                or writer_lease.scope_key != execution_scope.account_key
            ):
                raise GroupCoordinatorDenied("group request does not match trusted registration")
            try:
                binding.validate_order(intent)
                lease_fingerprint = writer_lease_fingerprint(writer_lease)
            except (TypeError, ValueError) as error:
                raise GroupCoordinatorDenied("invalid group binding or execution lease") from error
            authority_key = owner._authority_key(context)
            with owner._key_lock(authority_key):
                replay_inspector = getattr(
                    owner._completion_verifier, "inspect_terminal_replay", None
                )
                if callable(replay_inspector):
                    generation = self._current_active_generation(
                        authority_key=authority_key,
                        context=context,
                        execution_scope=execution_scope,
                        binding=binding,
                    )
                    try:
                        replay = replay_inspector(
                            intent=intent,
                            binding=binding,
                            execution_scope=execution_scope,
                            principal_ref=context.principal_ref,
                            writer_lease=writer_lease,
                        )
                    except Exception as error:
                        raise GroupAuthorityUnavailableError(
                            "existing execution result could not be inspected"
                        ) from error
                    if replay is False:
                        raise GroupAuthorityBusyError(
                            "an existing group intent lacks verified terminal replay evidence"
                        )
                    if replay is True:
                        authorization = GroupLegAuthorization(
                            # This ephemeral receipt is deliberately not added
                            # to the store's immutable attempt history. The
                            # facade returns the known row before Risk or
                            # provider admission, so no new dispatch authority
                            # is exercised.
                            authorization_attempt_id=uuid.uuid4().hex,
                            principal_ref=context.principal_ref,
                            portfolio_ref=binding.portfolio_ref,
                            strategy_id=binding.strategy_id,
                            account_ref=binding.account_ref,
                            group_id=binding.group_id,
                            leg_id=binding.leg_id,
                            coordinator_epoch=generation,
                            group_fingerprint=binding.group_fingerprint,
                            binding_fingerprint=binding.fingerprint,
                            deadline_ns=binding.deadline_ns,
                            account_writer_epoch=writer_lease.fencing_token,
                            risk_scope_fingerprint=binding.risk_scope_fingerprint,
                            execution_writer_lease_fingerprint=lease_fingerprint,
                            action="orders.submit_group_leg",
                        )
                        try:
                            yield authorization
                        except BaseException:
                            raise
                        else:
                            try:
                                current_generation = self._current_active_generation(
                                    authority_key=authority_key,
                                    context=context,
                                    execution_scope=execution_scope,
                                    binding=binding,
                                )
                                still_terminal = replay_inspector(
                                    intent=intent,
                                    binding=binding,
                                    execution_scope=execution_scope,
                                    principal_ref=context.principal_ref,
                                    writer_lease=writer_lease,
                                )
                            except Exception as error:
                                raise GroupAuthorityUnavailableError(
                                    "terminal replay changed during idempotent return"
                                ) from error
                            if current_generation != generation or still_terminal is not True:
                                raise GroupAuthorityUnavailableError(
                                    "terminal replay is no longer proven under the active owner"
                                )
                        return

                now_ns = owner._monotonic_clock_ns()
                if type(now_ns) is not int or now_ns < 0 or binding.deadline_ns <= now_ns:
                    raise GroupCoordinatorDenied("group authorization deadline has expired")
                attempt_id = uuid.uuid4().hex
                hold = self._create_hold(
                    authority_key=authority_key,
                    context=context,
                    execution_scope=execution_scope,
                    intent=intent,
                    binding=binding,
                    writer_lease=writer_lease,
                    lease_fingerprint=lease_fingerprint,
                    authorization_attempt_id=attempt_id,
                )
                try:
                    authorization = GroupLegAuthorization(
                        # The durable hold id is also the exact attempt id. It
                        # therefore survives a process crash without needing a
                        # schema-independent in-memory receipt.
                        authorization_attempt_id=hold.hold_id,
                        principal_ref=context.principal_ref,
                        portfolio_ref=binding.portfolio_ref,
                        strategy_id=binding.strategy_id,
                        account_ref=binding.account_ref,
                        group_id=binding.group_id,
                        leg_id=binding.leg_id,
                        coordinator_epoch=hold.coordinator_generation,
                        group_fingerprint=binding.group_fingerprint,
                        binding_fingerprint=binding.fingerprint,
                        deadline_ns=binding.deadline_ns,
                        # This is deliberately the actual account/strategy writer
                        # lease fence. It is not the coordinator generation.
                        account_writer_epoch=writer_lease.fencing_token,
                        risk_scope_fingerprint=hold.risk_scope_fingerprint,
                        execution_writer_lease_fingerprint=lease_fingerprint,
                        action="orders.submit_group_leg",
                    )
                except BaseException:
                    owner._mark_hold_unresolved(hold.hold_id)
                    raise
                try:
                    yield authorization
                except BaseException:
                    # Any exception after the durable claim may follow a
                    # provider side effect. Preserve the hold until a trusted
                    # verifier proves the exact attempt terminal and safe.
                    owner._mark_hold_unresolved(hold.hold_id)
                    raise
                else:
                    try:
                        verified = (
                            owner._completion_verifier is not None
                            and owner._completion_verifier(hold) is True
                        )
                    except BaseException as error:
                        owner._mark_hold_unresolved(hold.hold_id)
                        if not isinstance(error, Exception):
                            raise
                        raise GroupAuthorityUnavailableError(
                            "execution result could not be verified"
                        ) from error
                    if not verified:
                        owner._mark_hold_unresolved(hold.hold_id)
                        raise GroupAuthorityUnavailableError(
                            "normal exit lacks exact durable completion evidence"
                        )
                    try:
                        owner._release_hold(hold)
                    except BaseException:
                        owner._mark_hold_unresolved(hold.hold_id)
                        raise
                    else:
                        owner._mark_hold_released(hold.hold_id)

    def _current_active_generation(
        self,
        *,
        authority_key: str,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        binding: GroupPermitBinding,
    ) -> int:
        owner = self._owner
        with owner._transaction() as connection:
            row = owner._stored_coordinator(connection, authority_key)
            if (
                row is None
                or not row["active"]
                or row["registration_id"] != context.registration_id
                or row["principal_ref"] != context.principal_ref
                or row["identity_json"] != _json(_identity_payload(context.identity))
                or row["wire_scope_json"] != _json(_wire_scope_payload(context.scope))
                or row["execution_scope_key"] != execution_scope.key
                or row["risk_scope_fingerprint"] != binding.risk_scope_fingerprint
                or row["portfolio_ref"] != binding.portfolio_ref
                or row["account_ref"] != binding.account_ref
                or row["strategy_id"] != binding.strategy_id
            ):
                raise GroupCoordinatorDenied("coordinator registration is stale or revoked")
            if owner._has_holds(connection, authority_key):
                raise GroupAuthorityBusyError("an unresolved group dispatch hold blocks replay")
            return int(row["generation"])

    def _create_hold(
        self,
        *,
        authority_key: str,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        writer_lease: WriterLease,
        lease_fingerprint: str,
        authorization_attempt_id: str,
    ) -> GroupDispatchHold:
        hold = GroupDispatchHold(
            hold_id=authorization_attempt_id,
            authority_key=authority_key,
            principal_ref=context.principal_ref,
            registration_id=context.registration_id,
            coordinator_generation=0,
            portfolio_ref=binding.portfolio_ref,
            account_ref=binding.account_ref,
            strategy_id=binding.strategy_id,
            execution_scope_key=execution_scope.key,
            intent_id=intent.intent_id,
            intent_fingerprint=intent.fingerprint,
            binding_fingerprint=binding.fingerprint,
            risk_scope_fingerprint=binding.risk_scope_fingerprint,
            execution_writer_lease_fingerprint=lease_fingerprint,
            account_writer_epoch=writer_lease.fencing_token,
        )
        with self._owner._transaction() as connection:
            row = self._owner._stored_coordinator(connection, authority_key)
            if (
                row is None
                or not row["active"]
                or row["registration_id"] != context.registration_id
                or row["principal_ref"] != context.principal_ref
                or row["identity_json"] != _json(_identity_payload(context.identity))
                or row["wire_scope_json"] != _json(_wire_scope_payload(context.scope))
                or row["execution_scope_key"] != execution_scope.key
                or row["risk_scope_fingerprint"] != binding.risk_scope_fingerprint
                or row["portfolio_ref"] != binding.portfolio_ref
                or row["account_ref"] != binding.account_ref
                or row["strategy_id"] != binding.strategy_id
            ):
                raise GroupCoordinatorDenied("coordinator registration is stale or revoked")
            if self._owner._has_holds(connection, authority_key):
                raise GroupAuthorityBusyError("an unresolved group dispatch hold blocks retry")
            hold = replace(hold, coordinator_generation=int(row["generation"]))
            connection.execute(
                "INSERT INTO group_dispatch_holds(hold_id, authority_key, hold_json) "
                "VALUES(?, ?, ?)",
                (hold.hold_id, authority_key, _json(_hold_payload(hold))),
            )
        self._owner._mark_hold_active(hold.hold_id)
        return hold


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identity_payload(identity: WireIdentity) -> dict[str, object]:
    return {
        "role": identity.role.value,
        "principal_ref": identity.principal_ref,
        "actor_ref": identity.actor_ref,
        "delegate_ref": identity.delegate_ref,
    }


def _wire_scope_payload(scope: WireScope) -> dict[str, object]:
    return {
        "execution_domain_ref": scope.execution_domain_ref,
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "strategy_ref": scope.strategy_ref,
        "run_id": scope.run_id,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
    }


def _wire_scope_matches_registration(
    registered_scope: WireScope,
    operation_scope: WireScope,
) -> bool:
    """Match a per-leg scope to the stable Registry registration scope.

    Only ``group_id`` and ``leg_id`` may be added for an operation. The
    coordinator service adapter constructs this child scope from the trusted
    permit/reservation record; all identity, account, portfolio, execution
    domain, run, and manual-book fields must remain the exact registered
    values. Registrations that already pin a group or leg are rejected by
    ``_validate_registered_scope`` instead of being silently reinterpreted.
    """

    if (
        type(registered_scope) is not WireScope
        or type(operation_scope) is not WireScope
        or registered_scope.group_id is not None
        or registered_scope.leg_id is not None
        or not operation_scope.group_id
        or not operation_scope.leg_id
    ):
        return False
    return replace(operation_scope, group_id=None, leg_id=None) == registered_scope


def _hold_payload(hold: GroupDispatchHold) -> dict[str, object]:
    return {
        "hold_id": hold.hold_id,
        "authority_key": hold.authority_key,
        "principal_ref": hold.principal_ref,
        "registration_id": hold.registration_id,
        "coordinator_generation": hold.coordinator_generation,
        "portfolio_ref": hold.portfolio_ref,
        "account_ref": hold.account_ref,
        "strategy_id": hold.strategy_id,
        "execution_scope_key": hold.execution_scope_key,
        "intent_id": hold.intent_id,
        "intent_fingerprint": hold.intent_fingerprint,
        "binding_fingerprint": hold.binding_fingerprint,
        "risk_scope_fingerprint": hold.risk_scope_fingerprint,
        "execution_writer_lease_fingerprint": hold.execution_writer_lease_fingerprint,
        "account_writer_epoch": hold.account_writer_epoch,
    }


def _hold_from_json(payload: str) -> GroupDispatchHold:
    value = json.loads(payload)
    if type(value) is not dict or set(value) != set(_hold_payload_placeholder()):
        raise GroupAuthorityUnavailable("persisted dispatch hold is malformed")
    try:
        return GroupDispatchHold(**value)
    except (TypeError, ValueError) as error:
        raise GroupAuthorityUnavailable("persisted dispatch hold is malformed") from error


def _hold_payload_placeholder() -> dict[str, object]:
    # Keep schema-field comparison independent of a fabricated dataclass row.
    return {
        "hold_id": "",
        "authority_key": "",
        "principal_ref": "",
        "registration_id": "",
        "coordinator_generation": 1,
        "portfolio_ref": "",
        "account_ref": "",
        "strategy_id": "",
        "execution_scope_key": "",
        "intent_id": "",
        "intent_fingerprint": "",
        "binding_fingerprint": "",
        "risk_scope_fingerprint": "",
        "execution_writer_lease_fingerprint": "",
        "account_writer_epoch": 1,
    }


__all__ = [
    "CoordinatorGeneration",
    "ExecutionGroupAuthority",
    "ExecutionGroupAuthorityControl",
    "ExecutionGroupAuthorityError",
    "ExecutionGroupAuthorityGuard",
    "ExecutionStoreGroupCompletionVerifier",
    "GroupDispatchCompletionVerifier",
    "GroupAuthorityBusy",
    "GroupAuthorityClosed",
    "GroupAuthorityReconciliationRejected",
    "GroupAuthorityUnavailable",
    "GroupCoordinatorDenied",
    "GroupDispatchHold",
    "GroupDispatchReconciliationVerifier",
]
