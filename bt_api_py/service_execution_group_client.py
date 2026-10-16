"""Trusted account-owner persistence for accepted Execution group reservations.

The group coordinator and each account owner keep independent journals. This
module provides the account owner's local binding between an authenticated
coordinator request and the exact Risk reservation receipt. It never accepts
run authority from an unauthenticated scope field and it never calls Risk or a
provider while holding a SQLite transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_execution.contracts import OrderIntent, order_intent_from_payload
from bt_api_execution.group_contracts import group_leg_intent_id
from bt_api_execution.group_order_contracts import (
    GroupAccountReservationSnapshot,
    GroupLegPermitReference,
    GroupOrderIntent,
    GroupReservationPhase,
)

from .service_registry import ActiveRegistrationContext

_SCHEMA_VERSION = 2
_RESERVATION_PORT = "service.group_reservations"
_RESERVATION_OPS = frozenset({"reservations.reserve", "reservations.query", "reservations.release"})
_ACCOUNT_GROUP_PORTS = {
    "orders.submit_group_leg": ("service.order_entry", True),
    "orders.cancel_group_leg": ("service.order_entry", True),
    "orders.query_group_leg": ("service.order_query", False),
    "orders.query_group_cancel": ("service.order_query", False),
    "orders.resolve_group_leg": ("service.order_entry", True),
    "orders.prepare_group_compensation_close": ("service.order_query", False),
    "orders.query_group_compensation_close": ("service.order_query", False),
    "orders.submit_group_compensation_close": ("service.order_entry", True),
}
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AcceptedGroupBindingError(RuntimeError):
    """An accepted-group request or durable reservation binding is invalid."""

    code = "GROUP_BINDING_UNAVAILABLE"


class AcceptedGroupConflictError(AcceptedGroupBindingError):
    """The same stable group key was reused with different trusted facts."""

    code = "GROUP_BINDING_CONFLICT"


@dataclass(frozen=True, slots=True)
class AcceptedGroupBinding:
    """Immutable local binding for one account's share of an accepted group."""

    identity: Mapping[str, str | None]
    principal_ref: str
    first_registration_id: str
    instance_id: str
    scope_ref: str
    service_epoch: str
    policy_version: str
    requirements_version: str
    stable_scope: WireScope
    origin_run_id: str
    group_id: str
    deadline_ns: int
    members: tuple[tuple[str, OrderIntent], ...]
    request_fingerprint: str
    reservation: GroupAccountReservationSnapshot | None

    def __post_init__(self) -> None:
        if type(self.identity) is not MappingProxyType and not isinstance(self.identity, Mapping):
            raise AcceptedGroupBindingError("invalid accepted group identity")
        identity = dict(self.identity)
        if set(identity) != {"role", "principal_ref", "actor_ref", "delegate_ref"}:
            raise AcceptedGroupBindingError("invalid accepted group identity")
        object.__setattr__(self, "identity", MappingProxyType(identity))
        for name in (
            "principal_ref",
            "first_registration_id",
            "instance_id",
            "scope_ref",
            "service_epoch",
            "policy_version",
            "requirements_version",
            "origin_run_id",
            "group_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or _REF.fullmatch(value) is None:
                raise AcceptedGroupBindingError("invalid accepted group binding")
        if type(self.stable_scope) is not WireScope:
            raise AcceptedGroupBindingError("invalid accepted group scope")
        if type(self.deadline_ns) is not int or self.deadline_ns <= 0:
            raise AcceptedGroupBindingError("invalid accepted group deadline")
        if type(self.members) is not tuple or not 1 <= len(self.members) <= 3:
            raise AcceptedGroupBindingError("invalid accepted group member vector")
        if any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not OrderIntent
            for item in self.members
        ):
            raise AcceptedGroupBindingError("invalid accepted group member vector")
        if len({leg_id for leg_id, _ in self.members}) != len(self.members):
            raise AcceptedGroupBindingError("duplicate accepted group leg")
        if (
            type(self.request_fingerprint) is not str
            or len(self.request_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.request_fingerprint)
        ):
            raise AcceptedGroupBindingError("invalid accepted group fingerprint")
        if (
            self.reservation is not None
            and type(self.reservation) is not GroupAccountReservationSnapshot
        ):
            raise AcceptedGroupBindingError("invalid accepted reservation receipt")

    @property
    def account_ref(self) -> str:
        return self.stable_scope.account_ref

    @property
    def portfolio_ref(self) -> str:
        return self.stable_scope.portfolio_ref

    @property
    def strategy_id(self) -> str:
        return self.stable_scope.strategy_ref

    @property
    def phase(self) -> str:
        if self.reservation is None:
            return "PENDING"
        return self.reservation.phase.value

    def permit_for(self, permit: GroupLegPermitReference, intent: OrderIntent) -> OrderIntent:
        """Validate one child against this exact durable reservation.

        A group coordinator may size a child below the reserved maximum (for
        example, the hedge leg after observing the first fill). The accepted
        member therefore anchors every business field except quantity; the
        Risk claim adapter independently maps and bounds that exact quantity.
        """

        if (
            type(permit) is not GroupLegPermitReference
            or type(intent) is not OrderIntent
            or self.reservation is None
        ):
            raise AcceptedGroupBindingError("accepted group permit is unavailable")
        if (
            permit.group_id != self.group_id
            or permit.portfolio_ref != self.portfolio_ref
            or permit.strategy_id != self.strategy_id
            or permit.account_ref != self.account_ref
            or permit.coordinator_principal_ref != self.principal_ref
            or permit.deadline_ns != self.deadline_ns
        ):
            raise AcceptedGroupBindingError("accepted group permit scope mismatch")
        matches = [(leg_id, intent) for leg_id, intent in self.members if leg_id == permit.leg_id]
        if len(matches) != 1:
            raise AcceptedGroupBindingError("accepted group leg is not reserved")
        leg_id, reserved_intent = matches[0]
        if (
            permit.intent_id != reserved_intent.intent_id
            or permit.order_intent_hash != intent.fingerprint
            or intent.quantity > reserved_intent.quantity
        ):
            raise AcceptedGroupBindingError("accepted group permit intent mismatch")
        try:
            expected_intent = replace(reserved_intent, quantity=intent.quantity)
        except (TypeError, ValueError) as error:
            raise AcceptedGroupBindingError("invalid accepted group child quantity") from error
        if expected_intent != intent:
            raise AcceptedGroupBindingError("group child changed its reserved order terms")
        legs = [item for item in self.reservation.legs if item.leg_id == leg_id]
        if len(legs) != 1:
            raise AcceptedGroupBindingError("accepted Risk leg is missing")
        risk_leg = legs[0]
        if (
            self.reservation.group_id != self.group_id
            or self.reservation.account_ref != self.account_ref
            or self.reservation.scope_fingerprint != permit.risk_scope_fingerprint
            or self.reservation.group_fingerprint != permit.group_fingerprint
            or self.reservation.policy_fingerprint != permit.policy_fingerprint
            or risk_leg.permit_id != permit.permit_id
            or risk_leg.intent_id != permit.intent_id
            or risk_leg.reserved_intent_hash != permit.reserved_intent_hash
            or risk_leg.exposure_profile_fingerprint != permit.exposure_profile_fingerprint
        ):
            raise AcceptedGroupBindingError("Risk permit does not match accepted reservation")
        return intent


class AcceptedGroupBindingStore:
    """Local SQLite acceptance journal shared by Risk and Execution adapters.

    A PENDING row is committed before the Risk reservation call. If a process
    loses the reply, the next exact query can reconcile against Risk's durable
    receipt. Rows are never removed or adopted from an unbound Risk group.
    """

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms <= 0:
            raise AcceptedGroupBindingError("invalid accepted group store timeout")
        self._path = str(path)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                self._path,
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._create_schema()
        except (OSError, sqlite3.Error) as error:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            raise AcceptedGroupBindingError("accepted group store unavailable") from error
        self._closed = False

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS accepted_group_meta ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS accepted_group_bindings ("
                    "account_ref TEXT NOT NULL, portfolio_ref TEXT NOT NULL, "
                    "strategy_id TEXT NOT NULL, group_id TEXT NOT NULL, "
                    "request_fingerprint TEXT NOT NULL, binding_json TEXT NOT NULL, "
                    "updated_at_ns INTEGER NOT NULL, "
                    "PRIMARY KEY(account_ref, portfolio_ref, strategy_id, group_id))"
                )
                row = self._connection.execute(
                    "SELECT value FROM accepted_group_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO accepted_group_meta(key, value) VALUES('schema_version', ?)",
                        (str(_SCHEMA_VERSION),),
                    )
                elif row["value"] == "1":
                    self._migrate_v1_to_v2()
                    self._connection.execute(
                        "UPDATE accepted_group_meta SET value=? WHERE key='schema_version'",
                        (str(_SCHEMA_VERSION),),
                    )
                elif row["value"] != str(_SCHEMA_VERSION):
                    raise AcceptedGroupBindingError("unsupported accepted group schema")
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _migrate_v1_to_v2(self) -> None:
        """Drop the transient owner epoch from the stable group fingerprint.

        Registry service epochs rotate on a normal account-owner restart. They
        remain in each row as audit provenance, while the accepted business
        binding remains pinned to identity, stable scope, policy versions,
        logical strategy run, group and member vector.
        """

        rows = self._connection.execute(
            "SELECT account_ref, portfolio_ref, strategy_id, group_id, binding_json "
            "FROM accepted_group_bindings"
        ).fetchall()
        for row in rows:
            binding = _binding_from_json(row["binding_json"], validate_fingerprint=False)
            fingerprint = _digest(_canonical(_request_fingerprint_payload(binding)))
            migrated = replace(binding, request_fingerprint=fingerprint)
            self._connection.execute(
                "UPDATE accepted_group_bindings SET request_fingerprint=?, binding_json=? "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                (
                    fingerprint,
                    _canonical(_binding_payload(migrated)),
                    row["account_ref"],
                    row["portfolio_ref"],
                    row["strategy_id"],
                    row["group_id"],
                ),
            )

    def begin_reservation(
        self,
        registration: ActiveRegistrationContext,
        *,
        origin_run_id: str,
        group_id: str,
        deadline_ns: int,
        members: Sequence[Mapping[str, object]],
    ) -> AcceptedGroupBinding:
        """Persist the exact authenticated request before the Risk side effect."""

        checked = _validate_registration(registration, operation="reservations.reserve")
        if type(origin_run_id) is not str or origin_run_id != checked.strategy_write_run_id:
            raise AcceptedGroupBindingError("coordinator run is not the current logical run")
        if type(group_id) is not str or _REF.fullmatch(group_id) is None:
            raise AcceptedGroupBindingError("invalid group identity")
        if type(deadline_ns) is not int or deadline_ns <= 0:
            raise AcceptedGroupBindingError("invalid group deadline")
        normalized_members = _normalize_members(
            members,
            group_id=group_id,
            scope=checked.context.scope,
            deadline_ns=deadline_ns,
        )
        base = _binding_base(
            registration=checked,
            origin_run_id=origin_run_id,
            group_id=group_id,
            deadline_ns=deadline_ns,
            members=normalized_members,
        )
        # Registration IDs identify renewable leases, not the accepted group.
        # Keep the first one as audit provenance while allowing an exact replay
        # under a fresh registration of the same trusted service principal.
        request_fingerprint = _digest(
            _canonical(
                {
                    key: value
                    for key, value in base.items()
                    if key not in {"registration_id", "service_epoch"}
                }
            )
        )
        binding = AcceptedGroupBinding(
            identity=MappingProxyType(base["identity"]),
            principal_ref=base["principal_ref"],
            first_registration_id=checked.context.registration_id,
            instance_id=base["instance_id"],
            scope_ref=base["scope_ref"],
            service_epoch=base["service_epoch"],
            policy_version=base["policy_version"],
            requirements_version=base["requirements_version"],
            stable_scope=checked.context.scope,
            origin_run_id=origin_run_id,
            group_id=group_id,
            deadline_ns=deadline_ns,
            members=normalized_members,
            request_fingerprint=request_fingerprint,
            reservation=None,
        )
        now_ns = time.time_ns()
        key = _binding_key(binding)
        body = _binding_payload(binding)
        with self._transaction():
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM accepted_group_bindings "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                key,
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO accepted_group_bindings VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (*key, request_fingerprint, _canonical(body), now_ns),
                )
            else:
                stored = _binding_from_json(row["binding_json"])
                if row["request_fingerprint"] != request_fingerprint or not _same_origin_binding(
                    stored, binding
                ):
                    raise AcceptedGroupConflictError("group identity was reused with new facts")
                binding = stored
        return binding

    def complete_reservation(
        self,
        binding: AcceptedGroupBinding,
        reservation: GroupAccountReservationSnapshot,
    ) -> AcceptedGroupBinding:
        """Bind the exact durable Risk receipt to a previously pending row."""

        if (
            type(binding) is not AcceptedGroupBinding
            or type(reservation) is not GroupAccountReservationSnapshot
        ):
            raise AcceptedGroupBindingError("typed accepted group receipt is required")
        _validate_reservation_matches(binding, reservation)
        key = _binding_key(binding)
        with self._transaction():
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM accepted_group_bindings "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                key,
            ).fetchone()
            if row is None or row["request_fingerprint"] != binding.request_fingerprint:
                raise AcceptedGroupBindingError("accepted group reservation has no pending intent")
            current = _binding_from_json(row["binding_json"])
            if not _same_origin_binding(current, binding):
                raise AcceptedGroupConflictError("accepted group binding changed")
            if current.reservation is not None:
                if current.reservation != reservation:
                    raise AcceptedGroupConflictError("Risk reservation receipt changed")
                return current
            updated = _replace_binding(current, reservation=reservation)
            self._connection.execute(
                "UPDATE accepted_group_bindings SET binding_json=?, updated_at_ns=? "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                (_canonical(_binding_payload(updated)), time.time_ns(), *key),
            )
        return updated

    def record_reservation_snapshot(
        self,
        registration: ActiveRegistrationContext,
        *,
        origin_run_id: str,
        group_id: str,
        reservation: GroupAccountReservationSnapshot,
        operation: str = "reservations.query",
    ) -> AcceptedGroupBinding:
        """Persist a reserve/release result for an already-bound group."""

        if operation not in {"reservations.reserve", "reservations.release"}:
            raise AcceptedGroupBindingError(
                "only reserve and release effects may update the accepted snapshot"
            )

        current = self.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            operation=operation,
        )
        if current is None:
            raise AcceptedGroupBindingError("no accepted group binding exists")
        if type(reservation) is not GroupAccountReservationSnapshot:
            raise AcceptedGroupBindingError("typed Risk reservation receipt is required")
        _validate_reservation_matches(current, reservation)
        if current.reservation is not None:
            _validate_reservation_transition(current.reservation, reservation)
        key = _binding_key(current)
        updated = _replace_binding(current, reservation=reservation)
        with self._transaction():
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM accepted_group_bindings "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                key,
            ).fetchone()
            if row is None or row["request_fingerprint"] != current.request_fingerprint:
                raise AcceptedGroupBindingError("accepted group binding disappeared")
            latest = _binding_from_json(row["binding_json"])
            if not _same_origin_binding(latest, current):
                raise AcceptedGroupConflictError("accepted group binding changed")
            if latest.reservation is not None:
                _validate_reservation_transition(latest.reservation, reservation)
            self._connection.execute(
                "UPDATE accepted_group_bindings SET binding_json=?, updated_at_ns=? "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                (_canonical(_binding_payload(updated)), time.time_ns(), *key),
            )
        return updated

    def get_binding(
        self,
        registration: ActiveRegistrationContext,
        *,
        origin_run_id: str,
        group_id: str,
        operation: str = "reservations.query",
    ) -> AcceptedGroupBinding | None:
        """Read an existing group only under the exact current trusted context."""

        checked = _validate_registration(registration, operation=operation)
        if type(origin_run_id) is not str or origin_run_id != checked.strategy_write_run_id:
            raise AcceptedGroupBindingError("coordinator run is not the current logical run")
        if type(group_id) is not str or _REF.fullmatch(group_id) is None:
            raise AcceptedGroupBindingError("invalid group identity")
        scope = checked.context.scope
        with self._lock:
            if self._closed:
                raise AcceptedGroupBindingError("accepted group store is closed")
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM accepted_group_bindings "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? AND group_id=?",
                (scope.account_ref, scope.portfolio_ref, scope.strategy_ref, group_id),
            ).fetchone()
        if row is None:
            return None
        binding = _binding_from_json(row["binding_json"])
        if binding.request_fingerprint != row["request_fingerprint"]:
            raise AcceptedGroupBindingError("accepted group binding is corrupt")
        _validate_binding_owner_context(
            binding,
            checked,
            origin_run_id,
            operation=operation,
        )
        return binding

    def find_binding_by_intent(
        self,
        registration: ActiveRegistrationContext,
        *,
        origin_run_id: str,
        intent_id: str,
        operation: str = "orders.query_group_leg",
    ) -> AcceptedGroupBinding | None:
        """Find one accepted child by exact ID without parsing delimiters.

        Group and leg references may themselves contain colons, so the
        account owner searches only its exact durable owner partition and
        validates each decoded member vector. Ambiguous matches fail closed.
        """

        if operation != "orders.query_group_leg":
            raise AcceptedGroupBindingError(
                "intent lookup is available only on the group-leg query route"
            )
        checked = _validate_registration(registration, operation=operation)
        if type(origin_run_id) is not str or origin_run_id != checked.strategy_write_run_id:
            raise AcceptedGroupBindingError("coordinator run is not the current logical run")
        if type(intent_id) is not str or _REF.fullmatch(intent_id) is None:
            raise AcceptedGroupBindingError("invalid group child identity")
        scope = checked.context.scope
        with self._lock:
            if self._closed:
                raise AcceptedGroupBindingError("accepted group store is closed")
            rows = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM accepted_group_bindings "
                "WHERE account_ref=? AND portfolio_ref=? AND strategy_id=? ORDER BY group_id",
                (scope.account_ref, scope.portfolio_ref, scope.strategy_ref),
            ).fetchall()
        matches: list[AcceptedGroupBinding] = []
        for row in rows:
            binding = _binding_from_json(row["binding_json"])
            if binding.request_fingerprint != row["request_fingerprint"]:
                raise AcceptedGroupBindingError("accepted group binding is corrupt")
            if any(member.intent_id == intent_id for _, member in binding.members):
                _validate_binding_owner_context(
                    binding,
                    checked,
                    origin_run_id,
                    operation=operation,
                )
                matches.append(binding)
        if len(matches) > 1:
            raise AcceptedGroupConflictError("child identity is ambiguous across accepted groups")
        return matches[0] if matches else None

    def close(self) -> None:
        """Close the local database without deleting unresolved accepted groups."""

        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _transaction(self):
        store = self

        class _Txn:
            def __enter__(self):
                store._lock.acquire()
                if store._closed:
                    store._lock.release()
                    raise AcceptedGroupBindingError("accepted group store is closed")
                try:
                    store._connection.execute("BEGIN IMMEDIATE")
                except BaseException:
                    store._lock.release()
                    raise
                return store

            def __exit__(self, exc_type, exc, tb):
                try:
                    store._connection.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()

        return _Txn()


@dataclass(frozen=True, slots=True)
class GroupOriginRunBinding:
    """Coordinator-local immutable binding from an accepted request to its run."""

    request: GroupOrderIntent
    origin_run_id: str
    strategy_principal_ref: str
    strategy_identity: Mapping[str, str | None]
    strategy_scope: WireScope
    coordinator_principal_ref: str
    instance_id: str
    scope_ref: str
    policy_version: str
    requirements_version: str
    registration_id: str
    service_epoch: str
    request_fingerprint: str
    accepted_request_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if type(self.request) is not GroupOrderIntent:
            raise AcceptedGroupBindingError("invalid coordinator group request")
        if type(self.strategy_scope) is not WireScope:
            raise AcceptedGroupBindingError("invalid coordinator registration scope")
        for name in (
            "origin_run_id",
            "strategy_principal_ref",
            "coordinator_principal_ref",
            "instance_id",
            "scope_ref",
            "policy_version",
            "requirements_version",
            "registration_id",
            "service_epoch",
        ):
            value = getattr(self, name)
            if type(value) is not str or _REF.fullmatch(value) is None:
                raise AcceptedGroupBindingError("invalid coordinator run binding")
        identity = dict(self.strategy_identity)
        if set(identity) != {"role", "principal_ref", "actor_ref", "delegate_ref"}:
            raise AcceptedGroupBindingError("invalid coordinator strategy identity")
        object.__setattr__(self, "strategy_identity", MappingProxyType(identity))
        if (
            type(self.request_fingerprint) is not str
            or len(self.request_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.request_fingerprint)
            or (
                self.accepted_request_fingerprint is not None
                and (
                    type(self.accepted_request_fingerprint) is not str
                    or len(self.accepted_request_fingerprint) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in self.accepted_request_fingerprint
                    )
                )
            )
            or self.request.fingerprint != self.request_fingerprint
            or self.request.coordinator_principal_ref != self.coordinator_principal_ref
            or self.strategy_scope.run_id != self.origin_run_id
            or self.strategy_scope.portfolio_ref != self.request.portfolio_ref
            or self.strategy_scope.strategy_ref != self.request.strategy_id
            or tuple(self.strategy_scope.account_refs) != self.request.account_refs
        ):
            raise AcceptedGroupBindingError("invalid coordinator request fingerprint")


class GroupOriginRunBindingStore:
    """Durably bind accepted groups to verified strategy registrations locally.

    This is an SDK sidecar because the frozen Execution GroupOrderIntent DTO
    has no run field. The run comes only from a live ServiceRegistry context;
    it is retained with the exact normalized request so startup recovery never
    obtains authority from a later RPC body.
    """

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms <= 0:
            raise AcceptedGroupBindingError("invalid coordinator binding timeout")
        self._path = str(path)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                self._path,
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            # SQLite PRAGMA statements do not accept bound parameters.
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS coordinator_group_run_bindings ("
                "portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, "
                "request_fingerprint TEXT NOT NULL, binding_json TEXT NOT NULL, "
                "updated_at_ns INTEGER NOT NULL, PRIMARY KEY(portfolio_ref, group_id))"
            )
        except (OSError, sqlite3.Error) as error:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            raise AcceptedGroupBindingError("coordinator binding store unavailable") from error
        self._closed = False

    def bind(
        self,
        registration: ActiveRegistrationContext,
        request: GroupOrderIntent,
        *,
        coordinator_principal_ref: str,
    ) -> GroupOriginRunBinding:
        """Insert once or return an exact business replay under current context."""

        checked = _validate_coordinator_registration(registration, request)
        if (
            type(coordinator_principal_ref) is not str
            or _REF.fullmatch(coordinator_principal_ref) is None
            or request.coordinator_principal_ref != coordinator_principal_ref
        ):
            raise AcceptedGroupBindingError("coordinator service identity mismatch")
        run_id = checked.context.scope.run_id
        assert run_id is not None
        binding = GroupOriginRunBinding(
            request=request,
            origin_run_id=run_id,
            strategy_principal_ref=checked.context.principal_ref,
            strategy_identity=MappingProxyType(_identity_payload(checked.context.identity)),
            strategy_scope=checked.context.scope,
            coordinator_principal_ref=coordinator_principal_ref,
            instance_id=checked.instance_id,
            scope_ref=checked.scope_ref,
            policy_version=checked.policy_version,
            requirements_version=checked.requirements_version,
            registration_id=checked.context.registration_id,
            service_epoch=checked.service_epoch,
            request_fingerprint=request.fingerprint,
        )
        key = (request.portfolio_ref, request.group_id)
        payload = _coordinator_binding_payload(binding)
        with self._transaction():
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM coordinator_group_run_bindings "
                "WHERE portfolio_ref=? AND group_id=?",
                key,
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO coordinator_group_run_bindings VALUES(?, ?, ?, ?, ?)",
                    (*key, binding.request_fingerprint, _canonical(payload), time.time_ns()),
                )
                return binding
            stored = _coordinator_binding_from_json(row["binding_json"])
            if (
                stored.request_fingerprint != row["request_fingerprint"]
                or not _same_group_business_intent(stored.request, request)
                or not _same_coordinator_origin(stored, binding)
            ):
                raise AcceptedGroupConflictError("group request or verified run binding changed")
            return stored

    def get(
        self,
        portfolio_ref: str,
        group_id: str,
    ) -> GroupOriginRunBinding | None:
        """Return one persisted run binding for internal owner-thread recovery."""

        if any(
            type(value) is not str or _REF.fullmatch(value) is None
            for value in (portfolio_ref, group_id)
        ):
            raise AcceptedGroupBindingError("invalid coordinator group key")
        with self._lock:
            if self._closed:
                raise AcceptedGroupBindingError("coordinator binding store is closed")
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM coordinator_group_run_bindings "
                "WHERE portfolio_ref=? AND group_id=?",
                (portfolio_ref, group_id),
            ).fetchone()
        if row is None:
            return None
        binding = _coordinator_binding_from_json(row["binding_json"])
        if binding.request_fingerprint != row["request_fingerprint"]:
            raise AcceptedGroupBindingError("coordinator run binding is corrupt")
        return binding

    def find_by_intent(self, intent_id: str) -> GroupOriginRunBinding | None:
        """Locate one persisted group by its exact immutable child ID."""

        if type(intent_id) is not str or _REF.fullmatch(intent_id) is None:
            raise AcceptedGroupBindingError("invalid group child identity")
        with self._lock:
            if self._closed:
                raise AcceptedGroupBindingError("coordinator binding store is closed")
            rows = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM coordinator_group_run_bindings "
                "ORDER BY portfolio_ref, group_id"
            ).fetchall()
        matches: list[GroupOriginRunBinding] = []
        for row in rows:
            binding = _coordinator_binding_from_json(row["binding_json"])
            if binding.request_fingerprint != row["request_fingerprint"]:
                raise AcceptedGroupBindingError("coordinator run binding is corrupt")
            if any(leg.maximum_intent.intent_id == intent_id for leg in binding.request.legs):
                matches.append(binding)
        if len(matches) > 1:
            raise AcceptedGroupConflictError("child identity is ambiguous across accepted groups")
        return matches[0] if matches else None

    def get_for_registration(
        self,
        registration: ActiveRegistrationContext,
        group_id: str,
        *,
        coordinator_principal_ref: str,
        operation: str = "groups.query",
    ) -> GroupOriginRunBinding | None:
        """Read an existing group only under its current authenticated run."""

        if type(registration) is not ActiveRegistrationContext:
            raise AcceptedGroupBindingError("current coordinator registration is required")
        if type(group_id) is not str or _REF.fullmatch(group_id) is None:
            raise AcceptedGroupBindingError("invalid coordinator group key")
        scope = registration.context.scope
        portfolio_ref = scope.portfolio_ref
        if type(portfolio_ref) is not str or _REF.fullmatch(portfolio_ref) is None:
            raise AcceptedGroupBindingError("coordinator portfolio is unavailable")
        binding = self.get(portfolio_ref, group_id)
        if binding is None:
            return None
        if (
            type(coordinator_principal_ref) is not str
            or binding.coordinator_principal_ref != coordinator_principal_ref
        ):
            raise AcceptedGroupBindingError("coordinator service identity changed")
        _validate_coordinator_registration(registration, binding.request, operation=operation)
        current = replace(
            binding,
            strategy_principal_ref=registration.context.principal_ref,
            strategy_identity=MappingProxyType(_identity_payload(registration.context.identity)),
            strategy_scope=registration.context.scope,
            instance_id=registration.instance_id,
            scope_ref=registration.scope_ref,
            policy_version=registration.policy_version,
            requirements_version=registration.requirements_version,
            registration_id=registration.context.registration_id,
            service_epoch=registration.service_epoch,
        )
        if not _same_coordinator_origin(binding, current):
            raise AcceptedGroupConflictError(
                "current registration changed accepted group authority"
            )
        return binding

    def list_all(self) -> tuple[GroupOriginRunBinding, ...]:
        """Read all exact persisted run bindings for owner-thread recovery."""

        with self._lock:
            if self._closed:
                raise AcceptedGroupBindingError("coordinator binding store is closed")
            rows = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM coordinator_group_run_bindings "
                "ORDER BY portfolio_ref, group_id"
            ).fetchall()
        bindings = tuple(_coordinator_binding_from_json(row["binding_json"]) for row in rows)
        if any(
            binding.request_fingerprint != row["request_fingerprint"]
            for binding, row in zip(bindings, rows, strict=True)
        ):
            raise AcceptedGroupBindingError("coordinator run binding is corrupt")
        return bindings

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _transaction(self):
        store = self

        class _Txn:
            def __enter__(self):
                store._lock.acquire()
                if store._closed:
                    store._lock.release()
                    raise AcceptedGroupBindingError("coordinator binding store is closed")
                try:
                    store._connection.execute("BEGIN IMMEDIATE")
                except BaseException:
                    store._lock.release()
                    raise
                return store

            def __exit__(self, exc_type, exc, tb):
                try:
                    store._connection.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()

        return _Txn()



    def bind_accepted_request_fingerprint(
        self,
        binding: GroupOriginRunBinding,
        accepted_request_fingerprint: str,
    ) -> GroupOriginRunBinding:
        """Bind the trusted accepted snapshot fingerprint exactly once.

        ``request_fingerprint`` remains the full hash of the original client
        request.  A trusted group coordinator may add server-owned quote facts
        before persistence; those facts have a separate fingerprint and can
        only be attached after the caller verifies the durable accept event.
        """

        if (
            type(binding) is not GroupOriginRunBinding
            or type(accepted_request_fingerprint) is not str
            or len(accepted_request_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in accepted_request_fingerprint)
        ):
            raise AcceptedGroupBindingError("invalid accepted group fingerprint")
        key = (binding.request.portfolio_ref, binding.request.group_id)
        with self._transaction():
            row = self._connection.execute(
                "SELECT request_fingerprint, binding_json FROM coordinator_group_run_bindings "
                "WHERE portfolio_ref=? AND group_id=?",
                key,
            ).fetchone()
            if row is None:
                raise AcceptedGroupBindingError("coordinator run binding is missing")
            stored = _coordinator_binding_from_json(row["binding_json"])
            if (
                stored.request_fingerprint != row["request_fingerprint"]
                or stored.request_fingerprint != binding.request_fingerprint
                or stored.request != binding.request
                or not _same_coordinator_origin(stored, binding)
            ):
                raise AcceptedGroupConflictError("accepted group origin binding changed")
            if stored.accepted_request_fingerprint is not None:
                if stored.accepted_request_fingerprint != accepted_request_fingerprint:
                    raise AcceptedGroupConflictError("accepted group snapshot fingerprint changed")
                return stored
            updated = replace(
                stored,
                accepted_request_fingerprint=accepted_request_fingerprint,
            )
            self._connection.execute(
                "UPDATE coordinator_group_run_bindings SET binding_json=?, updated_at_ns=? "
                "WHERE portfolio_ref=? AND group_id=? AND request_fingerprint=?",
                (
                    _canonical(_coordinator_binding_payload(updated)),
                    time.time_ns(),
                    *key,
                    stored.request_fingerprint,
                ),
            )
            return updated

def _validate_registration(
    registration: ActiveRegistrationContext, *, operation: str
) -> ActiveRegistrationContext:
    if type(registration) is not ActiveRegistrationContext:
        raise AcceptedGroupBindingError("current owner registration is required")
    context = registration.context
    scope = context.scope
    account_port = _ACCOUNT_GROUP_PORTS.get(operation)
    reservation_operation = operation in _RESERVATION_OPS
    expected_port = (
        _RESERVATION_PORT
        if reservation_operation
        else (None if account_port is None else account_port[0])
    )
    write_required = (
        operation != "reservations.query"
        if reservation_operation
        else (True if account_port is None else account_port[1])
    )
    if (
        expected_port is None
        or registration.port != expected_port
        or not (reservation_operation or account_port is not None)
        or operation not in registration.operations
        or (write_required and operation not in registration.write_operations)
        or type(context.identity.role) is not WireRole
        or context.identity.role is not WireRole.SERVICE
        or context.identity.principal_ref != context.principal_ref
        or type(scope) is not WireScope
        or not scope.account_ref
        or scope.account_refs
        or not scope.portfolio_ref
        or not scope.strategy_ref
        or scope.run_id is not None
        or scope.group_id is not None
        or scope.leg_id is not None
        or scope.manual_book_ref is not None
        or type(registration.strategy_write_run_id) is not str
        or _REF.fullmatch(registration.strategy_write_run_id) is None
        or registration.expires_at <= time.time()
    ):
        raise AcceptedGroupBindingError("owner registration is not eligible for group reservation")
    return registration


def _validate_coordinator_registration(
    registration: ActiveRegistrationContext,
    request: GroupOrderIntent,
    *,
    operation: str = "groups.submit",
) -> ActiveRegistrationContext:
    """Validate the authenticated strategy registration for one group route."""

    if type(registration) is not ActiveRegistrationContext:
        raise AcceptedGroupBindingError("current coordinator registration is required")
    if type(request) is not GroupOrderIntent:
        raise AcceptedGroupBindingError("normalized group request is required")
    context = registration.context
    scope = context.scope
    if (
        operation not in {"groups.submit", "groups.query", "groups.cancel"}
        or registration.port != "service.order_group_entry"
        or operation not in registration.operations
        or (operation in {"groups.submit", "groups.cancel"} and operation not in registration.write_operations)
        or type(context.identity.role) is not WireRole
        or context.identity.role is not WireRole.STRATEGY_CLIENT
        or context.identity.actor_ref != scope.strategy_ref
        or context.identity.principal_ref != context.principal_ref
        or type(scope) is not WireScope
        or scope.execution_domain_ref is None
        or scope.account_ref is not None
        or tuple(scope.account_refs) != request.account_refs
        or scope.strategy_ref != request.strategy_id
        or scope.run_id is None
        or scope.manual_book_ref is not None
        or scope.portfolio_ref != request.portfolio_ref
        or scope.group_id is not None
        or scope.leg_id is not None
        or registration.expires_at <= time.time()
    ):
        raise AcceptedGroupBindingError("coordinator registration is not eligible for group route")
    return registration


def _same_group_business_intent(left: GroupOrderIntent, right: GroupOrderIntent) -> bool:
    """Compare client business facts while ignoring server receive time."""

    left_payload = left.to_payload()
    right_payload = right.to_payload()
    left_payload.pop("received_at_ns", None)
    right_payload.pop("received_at_ns", None)
    return left_payload == right_payload


def _same_coordinator_origin(left: GroupOriginRunBinding, right: GroupOriginRunBinding) -> bool:
    """Permit lease renewal/restart while pinning the logical run and scope."""

    return (
        left.origin_run_id == right.origin_run_id
        and left.strategy_principal_ref == right.strategy_principal_ref
        and left.strategy_identity == right.strategy_identity
        and _stable_scope_payload(left.strategy_scope)
        == _stable_scope_payload(right.strategy_scope)
        and left.coordinator_principal_ref == right.coordinator_principal_ref
        and left.instance_id == right.instance_id
        and left.scope_ref == right.scope_ref
        and left.policy_version == right.policy_version
        and left.requirements_version == right.requirements_version
    )


def verify_accepted_group_projection(
    binding: GroupOriginRunBinding,
    snapshot: GroupOrderSnapshot,
    history: tuple[GroupOrderEvent, ...],
) -> str:
    """Verify the durable accept projection and return its trusted fingerprint.

    ``history`` must come from ``SqliteOrderGroupStore.events`` so its event
    sequence ordering and per-record hashes have already been checked.  That first,
    unique accept event is durable evidence for a server-enriched snapshot if
    the coordinator sidecar crashed before storing ``accepted_request_fingerprint``.
    The original request remains separately bound by its full and unquoted
    fingerprints; callers cannot supply a replacement quote through this API.
    """

    if (
        type(binding) is not GroupOriginRunBinding
        or type(snapshot) is not GroupOrderSnapshot
        or type(history) is not tuple
        or not history
        or len(history) > 100_000
        or len(history) != snapshot.revision + 1
        or any(type(event) is not GroupOrderEvent for event in history)
    ):
        raise AcceptedGroupBindingError("accepted group projection is unavailable")
    request = binding.request
    accepted_request = snapshot.request
    accepted_events = tuple(event for event in history if event.event_type == "GROUP_ACCEPTED")
    expected_leg_ids = [leg.leg_id for leg in accepted_request.legs]
    first = history[0]
    payload = first.payload
    if (
        binding.request_fingerprint != request.fingerprint
        or binding.request.group_id != request.group_id
        or request.group_id != accepted_request.group_id
        or request.portfolio_ref != accepted_request.portfolio_ref
        or request.strategy_id != accepted_request.strategy_id
        or request.coordinator_principal_ref != accepted_request.coordinator_principal_ref
        or request.deadline_ns != accepted_request.deadline_ns
        or request.account_refs != accepted_request.account_refs
        or request.unquoted_fingerprint != accepted_request.unquoted_fingerprint
        or any(type(event.sequence) is not int or event.sequence <= 0 for event in history)
        or any(
            previous.sequence >= current.sequence
            for previous, current in zip(history, history[1:])
        )
        or first.group_id != accepted_request.group_id
        or first.event_type != "GROUP_ACCEPTED"
        or not isinstance(payload, Mapping)
        or len(accepted_events) != 1
        or accepted_events[0] != first
        or any(event.group_id != accepted_request.group_id for event in history)
        or payload.get("request_fingerprint") != accepted_request.fingerprint
        or payload.get("deadline_ns") != accepted_request.deadline_ns
        or payload.get("account_refs") != list(accepted_request.account_refs)
        or payload.get("leg_ids") != expected_leg_ids
        or (
            binding.accepted_request_fingerprint is not None
            and binding.accepted_request_fingerprint != accepted_request.fingerprint
        )
    ):
        raise AcceptedGroupBindingError("accepted group projection does not match its durable event")
    return accepted_request.fingerprint

def _coordinator_binding_payload(binding: GroupOriginRunBinding) -> dict[str, Any]:
    payload = {
        "schema": 1,
        "request": binding.request.to_payload(),
        "origin_run_id": binding.origin_run_id,
        "strategy_principal_ref": binding.strategy_principal_ref,
        "strategy_identity": dict(binding.strategy_identity),
        "strategy_scope": _stable_scope_payload(binding.strategy_scope),
        "coordinator_principal_ref": binding.coordinator_principal_ref,
        "instance_id": binding.instance_id,
        "scope_ref": binding.scope_ref,
        "policy_version": binding.policy_version,
        "requirements_version": binding.requirements_version,
        "registration_id": binding.registration_id,
        "service_epoch": binding.service_epoch,
        "request_fingerprint": binding.request_fingerprint,
    }
    if binding.accepted_request_fingerprint is not None:
        payload["accepted_request_fingerprint"] = binding.accepted_request_fingerprint
    return payload


def _coordinator_binding_from_json(payload: str) -> GroupOriginRunBinding:
    try:
        value = json.loads(payload)
        expected = {
            "schema",
            "request",
            "origin_run_id",
            "strategy_principal_ref",
            "strategy_identity",
            "strategy_scope",
            "coordinator_principal_ref",
            "instance_id",
            "scope_ref",
            "policy_version",
            "requirements_version",
            "registration_id",
            "service_epoch",
            "request_fingerprint",
        }
        accepted_expected = expected | {"accepted_request_fingerprint"}
        if (
            type(value) is not dict
            or frozenset(value) not in {frozenset(expected), frozenset(accepted_expected)}
            or value["schema"] != 1
        ):
            raise ValueError("schema")
        binding = GroupOriginRunBinding(
            request=GroupOrderIntent.from_payload(value["request"]),
            origin_run_id=value["origin_run_id"],
            strategy_principal_ref=value["strategy_principal_ref"],
            strategy_identity=MappingProxyType(value["strategy_identity"]),
            strategy_scope=WireScope(**value["strategy_scope"]),
            coordinator_principal_ref=value["coordinator_principal_ref"],
            instance_id=value["instance_id"],
            scope_ref=value["scope_ref"],
            policy_version=value["policy_version"],
            requirements_version=value["requirements_version"],
            registration_id=value["registration_id"],
            service_epoch=value["service_epoch"],
            request_fingerprint=value["request_fingerprint"],
            accepted_request_fingerprint=value.get("accepted_request_fingerprint"),
        )
        if _canonical(_coordinator_binding_payload(binding)) != payload:
            raise ValueError("canonical")
        return binding
    except Exception as error:
        raise AcceptedGroupBindingError("coordinator run binding row is corrupt") from error


def _normalize_members(
    members: Sequence[Mapping[str, object]],
    *,
    group_id: str,
    scope: WireScope,
    deadline_ns: int,
) -> tuple[tuple[str, OrderIntent], ...]:
    if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
        raise AcceptedGroupBindingError("invalid group member vector")
    parsed: list[tuple[str, OrderIntent]] = []
    for member in members:
        if not isinstance(member, Mapping) or set(member) != {"leg_id", "intent"}:
            raise AcceptedGroupBindingError("invalid group member")
        leg_id = member["leg_id"]
        payload = member["intent"]
        if type(leg_id) is not str or _REF.fullmatch(leg_id) is None:
            raise AcceptedGroupBindingError("invalid group leg identity")
        if not isinstance(payload, Mapping):
            raise AcceptedGroupBindingError("invalid group order intent")
        try:
            intent = order_intent_from_payload(payload)
        except Exception as error:
            raise AcceptedGroupBindingError("invalid group order intent") from error
        if (
            intent.intent_id != group_leg_intent_id(group_id, leg_id)
            or intent.scope.account_ref != scope.account_ref
            or intent.scope.strategy_id != scope.strategy_ref
            or intent.deadline_ns != deadline_ns
        ):
            raise AcceptedGroupBindingError("group member scope or identity mismatch")
        parsed.append((leg_id, intent))
    if not parsed or len(parsed) > 3 or len({leg_id for leg_id, _ in parsed}) != len(parsed):
        raise AcceptedGroupBindingError("invalid group member count")
    return tuple(sorted(parsed, key=lambda item: item[0]))


def _binding_base(
    *,
    registration: ActiveRegistrationContext,
    origin_run_id: str,
    group_id: str,
    deadline_ns: int,
    members: tuple[tuple[str, OrderIntent], ...],
) -> dict[str, Any]:
    context = registration.context
    return {
        "schema": 1,
        "identity": _identity_payload(context.identity),
        "principal_ref": context.principal_ref,
        "instance_id": registration.instance_id,
        "scope_ref": registration.scope_ref,
        "service_epoch": registration.service_epoch,
        "policy_version": registration.policy_version,
        "requirements_version": registration.requirements_version,
        "stable_scope": _stable_scope_payload(context.scope),
        "origin_run_id": origin_run_id,
        "group_id": group_id,
        "deadline_ns": deadline_ns,
        "members": [
            {"leg_id": leg_id, "intent": intent.to_payload()} for leg_id, intent in members
        ],
        "reservation": None,
    }


def _binding_payload(binding: AcceptedGroupBinding) -> dict[str, Any]:
    return {
        "schema": 1,
        "identity": dict(binding.identity),
        "principal_ref": binding.principal_ref,
        "registration_id": binding.first_registration_id,
        "instance_id": binding.instance_id,
        "scope_ref": binding.scope_ref,
        "service_epoch": binding.service_epoch,
        "policy_version": binding.policy_version,
        "requirements_version": binding.requirements_version,
        "stable_scope": _stable_scope_payload(binding.stable_scope),
        "origin_run_id": binding.origin_run_id,
        "group_id": binding.group_id,
        "deadline_ns": binding.deadline_ns,
        "members": [
            {"leg_id": leg_id, "intent": intent.to_payload()} for leg_id, intent in binding.members
        ],
        "request_fingerprint": binding.request_fingerprint,
        "reservation": None if binding.reservation is None else binding.reservation.to_payload(),
    }


def _binding_from_json_impl(payload: str, *, validate_fingerprint: bool) -> AcceptedGroupBinding:
    try:
        value = json.loads(payload)
        expected = {
            "schema",
            "identity",
            "principal_ref",
            "registration_id",
            "instance_id",
            "scope_ref",
            "service_epoch",
            "policy_version",
            "requirements_version",
            "stable_scope",
            "origin_run_id",
            "group_id",
            "deadline_ns",
            "members",
            "request_fingerprint",
            "reservation",
        }
        if type(value) is not dict or set(value) != expected or value["schema"] != 1:
            raise ValueError("schema")
        from bt_api_base.service_protocol import WireScope

        scope = WireScope(**value["stable_scope"])
        members = _normalize_members(
            value["members"],
            group_id=value["group_id"],
            scope=scope,
            deadline_ns=value["deadline_ns"],
        )
        reservation = (
            None
            if value["reservation"] is None
            else GroupAccountReservationSnapshot.from_payload(value["reservation"])
        )
        binding = AcceptedGroupBinding(
            identity=value["identity"],
            principal_ref=value["principal_ref"],
            first_registration_id=value["registration_id"],
            instance_id=value["instance_id"],
            scope_ref=value["scope_ref"],
            service_epoch=value["service_epoch"],
            policy_version=value["policy_version"],
            requirements_version=value["requirements_version"],
            stable_scope=scope,
            origin_run_id=value["origin_run_id"],
            group_id=value["group_id"],
            deadline_ns=value["deadline_ns"],
            members=members,
            request_fingerprint=value["request_fingerprint"],
            reservation=reservation,
        )
        if _binding_from_payload(binding) != payload or (
            validate_fingerprint
            and _digest(_canonical(_request_fingerprint_payload(binding)))
            != binding.request_fingerprint
        ):
            raise ValueError("canonical")
        return binding
    except Exception as error:
        raise AcceptedGroupBindingError("accepted group binding row is corrupt") from error


def _binding_from_json(payload: str, *, validate_fingerprint: bool = True) -> AcceptedGroupBinding:
    return _binding_from_json_impl(payload, validate_fingerprint=validate_fingerprint)


def _binding_from_payload(binding: AcceptedGroupBinding) -> str:
    return _canonical(_binding_payload(binding))


def _request_fingerprint_payload(binding: AcceptedGroupBinding) -> dict[str, Any]:
    return {
        "schema": 1,
        "identity": dict(binding.identity),
        "principal_ref": binding.principal_ref,
        "instance_id": binding.instance_id,
        "scope_ref": binding.scope_ref,
        "policy_version": binding.policy_version,
        "requirements_version": binding.requirements_version,
        "stable_scope": _stable_scope_payload(binding.stable_scope),
        "origin_run_id": binding.origin_run_id,
        "group_id": binding.group_id,
        "deadline_ns": binding.deadline_ns,
        "members": [
            {"leg_id": leg_id, "intent": intent.to_payload()} for leg_id, intent in binding.members
        ],
        "reservation": None,
    }


def _replace_binding(
    binding: AcceptedGroupBinding,
    *,
    reservation: GroupAccountReservationSnapshot,
) -> AcceptedGroupBinding:
    return AcceptedGroupBinding(
        identity=binding.identity,
        principal_ref=binding.principal_ref,
        first_registration_id=binding.first_registration_id,
        instance_id=binding.instance_id,
        scope_ref=binding.scope_ref,
        service_epoch=binding.service_epoch,
        policy_version=binding.policy_version,
        requirements_version=binding.requirements_version,
        stable_scope=binding.stable_scope,
        origin_run_id=binding.origin_run_id,
        group_id=binding.group_id,
        deadline_ns=binding.deadline_ns,
        members=binding.members,
        request_fingerprint=binding.request_fingerprint,
        reservation=reservation,
    )


def _validate_reservation_matches(
    binding: AcceptedGroupBinding, reservation: GroupAccountReservationSnapshot
) -> None:
    expected = dict(binding.members)
    actual = {leg.leg_id: leg for leg in reservation.legs}
    if (
        reservation.group_id != binding.group_id
        or reservation.account_ref != binding.account_ref
        or reservation.deadline_ns != binding.deadline_ns
        or reservation.group_fingerprint is None
        or reservation.policy_fingerprint is None
        or set(actual) != set(expected)
        or any(actual[leg_id].intent_id != intent.intent_id for leg_id, intent in expected.items())
    ):
        raise AcceptedGroupBindingError("Risk reservation does not match accepted group")


def _validate_reservation_transition(
    previous: GroupAccountReservationSnapshot, current: GroupAccountReservationSnapshot
) -> None:
    _validate_reservation_identity(previous, current)
    allowed = {
        GroupReservationPhase.UNKNOWN: {
            GroupReservationPhase.UNKNOWN,
            GroupReservationPhase.ACTIVE,
            GroupReservationPhase.DENIED,
            GroupReservationPhase.RELEASED,
        },
        GroupReservationPhase.ACTIVE: {
            GroupReservationPhase.ACTIVE,
            GroupReservationPhase.RELEASE_REQUESTED,
            GroupReservationPhase.RELEASED,
        },
        GroupReservationPhase.RELEASE_REQUESTED: {
            GroupReservationPhase.RELEASE_REQUESTED,
            GroupReservationPhase.RELEASED,
            GroupReservationPhase.UNKNOWN,
        },
        GroupReservationPhase.RELEASED: {GroupReservationPhase.RELEASED},
        GroupReservationPhase.DENIED: {GroupReservationPhase.DENIED},
    }
    if current.phase not in allowed.get(previous.phase, {previous.phase}):
        raise AcceptedGroupBindingError("Risk reservation phase regressed")
    old_legs = {leg.leg_id: leg for leg in previous.legs}
    new_legs = {leg.leg_id: leg for leg in current.legs}
    leg_transitions = {
        "ACTIVE": {"ACTIVE", "SETTLED", "RELEASED", "EXPIRED", "NO_FILL_TERMINAL"},
        "SETTLED": {"SETTLED", "NO_FILL_TERMINAL"},
        "NO_FILL_TERMINAL": {"NO_FILL_TERMINAL"},
        "RELEASED": {"RELEASED"},
        "EXPIRED": {"EXPIRED"},
    }
    for leg_id, old in old_legs.items():
        new = new_legs[leg_id]
        if (
            old.permit_id != new.permit_id
            or old.intent_id != new.intent_id
            or old.reserved_intent_hash != new.reserved_intent_hash
            or old.exposure_profile_fingerprint != new.exposure_profile_fingerprint
            or old.actual_intent_hash not in (None, new.actual_intent_hash)
            or old.claimed
            and not new.claimed
            or old.resolved
            and not new.resolved
            or new.state not in leg_transitions.get(old.state, set())
        ):
            raise AcceptedGroupBindingError("Risk leg state regressed or identity changed")


def validate_group_reservation_transition(
    previous: GroupAccountReservationSnapshot,
    current: GroupAccountReservationSnapshot,
) -> None:
    """Validate a current reservation against its accepted group snapshot.

    This typed public entry point deliberately delegates to the account
    binding's single transition algorithm so other durable group projections
    can apply the same identity and monotonicity rules without copying them.
    """

    if (
        type(previous) is not GroupAccountReservationSnapshot
        or type(current) is not GroupAccountReservationSnapshot
    ):
        raise AcceptedGroupBindingError("invalid group reservation transition")
    _validate_reservation_transition(previous, current)

def _validate_reservation_identity(
    previous: GroupAccountReservationSnapshot, current: GroupAccountReservationSnapshot
) -> None:
    if (
        previous.group_id != current.group_id
        or previous.account_ref != current.account_ref
        or previous.scope_fingerprint != current.scope_fingerprint
        or previous.group_fingerprint != current.group_fingerprint
        or previous.policy_fingerprint != current.policy_fingerprint
        or previous.deadline_ns != current.deadline_ns
        or tuple(leg.leg_id for leg in previous.legs) != tuple(leg.leg_id for leg in current.legs)
    ):
        raise AcceptedGroupBindingError("Risk reservation identity changed")


def _same_origin_binding(left: AcceptedGroupBinding, right: AcceptedGroupBinding) -> bool:
    return (
        left.identity == right.identity
        and left.principal_ref == right.principal_ref
        and left.instance_id == right.instance_id
        and left.scope_ref == right.scope_ref
        and left.policy_version == right.policy_version
        and left.requirements_version == right.requirements_version
        and left.stable_scope == right.stable_scope
        and left.origin_run_id == right.origin_run_id
        and left.group_id == right.group_id
        and left.deadline_ns == right.deadline_ns
        and left.members == right.members
        and left.request_fingerprint == right.request_fingerprint
    )


def _validate_binding_owner_context(
    binding: AcceptedGroupBinding,
    registration: ActiveRegistrationContext,
    origin_run_id: str,
    *,
    operation: str,
) -> None:
    scope = registration.context.scope
    stable_owner_mismatch = (
        binding.origin_run_id != origin_run_id
        or binding.account_ref != scope.account_ref
        or binding.portfolio_ref != scope.portfolio_ref
        or binding.strategy_id != scope.strategy_ref
        or binding.identity != MappingProxyType(_identity_payload(registration.context.identity))
        or binding.principal_ref != registration.context.principal_ref
        or _stable_scope_payload(binding.stable_scope) != _stable_scope_payload(scope)
    )
    if stable_owner_mismatch:
        raise AcceptedGroupBindingError("accepted group is outside the current owner context")

    if operation in _RESERVATION_OPS:
        if (
            binding.instance_id != registration.instance_id
            or binding.scope_ref != registration.scope_ref
            or binding.policy_version != registration.policy_version
            or binding.requirements_version != registration.requirements_version
        ):
            raise AcceptedGroupBindingError(
                "accepted reservation is outside its originating service route"
            )
    elif operation not in _ACCOUNT_GROUP_PORTS:
        raise AcceptedGroupBindingError("accepted group operation is not authorized")


def _binding_key(binding: AcceptedGroupBinding) -> tuple[str, str, str, str]:
    return (
        binding.account_ref,
        binding.portfolio_ref,
        binding.strategy_id,
        binding.group_id,
    )


def _stable_scope_payload(scope: WireScope) -> dict[str, object]:
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


def _identity_payload(identity: object) -> dict[str, str | None]:
    return {
        "role": getattr(getattr(identity, "role", None), "value", None),
        "principal_ref": getattr(identity, "principal_ref", None),
        "actor_ref": getattr(identity, "actor_ref", None),
        "delegate_ref": getattr(identity, "delegate_ref", None),
    }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "AcceptedGroupBinding",
    "AcceptedGroupBindingError",
    "AcceptedGroupBindingStore",
    "AcceptedGroupConflictError",
    "GroupOriginRunBinding",
    "GroupOriginRunBindingStore",
]
