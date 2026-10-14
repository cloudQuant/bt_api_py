"""Durable, authorization-checked registry for optional SDK services.

The registry consumes only the neutral ``bt_api_base.service_protocol`` DTOs.
Transport authentication supplies the principal key to :meth:`handle`; the
identity and scope inside a wire envelope are claims and are checked against
the immutable trusted grants supplied at construction time.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from bt_api_base.service_protocol import (
    MAX_JSON_DEPTH,
    MAX_REFERENCE_BYTES,
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireIdentity,
    WireRole,
    WireScope,
    WireValidationError,
    validate_request,
)

_CONTROL_SCHEMA = "sdk.service.control"
_CALL_SCHEMA = "sdk.service.call"
_SCHEMA_VERSION = 1
_MAX_SESSIONS = 1024
_MAX_REGISTRATION_KEYS = 16_384
_DEFAULT_REGISTRATION_KEYS = 8_192
_MAX_OPERATIONS_PER_REQUEST = 64
# Keep room for the result wrapper and the transport envelope's header under
# the wire protocol's 1 MiB message limit.
_MAX_RESULT_BYTES = 900 * 1024
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTROL_OPERATIONS = frozenset(
    {"service.discover", "service.register", "service.renew", "service.unregister"}
)
SERVICE_ERROR_CODES = frozenset(
    {
        "CAPACITY",
        "CAPABILITY_UNAVAILABLE",
        "CLOSED",
        "CONFLICT",
        "DENIED",
        "DUPLICATE_ACTIVE_RUN",
        "HANDLER_FAILED",
        "INVALID_REQUEST",
        "NOT_REGISTERED",
        "OK",
        "POLICY_REQUIREMENT_UNMET",
        "SCOPE_DENIED",
        "STALE_EPOCH",
        "UNKNOWN_OPERATION",
        "UNAVAILABLE",
    }
)


def _text(value: Any) -> bool:
    return type(value) is str and bool(_SAFE_REF.fullmatch(value)) and value == value.strip()


def _principal_ref(value: Any) -> bool:
    """Match the transport's bounded printable principal-reference contract."""

    if type(value) is not str or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= MAX_REFERENCE_BYTES and not any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value
    )


def _operation_set(value: Any, *, field_name: str) -> frozenset[str]:
    if type(value) is not frozenset:
        raise TypeError(f"{field_name} must be a frozenset")
    if not value or len(value) > _MAX_OPERATIONS_PER_REQUEST:
        raise ValueError(f"{field_name} has an invalid size")
    if any(not _text(operation) or operation.startswith("service.") for operation in value):
        raise ValueError(f"{field_name} contains an invalid operation")
    return value


@dataclass(frozen=True, slots=True)
class ServiceCapability:
    """Code-declared instance/port capability; never supplied by a peer."""

    instance_id: str
    port: str
    operations: frozenset[str]
    write_operations: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not _text(self.instance_id) or not _text(self.port):
            raise ValueError("capability identifiers are invalid")
        operations = _operation_set(self.operations, field_name="operations")
        writes = self.write_operations
        if type(writes) is not frozenset:
            raise TypeError("write_operations must be a frozenset")
        if any(not _text(operation) or operation.startswith("service.") for operation in writes):
            raise ValueError("write_operations contains an invalid operation")
        if not writes.issubset(operations):
            raise ValueError("write_operations must be a subset of operations")


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    """Trusted scope template and operation grant for one service resource."""

    instance_id: str
    port: str
    scope_ref: str
    scope: WireScope
    operations: frozenset[str]

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (self.instance_id, self.port, self.scope_ref)):
            raise ValueError("resource grant identifiers are invalid")
        if type(self.scope) is not WireScope:
            raise TypeError("scope must be WireScope")
        if self.scope.run_id is not None:
            raise ValueError("trusted scope templates must not bind a run_id")
        _operation_set(self.operations, field_name="grant operations")


@dataclass(frozen=True, slots=True)
class PrincipalGrant:
    """Exact trusted wire identity and the resources it may register."""

    identity: WireIdentity
    resources: tuple[ResourceGrant, ...]

    def __post_init__(self) -> None:
        if type(self.identity) is not WireIdentity:
            raise TypeError("identity must be WireIdentity")
        if type(self.resources) is not tuple:
            raise TypeError("resources must be a tuple")
        if any(type(resource) is not ResourceGrant for resource in self.resources):
            raise TypeError("resources must contain ResourceGrant values")


@dataclass(frozen=True, slots=True)
class ServiceCallContext:
    """Trusted context passed to a code-registered business handler."""

    identity: WireIdentity
    scope: WireScope
    registration_id: str
    principal_ref: str


def validate_capabilities_and_grants(
    capabilities: tuple[ServiceCapability, ...],
    grants: Mapping[str, tuple[PrincipalGrant, ...]],
) -> tuple[
    Mapping[tuple[str, str], ServiceCapability],
    Mapping[str, tuple[PrincipalGrant, ...]],
]:
    """Purely validate and snapshot code capabilities against trusted grants.

    Service composition calls this before acquiring component resources; the
    registry constructor reuses the same checks before opening its database.
    It performs no I/O and does not alter registration or lease behavior.
    """

    if type(capabilities) is not tuple or not capabilities:
        raise TypeError("capabilities must be a non-empty tuple")
    if any(type(capability) is not ServiceCapability for capability in capabilities):
        raise TypeError("capabilities must contain ServiceCapability values")
    capability_map = {(item.instance_id, item.port): item for item in capabilities}
    if len(capability_map) != len(capabilities):
        raise ValueError("capability instance/port pairs must be unique")

    trusted_grants: dict[str, tuple[PrincipalGrant, ...]] = {}
    if not isinstance(grants, Mapping):
        raise TypeError("grants must be a mapping")
    for principal, entries in grants.items():
        if not _principal_ref(principal) or type(entries) is not tuple:
            raise ValueError("principal grant mapping is invalid")
        if any(type(entry) is not PrincipalGrant for entry in entries):
            raise TypeError("principal grants must contain PrincipalGrant values")
        for entry in entries:
            for resource in entry.resources:
                capability = capability_map.get((resource.instance_id, resource.port))
                if capability is None or not resource.operations.issubset(capability.operations):
                    raise ValueError("resource grant exceeds declared capability")
        trusted_grants[principal] = entries
    return MappingProxyType(capability_map), MappingProxyType(trusted_grants)


class ServiceRegistryBusyError(RuntimeError):
    """Another live registry owner already holds this database path."""


class ServiceRegistryStartupCleanupError(RuntimeError):
    """A failed registry startup could not close every resource it acquired."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        super().__init__("service registry startup cleanup failed; retry close")


class _StaleOwnerError(RuntimeError):
    pass


class ServiceRegistry:
    """SQLite-backed registration leases and authorization gate.

    ``principal`` passed to :meth:`handle` is the transport-authenticated key
    (for example, a CURVE public-key identity). Wire identity/scope values are
    always compared with ``grants``; they are never treated as credentials.

    Service-role portfolio writes remain denied until a separate trusted
    coordinator-generation claim path is available.
    """

    _owner_guard: ClassVar[threading.Lock] = threading.Lock()
    _owned_paths: ClassVar[set[str]] = set()

    def __init__(
        self,
        path: str | Path,
        *,
        service_id: str,
        capabilities: tuple[ServiceCapability, ...],
        grants: Mapping[str, tuple[PrincipalGrant, ...]],
        policy_version: str,
        requirements_version: str,
        lease_seconds: float = 30,
        clock: Callable[[], float] = time.time,
        ready: bool = True,
        max_sessions: int = _MAX_SESSIONS,
        max_registration_keys: int = _DEFAULT_REGISTRATION_KEYS,
    ) -> None:
        if not _text(service_id):
            raise ValueError("service_id is invalid")
        if not _text(policy_version) or not _text(requirements_version):
            raise ValueError("version references are invalid")
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds):
            raise ValueError("lease_seconds is invalid")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if type(max_sessions) is not int or not 1 <= max_sessions <= _MAX_SESSIONS:
            raise ValueError("max_sessions must be between 1 and 1024")
        if (
            type(max_registration_keys) is not int
            or not 1 <= max_registration_keys <= _MAX_REGISTRATION_KEYS
        ):
            raise ValueError("max_registration_keys must be between 1 and 16384")
        if type(ready) is not bool or not callable(clock):
            raise TypeError("ready or clock is invalid")
        capability_map, trusted_grants = validate_capabilities_and_grants(capabilities, grants)

        self.service_id = service_id
        self.policy_version = policy_version
        self.requirements_version = requirements_version
        self.lease_seconds = float(lease_seconds)
        self.max_sessions = max_sessions
        self.max_registration_keys = max_registration_keys
        self._clock = clock
        self._capabilities = capability_map
        self._grants = trusted_grants
        self._handlers: dict[
            tuple[str, str], Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]]
        ] = {}
        self._ready = ready
        self._lock = threading.RLock()
        self._closed = False
        self._epoch = uuid.uuid4().hex
        db_path = str(path)
        self._db: sqlite3.Connection | None = None
        self._owner_key: str | None = None
        self._owner_file: Any = None
        try:
            self._acquire_owner_lock(db_path)
            self._db = sqlite3.connect(
                db_path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA busy_timeout = 5000")
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
            with self._transaction(check_epoch=False):
                existing_service = self._db.execute(
                    "SELECT value FROM registry_meta WHERE key='service_id'"
                ).fetchone()
                if existing_service is not None and existing_service["value"] != self.service_id:
                    raise ValueError("registry database belongs to another service_id")
                self._db.execute(
                    "INSERT INTO registry_meta(key, value) VALUES('service_id', ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (self.service_id,),
                )
                # A new process epoch invalidates every prior lease. Strategy
                # write claims intentionally live in a separate durable table.
                self._db.execute("DELETE FROM sessions")
                self._db.execute("UPDATE strategy_write_claims SET active_registration_id=NULL")
                self._db.execute(
                    "INSERT INTO registry_meta(key, value) VALUES('service_epoch', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (self._epoch,),
                )
        except BaseException:
            self._ready = False
            cleanup_failed = False
            if self._db is not None:
                try:
                    self._db.close()
                except BaseException:
                    cleanup_failed = True
                else:
                    self._db = None
            # Keep the owner lock while a database connection remains open;
            # the host can retry both resources through the retained object.
            if self._db is None:
                try:
                    self._release_owner_lock()
                except BaseException:
                    cleanup_failed = True
            if cleanup_failed:
                raise ServiceRegistryStartupCleanupError(self) from None
            self._closed = True
            raise

    @property
    def service_epoch(self) -> str:
        return self._epoch

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS registry_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                registration_id TEXT PRIMARY KEY,
                principal TEXT NOT NULL,
                registration_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                port TEXT NOT NULL,
                scope_ref TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                operations_json TEXT NOT NULL,
                write_operations_json TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                requirements_version TEXT NOT NULL,
                service_epoch TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_by_principal
                ON sessions(principal, expires_at);
            CREATE TABLE IF NOT EXISTS registration_keys (
                principal TEXT NOT NULL,
                registration_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                registration_id TEXT NOT NULL,
                PRIMARY KEY(principal, registration_key)
            );
            CREATE TABLE IF NOT EXISTS strategy_write_claims (
                execution_domain_ref TEXT NOT NULL,
                strategy_ref TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                active_registration_id TEXT,
                PRIMARY KEY(execution_domain_ref, strategy_ref, account_ref)
            );
            """
        )
        columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(strategy_write_claims)")
        }
        if "active_registration_id" not in columns:
            self._db.execute(
                "ALTER TABLE strategy_write_claims ADD COLUMN active_registration_id TEXT"
            )

    def _acquire_owner_lock(self, db_path: str) -> None:
        if db_path == ":memory:":
            return
        key = os.path.normcase(str(Path(db_path).resolve()))
        with self._owner_guard:
            if key in self._owned_paths:
                raise ServiceRegistryBusyError("registry database already has a live owner")
            self._owned_paths.add(key)
        self._owner_key = key
        try:
            # Keep this descriptor open until close() to retain the OS lock.
            owner_file = open(key + ".service_registry.lock", "a+b")  # noqa: SIM115
            self._owner_file = owner_file
            owner_file.seek(0, os.SEEK_END)
            if owner_file.tell() == 0:
                owner_file.write(b"\0")
                owner_file.flush()
            owner_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(owner_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ServiceRegistryBusyError("registry database already has a live owner") from None

    def _release_owner_lock(self) -> None:
        owner_file = self._owner_file
        owner_key = self._owner_key
        if owner_file is not None:
            try:
                # close() may have closed the descriptor before reporting an
                # error. Retrying cleanup must not seek that closed file.
                if not owner_file.closed:
                    if os.name == "nt":
                        import msvcrt

                        owner_file.seek(0)
                        msvcrt.locking(owner_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(owner_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            owner_file.close()
            self._owner_file = None
        if owner_key is not None:
            with self._owner_guard:
                self._owned_paths.discard(owner_key)
            self._owner_key = None

    @contextmanager
    def _transaction(self, *, check_epoch: bool = True) -> Iterator[None]:
        with self._lock:
            if self._closed or self._db is None:
                raise sqlite3.ProgrammingError("registry is closed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if check_epoch:
                    row = self._db.execute(
                        "SELECT value FROM registry_meta WHERE key='service_epoch'"
                    ).fetchone()
                    if row is None or row["value"] != self._epoch:
                        raise _StaleOwnerError
                yield
            except BaseException:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("clock returned an invalid value")
        return float(value)

    def _expire_sessions(self, now: float) -> None:
        # The active set is capped at 1024, so this bounded cleanup is cheap.
        self._db.execute(
            "UPDATE strategy_write_claims SET active_registration_id=NULL "
            "WHERE active_registration_id IN (SELECT registration_id FROM sessions "
            "WHERE service_epoch <> ? OR expires_at <= ?)",
            (self._epoch, now),
        )
        self._db.execute(
            "DELETE FROM sessions WHERE service_epoch <> ? OR expires_at <= ?",
            (self._epoch, now),
        )

    def register_handler(
        self,
        instance_id: str,
        operation: str,
        handler: Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        """Register a local code-owned handler for a declared capability."""

        if not _text(instance_id) or not _text(operation) or operation.startswith("service."):
            raise ValueError("handler key is invalid")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._ensure_open()
            if not any(
                cap.instance_id == instance_id and operation in cap.operations
                for cap in self._capabilities.values()
            ):
                raise ValueError("handler operation is outside declared capability")
            key = (instance_id, operation)
            if key in self._handlers:
                raise ValueError("handler is already registered")
            self._handlers[key] = handler

    def set_ready(self, ready: bool) -> None:
        if type(ready) is not bool:
            raise TypeError("ready must be bool")
        with self._lock:
            self._ensure_open()
            self._ready = ready

    def _ensure_open(self) -> None:
        if self._closed or self._db is None:
            raise RuntimeError("registry is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._ready = False
            if self._db is not None:
                self._db.close()
                self._db = None
            self._release_owner_lock()
            self._closed = True

    def handle(self, authenticated_principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        """Authorize and handle one already-decoded v2 request envelope."""

        with self._lock:
            if self._closed or self._db is None:
                return self._failure("CLOSED")
        if not _principal_ref(authenticated_principal) or type(request) is not WireEnvelope:
            return self._failure("INVALID_REQUEST")
        try:
            validate_request(request)
        except WireValidationError:
            return self._failure("INVALID_REQUEST")

        operation = request.header.operation
        expected_schema = _CONTROL_SCHEMA if operation.startswith("service.") else _CALL_SCHEMA
        if (
            request.header.schema_id != expected_schema
            or request.header.schema_version != _SCHEMA_VERSION
        ):
            return self._failure("INVALID_REQUEST")
        if operation not in _CONTROL_OPERATIONS and operation.startswith("service."):
            return self._failure("UNKNOWN_OPERATION")

        try:
            if operation == "service.discover":
                return self._discover(authenticated_principal, request)
            if operation == "service.register":
                return self._register(authenticated_principal, request)
            if operation == "service.renew":
                return self._renew(authenticated_principal, request)
            if operation == "service.unregister":
                return self._unregister(authenticated_principal, request)
            return self._dispatch(authenticated_principal, request)
        except sqlite3.Error:
            return self._failure("UNAVAILABLE")
        except _StaleOwnerError:
            return self._failure("STALE_EPOCH")
        except (TypeError, ValueError, OverflowError, KeyError):
            return self._failure("INVALID_REQUEST")

    @staticmethod
    def _success(result: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": True, "code": "OK", "result": dict(result)}

    @staticmethod
    def _failure(code: str) -> Mapping[str, Any]:
        return {"ok": False, "code": code, "result": {}}

    @staticmethod
    def _payload_fields(
        request: WireEnvelope, expected: frozenset[str]
    ) -> Mapping[str, Any] | None:
        payload = request.payload
        if not isinstance(payload, Mapping) or frozenset(payload.keys()) != expected:
            return None
        return payload

    def _identity_is_trusted(self, principal: str, identity: WireIdentity) -> bool:
        return any(grant.identity == identity for grant in self._grants.get(principal, ()))

    def _discover(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        if self._payload_fields(request, frozenset()) is None:
            return self._failure("INVALID_REQUEST")
        with self._transaction():
            if not self._identity_is_trusted(principal, request.header.identity):
                return self._failure("DENIED")
            result = {
                "service_id": self.service_id,
                "protocol_version": WIRE_PROTOCOL_VERSION,
                "service_epoch": self._epoch,
                "ready": self._ready,
                "capabilities": [
                    {
                        "instance_id": capability.instance_id,
                        "port": capability.port,
                        "operations": sorted(capability.operations),
                    }
                    for capability in sorted(
                        self._capabilities.values(), key=lambda item: (item.instance_id, item.port)
                    )
                ],
            }
        return self._success(result)

    @staticmethod
    def _scope_dict(scope: WireScope) -> dict[str, Any]:
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

    @staticmethod
    def _identity_dict(identity: WireIdentity) -> dict[str, Any]:
        return {
            "role": identity.role.value,
            "principal_ref": identity.principal_ref,
            "actor_ref": identity.actor_ref,
            "delegate_ref": identity.delegate_ref,
        }

    @staticmethod
    def _stored_identity(value: str) -> WireIdentity:
        raw = json.loads(value)
        return WireIdentity(
            role=WireRole(raw["role"]),
            principal_ref=raw["principal_ref"],
            actor_ref=raw["actor_ref"],
            delegate_ref=raw["delegate_ref"],
        )

    @staticmethod
    def _stored_scope(value: str) -> WireScope:
        raw = json.loads(value)
        return WireScope(
            execution_domain_ref=raw["execution_domain_ref"],
            account_ref=raw["account_ref"],
            account_refs=tuple(raw["account_refs"]),
            strategy_ref=raw["strategy_ref"],
            run_id=raw["run_id"],
            manual_book_ref=raw["manual_book_ref"],
            portfolio_ref=raw["portfolio_ref"],
            group_id=raw["group_id"],
            leg_id=raw["leg_id"],
        )

    @staticmethod
    def _scope_matches(template: WireScope, requested: WireScope, identity: WireIdentity) -> bool:
        if identity.role is WireRole.STRATEGY_CLIENT:
            if template.strategy_ref is None or template.run_id is not None:
                return False
            if requested.strategy_ref is None or requested.run_id is None:
                return False
            return (
                requested.execution_domain_ref == template.execution_domain_ref
                and requested.account_ref == template.account_ref
                and requested.account_refs == template.account_refs
                and requested.strategy_ref == template.strategy_ref
                and requested.manual_book_ref == template.manual_book_ref
                and requested.portfolio_ref == template.portfolio_ref
                and requested.group_id == template.group_id
                and requested.leg_id == template.leg_id
            )
        return requested == template

    def _authorized_resource(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        requested_operations: frozenset[str],
    ) -> tuple[str, ServiceCapability | None, frozenset[str]]:
        if not self._identity_is_trusted(principal, identity):
            return "DENIED", None, frozenset()
        capability = self._capabilities.get((instance_id, port))
        if capability is None or not requested_operations.issubset(capability.operations):
            return "CAPABILITY_UNAVAILABLE", capability, frozenset()
        resource_candidates = [
            resource
            for principal_grant in self._grants.get(principal, ())
            if principal_grant.identity == identity
            for resource in principal_grant.resources
            if resource.instance_id == instance_id and resource.port == port
        ]
        if not resource_candidates:
            return "SCOPE_DENIED", capability, frozenset()
        matching_refs = [
            resource for resource in resource_candidates if resource.scope_ref == scope_ref
        ]
        if not matching_refs:
            return "SCOPE_DENIED", capability, frozenset()
        grants = [
            resource
            for resource in matching_refs
            if self._scope_matches(resource.scope, scope, identity)
        ]
        if not grants:
            return "SCOPE_DENIED", capability, frozenset()
        allowed = frozenset().union(*(resource.operations for resource in grants))
        granted = requested_operations & capability.operations & allowed
        if not requested_operations or granted != requested_operations:
            return "SCOPE_DENIED", capability, frozenset()
        if granted & capability.write_operations:
            if identity.role is WireRole.STRATEGY_CLIENT:
                # Strategy writes are admitted below only when the concrete
                # scope supplies both strategy and run identifiers.
                if scope.strategy_ref is None or scope.run_id is None:
                    return "SCOPE_DENIED", capability, frozenset()
            elif scope.strategy_ref is not None:
                # A trusted app/service grant cannot become a strategy writer
                # without an explicit delegated-generation contract.
                return "SCOPE_DENIED", capability, frozenset()
            elif identity.role is WireRole.SERVICE and scope.portfolio_ref is not None:
                # Portfolio coordination needs its own durable active-generation
                # claim; this registry does not admit an unclaimed coordinator.
                return "SCOPE_DENIED", capability, frozenset()
            elif scope.manual_book_ref is None and scope.portfolio_ref is None:
                # Account-only writes do not establish order ownership.
                return "SCOPE_DENIED", capability, frozenset()
        return "OK", capability, granted

    def _register(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        payload = self._payload_fields(
            request,
            frozenset(
                {
                    "registration_key",
                    "client_session_id",
                    "instance_id",
                    "port",
                    "scope_ref",
                    "operations",
                }
            ),
        )
        if payload is None:
            return self._failure("INVALID_REQUEST")
        registration_key = payload["registration_key"]
        client_session_id = payload["client_session_id"]
        instance_id = payload["instance_id"]
        port = payload["port"]
        scope_ref = payload["scope_ref"]
        raw_operations = payload["operations"]
        if not all(
            _text(value)
            for value in (registration_key, client_session_id, instance_id, port, scope_ref)
        ):
            return self._failure("INVALID_REQUEST")
        if (
            type(raw_operations) is not tuple
            or not 1 <= len(raw_operations) <= _MAX_OPERATIONS_PER_REQUEST
        ):
            return self._failure("INVALID_REQUEST")
        if any(not _text(item) for item in raw_operations) or len(set(raw_operations)) != len(
            raw_operations
        ):
            return self._failure("INVALID_REQUEST")
        requested = frozenset(raw_operations)
        status, capability, granted = self._authorized_resource(
            principal,
            request.header.identity,
            instance_id=instance_id,
            port=port,
            scope_ref=scope_ref,
            scope=request.header.scope,
            requested_operations=requested,
        )
        if status != "OK" or capability is None:
            return self._failure(status)
        now = self._now()
        expires_at = now + self.lease_seconds
        if not math.isfinite(expires_at):
            return self._failure("INVALID_REQUEST")
        digest_value = {
            "client_session_id": client_session_id,
            "identity": self._identity_dict(request.header.identity),
            "instance_id": instance_id,
            "operations": sorted(granted),
            "port": port,
            "scope": self._scope_dict(request.header.scope),
            "scope_ref": scope_ref,
        }
        digest = hashlib.sha256(
            json.dumps(digest_value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._transaction():
            self._expire_sessions(now)
            if not self._ready:
                return self._failure("POLICY_REQUIREMENT_UNMET")
            previous = self._db.execute(
                "SELECT request_digest, registration_id FROM registration_keys "
                "WHERE principal=? AND registration_key=?",
                (principal, registration_key),
            ).fetchone()
            if previous is not None and previous["request_digest"] != digest:
                return self._failure("CONFLICT")
            if previous is not None:
                active = self._db.execute(
                    "SELECT * FROM sessions WHERE registration_id=? AND service_epoch=?",
                    (previous["registration_id"], self._epoch),
                ).fetchone()
                if active is not None and active["expires_at"] > now:
                    return self._success(self._registration_result(active, now=now))
            else:
                key_count = self._db.execute("SELECT COUNT(*) FROM registration_keys").fetchone()[0]
                if key_count >= self.max_registration_keys:
                    return self._failure("CAPACITY")
            active_count = self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            if active_count >= self.max_sessions:
                return self._failure("CAPACITY")
            registration_id = uuid.uuid4().hex
            if request.header.identity.role is WireRole.STRATEGY_CLIENT:
                write_ops = granted & capability.write_operations
                if write_ops:
                    claim_error = self._claim_strategy_write(
                        request.header.scope, now, registration_id
                    )
                    if claim_error is not None:
                        return self._failure(claim_error)

            identity_json = json.dumps(
                self._identity_dict(request.header.identity), sort_keys=True, separators=(",", ":")
            )
            scope_json = json.dumps(
                self._scope_dict(request.header.scope), sort_keys=True, separators=(",", ":")
            )
            self._db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    registration_id,
                    principal,
                    registration_key,
                    digest,
                    identity_json,
                    instance_id,
                    port,
                    scope_ref,
                    scope_json,
                    json.dumps(sorted(granted), separators=(",", ":")),
                    json.dumps(
                        sorted(granted & capability.write_operations), separators=(",", ":")
                    ),
                    self.policy_version,
                    self.requirements_version,
                    self._epoch,
                    expires_at,
                ),
            )
            self._db.execute(
                "INSERT INTO registration_keys(principal, registration_key, request_digest, registration_id) "
                "VALUES(?,?,?,?) ON CONFLICT(principal, registration_key) DO UPDATE SET "
                "registration_id=excluded.registration_id",
                (principal, registration_key, digest, registration_id),
            )
            row = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=?", (registration_id,)
            ).fetchone()
        return self._success(self._registration_result(row, now=now))

    def _claim_strategy_write(
        self, scope: WireScope, now: float, registration_id: str
    ) -> str | None:
        if scope.strategy_ref is None or scope.run_id is None:
            return "SCOPE_DENIED"
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return "SCOPE_DENIED"
        for account_ref in accounts:
            row = self._db.execute(
                "SELECT run_id, active_registration_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, account_ref),
            ).fetchone()
            if row is not None and row["run_id"] != scope.run_id:
                return "DUPLICATE_ACTIVE_RUN"
            if row is not None and row["active_registration_id"] is not None:
                return "DUPLICATE_ACTIVE_RUN"
        for account_ref in accounts:
            self._db.execute(
                "INSERT OR IGNORE INTO strategy_write_claims "
                "(execution_domain_ref, strategy_ref, account_ref, run_id, created_at, active_registration_id) "
                "VALUES(?,?,?,?,?,?)",
                (
                    scope.execution_domain_ref,
                    scope.strategy_ref,
                    account_ref,
                    scope.run_id,
                    now,
                    registration_id,
                ),
            )
            self._db.execute(
                "UPDATE strategy_write_claims SET active_registration_id=? "
                "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=? AND run_id=? "
                "AND active_registration_id IS NULL",
                (
                    registration_id,
                    scope.execution_domain_ref,
                    scope.strategy_ref,
                    account_ref,
                    scope.run_id,
                ),
            )
        return None

    def _registration_result(self, row: sqlite3.Row, *, now: float) -> dict[str, Any]:
        lease_remaining = float(row["expires_at"]) - now
        if not math.isfinite(lease_remaining) or lease_remaining <= 0:
            raise ValueError("registration receipt would contain an expired lease")
        return {
            "registration_id": row["registration_id"],
            "service_epoch": row["service_epoch"],
            "expires_at": row["expires_at"],
            "lease_remaining_seconds": lease_remaining,
            "operations": json.loads(row["operations_json"]),
            "policy_version": row["policy_version"],
            "requirements_version": row["requirements_version"],
        }

    def _renew_or_unregister(
        self,
        principal: str,
        request: WireEnvelope,
        *,
        renew: bool,
    ) -> Mapping[str, Any]:
        payload = self._payload_fields(request, frozenset({"registration_id", "service_epoch"}))
        if (
            payload is None
            or not _text(payload["registration_id"])
            or not _text(payload["service_epoch"])
        ):
            return self._failure("INVALID_REQUEST")
        if payload["service_epoch"] != self._epoch:
            return self._failure("STALE_EPOCH")
        now = self._now()
        with self._transaction():
            self._expire_sessions(now)
            row = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
                (payload["registration_id"], principal, self._epoch),
            ).fetchone()
            if row is None:
                return self._failure("NOT_REGISTERED")
            stored_identity = self._stored_identity(row["identity_json"])
            stored_scope = self._stored_scope(row["scope_json"])
            if request.header.identity != stored_identity:
                return self._failure("DENIED")
            if request.header.scope != stored_scope:
                return self._failure("SCOPE_DENIED")
            if not self._identity_is_trusted(principal, stored_identity):
                return self._failure("DENIED")
            if renew:
                if not self._ready:
                    return self._failure("POLICY_REQUIREMENT_UNMET")
                if (
                    row["policy_version"] != self.policy_version
                    or row["requirements_version"] != self.requirements_version
                ):
                    return self._failure("POLICY_REQUIREMENT_UNMET")
                expires_at = now + self.lease_seconds
                self._db.execute(
                    "UPDATE sessions SET expires_at=? WHERE registration_id=?",
                    (expires_at, payload["registration_id"]),
                )
                updated = dict(row)
                updated["expires_at"] = expires_at
                result = self._registration_result(updated, now=now)  # type: ignore[arg-type]
                return self._success(result)
            self._db.execute(
                "UPDATE strategy_write_claims SET active_registration_id=NULL "
                "WHERE active_registration_id=?",
                (payload["registration_id"],),
            )
            self._db.execute(
                "DELETE FROM sessions WHERE registration_id=?", (payload["registration_id"],)
            )
            return self._success(
                {"unregistered": True, "registration_id": payload["registration_id"]}
            )

    def _renew(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        return self._renew_or_unregister(principal, request, renew=True)

    def _unregister(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        return self._renew_or_unregister(principal, request, renew=False)

    def _dispatch(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        payload = self._payload_fields(
            request,
            frozenset({"registration_id", "service_epoch", "instance_id", "arguments"}),
        )
        if (
            payload is None
            or not _text(payload["registration_id"])
            or not _text(payload["service_epoch"])
            or not _text(payload["instance_id"])
            or not isinstance(payload["arguments"], Mapping)
        ):
            return self._failure("INVALID_REQUEST")
        if payload["service_epoch"] != self._epoch:
            return self._failure("STALE_EPOCH")
        now = self._now()
        with self._transaction():
            self._expire_sessions(now)
            row = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
                (payload["registration_id"], principal, self._epoch),
            ).fetchone()
            if row is None:
                return self._failure("NOT_REGISTERED")
            identity = self._stored_identity(row["identity_json"])
            scope = self._stored_scope(row["scope_json"])
            if request.header.identity != identity or request.header.scope != scope:
                return self._failure(
                    "DENIED" if request.header.identity != identity else "SCOPE_DENIED"
                )
            if not self._identity_is_trusted(principal, identity):
                return self._failure("DENIED")
            if not self._ready:
                return self._failure("POLICY_REQUIREMENT_UNMET")
            if (
                row["policy_version"] != self.policy_version
                or row["requirements_version"] != self.requirements_version
            ):
                return self._failure("POLICY_REQUIREMENT_UNMET")
            instance_id = row["instance_id"]
            if payload["instance_id"] != instance_id:
                return self._failure("DENIED")
            capability = self._capabilities.get((instance_id, row["port"]))
            granted = frozenset(json.loads(row["operations_json"]))
            write_ops = frozenset(json.loads(row["write_operations_json"]))
            operation = request.header.operation
            if capability is None or operation not in capability.operations:
                return self._failure("CAPABILITY_UNAVAILABLE")
            if operation not in granted or not self._current_resource_allows(
                principal,
                identity,
                instance_id=instance_id,
                port=row["port"],
                scope_ref=row["scope_ref"],
                scope=scope,
                operation=operation,
            ):
                return self._failure("SCOPE_DENIED")
            handler = self._handlers.get((instance_id, operation))
            if handler is None:
                return self._failure("CAPABILITY_UNAVAILABLE")
            # Do not dispatch a write operation unless the durable strategy
            # claim still matches the exact run bound to this registration.
            if (
                operation in write_ops
                and identity.role is WireRole.STRATEGY_CLIENT
                and not self._strategy_claim_exists(scope, row["registration_id"])
            ):
                return self._failure("DUPLICATE_ACTIVE_RUN")
            context = ServiceCallContext(identity, scope, row["registration_id"], principal)
            arguments = MappingProxyType(dict(payload["arguments"]))
        try:
            result = handler(context, arguments)
            if not isinstance(result, Mapping):
                return self._failure("HANDLER_FAILED")
            normalized = json.dumps(
                self._normalize_result(result),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8", errors="strict")
            if len(normalized) > _MAX_RESULT_BYTES:
                return self._failure("HANDLER_FAILED")
            return self._success(json.loads(normalized.decode("utf-8")))
        except Exception:
            return self._failure("HANDLER_FAILED")

    @classmethod
    def _normalize_result(cls, value: Any, *, depth: int = 0) -> Any:
        """Copy a handler result into strict, bounded-depth JSON values."""

        if depth > MAX_JSON_DEPTH:
            raise ValueError("handler result is too deeply nested")
        if value is None or type(value) in (bool, int, str):
            if type(value) is str:
                value.encode("utf-8", errors="strict")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("handler result contains a non-finite number")
            return value
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("handler result object keys must be strings")
                key.encode("utf-8", errors="strict")
                result[key] = cls._normalize_result(item, depth=depth + 1)
            return result
        if type(value) in (list, tuple):
            return [cls._normalize_result(item, depth=depth + 1) for item in value]
        raise TypeError("handler result contains a non-JSON value")

    def _current_resource_allows(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> bool:
        capability = self._capabilities.get((instance_id, port))
        if capability is None or operation not in capability.operations:
            return False
        if operation in capability.write_operations:
            if identity.role is WireRole.STRATEGY_CLIENT:
                if scope.strategy_ref is None or scope.run_id is None:
                    return False
            elif (
                (identity.role is WireRole.SERVICE and scope.portfolio_ref is not None)
                or scope.strategy_ref is not None
                or (scope.manual_book_ref is None and scope.portfolio_ref is None)
            ):
                return False
        return any(
            principal_grant.identity == identity
            and resource.instance_id == instance_id
            and resource.port == port
            and resource.scope_ref == scope_ref
            and operation in resource.operations
            and self._scope_matches(resource.scope, scope, identity)
            for principal_grant in self._grants.get(principal, ())
            for resource in principal_grant.resources
        )

    def _strategy_claim_exists(self, scope: WireScope, registration_id: str) -> bool:
        if scope.strategy_ref is None or scope.run_id is None:
            return False
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return False
        for account_ref in accounts:
            row = self._db.execute(
                "SELECT run_id, active_registration_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, account_ref),
            ).fetchone()
            if (
                row is None
                or row["run_id"] != scope.run_id
                or row["active_registration_id"] != registration_id
            ):
                return False
        return True
