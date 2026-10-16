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
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from bt_api_base.service_protocol import (
    MAX_JSON_DEPTH,
    MAX_REFERENCE_BYTES,
    MAX_WIRE_MESSAGE_BYTES,
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireIdentity,
    WireRole,
    WireScope,
    WireValidationError,
    validate_request,
)
from .service_migration_gate import (
    MigrationGateError,
    MigrationStartSnapshot,
    enable_migration_cutover,
    migration_start_guard as acquire_migration_start_guard,
    record_migration_write_attempt,
    STATUS_COMMITTED,
    STATUS_ENABLED,
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
_MIGRATION_WRITE_DISABLED = "MIGRATION_WRITE_DISABLED"
_MIGRATION_WRITE_ENABLEMENT_INVALID = "MIGRATION_WRITE_ENABLEMENT_INVALID"
_APPROVED_TARGET_OPERATION = ("service.order_entry", "orders.submit_approved_target")
_PAPER_OWNER_OPERATION = ("service.order_entry", "orders.submit")
_PAPER_LEGACY_IMPORT_PORT = "service.paper_legacy_import"
_PAPER_LEGACY_IMPORT_OPERATIONS = frozenset(
    {
        "paper.import.status",
        "paper.import.inspect_source",
        "paper.import.prepare",
        "paper.import.activate",
        "paper.import.abort",
    }
)
_PAPER_LEGACY_IMPORT_WRITE_OPERATIONS = frozenset(
    {"paper.import.prepare", "paper.import.activate", "paper.import.abort"}
)
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
        "MIGRATION_WRITE_DISABLED",
        "NOT_REGISTERED",
        "OK",
        "POLICY_REQUIREMENT_UNMET",
        "REPLY_TOO_LARGE",
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
    market_wire_version: int | None = None

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
        if self.market_wire_version is not None and (
            self.port != "service.market_source"
            or type(self.market_wire_version) is not int
            or self.market_wire_version not in (1, 2)
        ):
            raise ValueError("market_wire_version is valid only for a market source")


@dataclass(frozen=True, slots=True)
class AccountRiskPolicyBinding:
    """Trusted, scope-specific account Risk policy shown in registration receipts.

    The SDK composition root creates these only after checking the configured
    reference against the actual Risk gate policy supplied by the trusted
    resource provider. ``policy_version`` is the gate policy fingerprint, not
    an access-control or readiness version.
    """

    instance_id: str
    scope: WireScope
    policy_ref: str
    policy_version: str

    def __post_init__(self) -> None:
        if not _text(self.instance_id) or not _text(self.policy_ref):
            raise ValueError("account Risk policy binding identifiers are invalid")
        if type(self.scope) is not WireScope or self.scope.run_id is not None:
            raise TypeError("account Risk policy binding scope is invalid")
        if not self.scope.account_ref or self.scope.account_refs:
            raise ValueError("account Risk policy binding requires one account")
        if (
            type(self.policy_version) is not str
            or len(self.policy_version) != 64
            or any(character not in "0123456789abcdef" for character in self.policy_version)
        ):
            raise ValueError("account Risk policy version must be a SHA-256 fingerprint")


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
        if self.scope.run_id is not None and (
            self.scope.account_ref is None
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("exact run grants require one account and strategy")
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


def _is_exact_owner_cancel_scope(
    identity: WireIdentity,
    *,
    port: str,
    scope: WireScope,
    operation: str,
) -> bool:
    """Recognize the one exact-run service owner cancellation operation.

    This is only a narrow exception to the generic service-write policy. The
    caller still has to match a trusted ``PrincipalGrant`` and exact
    ``ResourceGrant``. A live strategy run claim is checked again at dispatch.
    """

    return (
        identity.role is WireRole.SERVICE
        and port == "service.owner_control"
        and operation == "owner.cancel"
        and scope.account_ref is not None
        and not scope.account_refs
        and scope.strategy_ref is not None
        and scope.run_id is not None
        and scope.manual_book_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


@dataclass(frozen=True, slots=True)
class ServiceCallContext:
    """Trusted context passed to a code-registered business handler."""

    identity: WireIdentity
    scope: WireScope
    registration_id: str
    principal_ref: str
    result_fits: Callable[[Mapping[str, Any]], bool] | None = None


@dataclass(frozen=True, slots=True)
class ActiveRegistrationContext:
    """Read-only owner projection of one currently eligible registration.

    The context is captured from a single Registry SQLite read snapshot. It
    deliberately says nothing about component-owned coordinator authority:
    callers must still activate and check that separate durable generation.
    Expired leases are omitted without deleting their rows or releasing any
    accepted business responsibility.
    """

    context: ServiceCallContext
    instance_id: str
    port: str
    scope_ref: str
    operations: frozenset[str]
    write_operations: frozenset[str]
    service_epoch: str
    policy_version: str
    requirements_version: str
    expires_at: float
    strategy_write_run_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.context) is not ServiceCallContext:
            raise TypeError("context must be ServiceCallContext")
        if not all(_text(value) for value in (self.instance_id, self.port, self.scope_ref)):
            raise ValueError("registration identifiers are invalid")
        if type(self.operations) is not frozenset or not self.operations:
            raise TypeError("operations must be a non-empty frozenset")
        if type(self.write_operations) is not frozenset or not self.write_operations.issubset(
            self.operations
        ):
            raise TypeError("write_operations must be a subset of operations")
        if not all(
            _text(value)
            for value in (self.service_epoch, self.policy_version, self.requirements_version)
        ):
            raise ValueError("registration versions are invalid")
        if type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at):
            raise ValueError("expires_at is invalid")
        if self.strategy_write_run_id is not None and not _text(self.strategy_write_run_id):
            raise ValueError("strategy_write_run_id is invalid")


@dataclass(frozen=True, slots=True)
class TrustedGroupWritePolicy:
    """Exact local policy for an accepted service-coordinated group write.

    This is an operator/deployment capability, never a service-manifest or
    wire value. The account-owner route and operation are fixed so an ordinary
    SERVICE ACL cannot turn another order route into a coordinator write.
    """

    instance_id: str
    principal_ref: str
    identity: WireIdentity
    scope_ref: str
    scope: WireScope
    run_id: str
    port: str = "service.order_entry"
    operation: str = "orders.submit_group_leg"

    def __post_init__(self) -> None:
        if not all(
            _text(value) for value in (self.instance_id, self.principal_ref, self.scope_ref)
        ):
            raise ValueError("group write policy identifiers are invalid")
        if type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE:
            raise ValueError("group write policy requires a service identity")
        if self.principal_ref != self.identity.principal_ref:
            raise ValueError("group write policy principal does not match its identity")
        if type(self.scope) is not WireScope:
            raise TypeError("group write policy scope must be WireScope")
        if (
            self.port != "service.order_entry"
            or self.operation not in {"orders.submit_group_leg", "orders.cancel_group_leg"}
            or not _text(self.run_id)
            or self.scope.execution_domain_ref is None
            or self.scope.account_ref is None
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.run_id is not None
            or self.scope.manual_book_ref is not None
            or self.scope.portfolio_ref is None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("group write policy scope is invalid")


@dataclass(frozen=True, slots=True)
class TrustedPaperOwnerWritePolicy:
    """Exact application submit grant backed by one constructed Paper owner.

    The policy is created by the Host only after the fixed Execution adapter
    has accepted the trusted PaperOwnerDomainV1, PaperMatchingPort and target
    binding. It cannot be requested or extended by a wire envelope. This
    exception admits only the ordinary ``orders.submit`` operation for the
    exact application identity and paper scope; it does not authorize other
    writes on that route.
    """

    instance_id: str
    principal_ref: str
    identity: WireIdentity
    scope_ref: str
    scope: WireScope
    paper_owner_domain: object = field(repr=False, compare=False)
    execution_scope: object = field(repr=False, compare=False)
    port: str = "service.order_entry"
    operation: str = "orders.submit"

    def __post_init__(self) -> None:
        if not all(
            _text(value) for value in (self.instance_id, self.principal_ref, self.scope_ref)
        ):
            raise ValueError("paper owner policy identifiers are invalid")
        if (
            type(self.identity) is not WireIdentity
            or self.identity.role is not WireRole.APPLICATION_CLIENT
            or self.principal_ref != self.identity.principal_ref
        ):
            raise ValueError("paper owner policy requires its exact application identity")
        if type(self.scope) is not WireScope:
            raise TypeError("paper owner policy scope must be WireScope")
        if self.port != _PAPER_OWNER_OPERATION[0] or self.operation != _PAPER_OWNER_OPERATION[1]:
            raise ValueError("paper owner policy route is fixed")
        if (
            self.scope.execution_domain_ref is None
            or self.scope.account_ref is None
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.run_id is not None
            or self.scope.manual_book_ref is not None
            or self.scope.portfolio_ref is not None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("paper owner policy scope is invalid")

        # Keep Execution optional at SDK import time. Selecting a paper owner
        # necessarily installs that package, and these exact types provide the
        # runtime proof that the grant is tied to its paper domain and target.
        try:
            from bt_api_execution import (
                PAPER_ENVIRONMENT,
                PAPER_PROVIDER,
                ExecutionScope,
                PaperOwnerDomainV1,
            )
        except Exception as error:
            raise ValueError("paper owner runtime types are unavailable") from error
        if (
            type(self.paper_owner_domain) is not PaperOwnerDomainV1
            or type(self.execution_scope) is not ExecutionScope
            or self.execution_scope.provider != PAPER_PROVIDER
            or self.execution_scope.environment != PAPER_ENVIRONMENT
            or self.execution_scope.account_ref != self.paper_owner_domain.simulation_account_ref
            or self.execution_scope.strategy_id != self.paper_owner_domain.strategy_ref
            or self.scope.execution_domain_ref != self.paper_owner_domain.execution_domain_ref
            or self.scope.account_ref != self.paper_owner_domain.simulation_account_ref
            or self.scope.strategy_ref != self.paper_owner_domain.strategy_ref
        ):
            raise ValueError("paper owner policy does not match its typed paper domain")
        try:
            self.paper_owner_domain.validate_binding(self.scope, self.execution_scope)
        except Exception as error:
            raise ValueError("paper owner policy target binding is invalid") from error


@dataclass(frozen=True, slots=True)
class TrustedPaperLegacyImportPolicy:
    """Exact application grant for one Host-owned legacy Paper import target."""

    instance_id: str
    principal_ref: str
    identity: WireIdentity
    scope_ref: str
    scope: WireScope
    paper_owner_domain: object = field(repr=False, compare=False)
    execution_scope: object = field(repr=False, compare=False)
    port: str = _PAPER_LEGACY_IMPORT_PORT

    def __post_init__(self) -> None:
        if not all(
            _text(value) for value in (self.instance_id, self.principal_ref, self.scope_ref)
        ):
            raise ValueError("paper legacy import policy identifiers are invalid")
        if (
            type(self.identity) is not WireIdentity
            or self.identity.role is not WireRole.APPLICATION_CLIENT
            or self.principal_ref != self.identity.principal_ref
        ):
            raise ValueError("paper legacy import policy requires its exact application identity")
        if type(self.scope) is not WireScope:
            raise TypeError("paper legacy import policy scope must be WireScope")
        if self.port != _PAPER_LEGACY_IMPORT_PORT:
            raise ValueError("paper legacy import policy route is fixed")
        if (
            self.scope.account_ref is None
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.run_id is not None
            or self.scope.manual_book_ref is not None
            or self.scope.portfolio_ref is not None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("paper legacy import policy scope is invalid")
        # Import Execution only when an operator actually selects this role.
        try:
            from bt_api_execution import ExecutionScope, PaperOwnerDomainV1
        except Exception as error:
            raise ValueError("paper legacy import runtime types are unavailable") from error
        if (
            type(self.paper_owner_domain) is not PaperOwnerDomainV1
            or type(self.execution_scope) is not ExecutionScope
            or self.scope.execution_domain_ref != self.paper_owner_domain.execution_domain_ref
            or self.scope.account_ref != self.paper_owner_domain.simulation_account_ref
            or self.scope.strategy_ref != self.paper_owner_domain.strategy_ref
        ):
            raise ValueError("paper legacy import policy does not match its typed target")
        try:
            self.paper_owner_domain.validate_binding(self.scope, self.execution_scope)
        except Exception as error:
            raise ValueError("paper legacy import policy target binding is invalid") from error

    @property
    def operations(self) -> frozenset[str]:
        return _PAPER_LEGACY_IMPORT_OPERATIONS

    @property
    def write_operations(self) -> frozenset[str]:
        return _PAPER_LEGACY_IMPORT_WRITE_OPERATIONS


_GROUP_RESERVATION_OPERATIONS = frozenset(
    {"reservations.reserve", "reservations.query", "reservations.release"}
)
_GROUP_RESERVATION_WRITE_OPERATIONS = frozenset({"reservations.reserve", "reservations.release"})


@dataclass(frozen=True, slots=True)
class TrustedGroupReservationPolicy:
    """Exact operator policy for one account owner's group reservation route."""

    instance_id: str
    principal_ref: str
    identity: WireIdentity
    scope_ref: str
    scope: WireScope
    run_id: str
    account_ref: str
    port: str = "service.group_reservations"

    def __post_init__(self) -> None:
        if not all(
            _text(value)
            for value in (
                self.instance_id,
                self.principal_ref,
                self.scope_ref,
                self.run_id,
                self.account_ref,
            )
        ):
            raise ValueError("group reservation policy identifiers are invalid")
        if type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE:
            raise ValueError("group reservation policy requires a service identity")
        if self.principal_ref != self.identity.principal_ref:
            raise ValueError("group reservation policy principal does not match its identity")
        if type(self.scope) is not WireScope:
            raise TypeError("group reservation policy scope must be WireScope")
        if (
            self.port != "service.group_reservations"
            or self.scope.execution_domain_ref is None
            or self.scope.account_ref != self.account_ref
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.run_id is not None
            or self.scope.manual_book_ref is not None
            or self.scope.portfolio_ref is None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("group reservation policy scope is invalid")

    @property
    def operations(self) -> frozenset[str]:
        return _GROUP_RESERVATION_OPERATIONS

    @property
    def write_operations(self) -> frozenset[str]:
        return _GROUP_RESERVATION_WRITE_OPERATIONS


@dataclass(frozen=True, slots=True)
class TrustedGroupResolutionPolicy:
    """Exact operator policy for resolving one accepted account group leg."""

    instance_id: str
    principal_ref: str
    identity: WireIdentity
    scope_ref: str
    scope: WireScope
    run_id: str
    account_ref: str
    port: str = "service.order_entry"
    operation: str = "orders.resolve_group_leg"

    def __post_init__(self) -> None:
        if not all(
            _text(value)
            for value in (
                self.instance_id,
                self.principal_ref,
                self.scope_ref,
                self.run_id,
                self.account_ref,
            )
        ):
            raise ValueError("group resolution policy identifiers are invalid")
        if type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE:
            raise ValueError("group resolution policy requires a service identity")
        if self.principal_ref != self.identity.principal_ref:
            raise ValueError("group resolution policy principal does not match its identity")
        if type(self.scope) is not WireScope:
            raise TypeError("group resolution policy scope must be WireScope")
        if (
            self.port != "service.order_entry"
            or self.operation != "orders.resolve_group_leg"
            or self.scope.execution_domain_ref is None
            or self.scope.account_ref != self.account_ref
            or self.scope.account_refs
            or self.scope.strategy_ref is None
            or self.scope.run_id is not None
            or self.scope.manual_book_ref is not None
            or self.scope.portfolio_ref is None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("group resolution policy scope is invalid")


class ServiceRegistryStrategyRunConflictError(RuntimeError):
    """Trusted group mapping conflicts with an existing logical strategy run."""

    code = "DUPLICATE_ACTIVE_RUN"

    def __init__(self) -> None:
        super().__init__(self.code)


class ServiceRegistryGroupWritePolicyError(RuntimeError):
    """No operator-configured policy authorizes a trusted group run binding."""

    code = "SCOPE_DENIED"

    def __init__(self) -> None:
        super().__init__(self.code)


class ServiceResultTooLargeError(RuntimeError):
    """A trusted adapter could not fit a bounded response page."""

    code = "REPLY_TOO_LARGE"

    def __init__(self) -> None:
        super().__init__(self.code)


class ServiceOperationDeniedError(RuntimeError):
    """A trusted adapter proved an operation was rejected before side effects.

    The registry only maps this exact exception to a definitive wire denial
    for market subscription and monitor control admission. Other operations
    and exception types retain the ordinary ``HANDLER_FAILED`` outcome.
    """

    def __init__(self) -> None:
        super().__init__("SERVICE_OPERATION_DENIED")


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
                if resource.scope.run_id is not None and (
                    not (
                        entry.identity.role is WireRole.APPLICATION_CLIENT
                        and resource.port == "service.monitor_control"
                        and resource.operations.issubset(
                            {"monitor.control", "monitor.control_status"}
                        )
                    )
                    and not (
                        resource.operations == frozenset({"owner.cancel"})
                        and _is_exact_owner_cancel_scope(
                            entry.identity,
                            port=resource.port,
                            scope=resource.scope,
                            operation="owner.cancel",
                        )
                    )
                ):
                    raise ValueError("exact run grants exceed an allowed owner operation")
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

    Service-role portfolio writes remain denied. The only service-role
    strategy-scoped write is the exact account group-leg operation admitted by
    an operator policy and a persistent logical-run claim.
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
        reply_size_checker: Callable[[WireEnvelope, Mapping[str, Any], int | None], bool]
        | None = None,
        trusted_group_write_policies: tuple[TrustedGroupWritePolicy, ...] = (),
        trusted_group_reservation_policies: tuple[TrustedGroupReservationPolicy, ...] = (),
        trusted_group_resolution_policies: tuple[TrustedGroupResolutionPolicy, ...] = (),
        trusted_paper_owner_write_policies: tuple[TrustedPaperOwnerWritePolicy, ...] = (),
        trusted_paper_legacy_import_policies: tuple[TrustedPaperLegacyImportPolicy, ...] = (),
        access_policy_ref: str | None = None,
        requirements_ref: str | None = None,
        account_risk_policy_bindings: tuple[AccountRiskPolicyBinding, ...] = (),
        account_risk_policy_required_routes: frozenset[tuple[str, str]] = frozenset(),
        migration_deployment_epoch: str | None = None,
        migration_start_guard: MigrationStartSnapshot | None = None,
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
        if (
            type(ready) is not bool
            or not callable(clock)
            or (reply_size_checker is not None and not callable(reply_size_checker))
        ):
            raise TypeError("ready or clock is invalid")
        if migration_deployment_epoch is not None and (
            type(migration_deployment_epoch) is not str or not migration_deployment_epoch
        ):
            raise ValueError("migration_deployment_epoch is invalid")
        capability_map, trusted_grants = validate_capabilities_and_grants(capabilities, grants)
        effective_access_policy_ref = policy_version if access_policy_ref is None else access_policy_ref
        effective_requirements_ref = (
            requirements_version if requirements_ref is None else requirements_ref
        )
        if not _text(effective_access_policy_ref) or not _text(effective_requirements_ref):
            raise ValueError("policy references are invalid")
        if type(account_risk_policy_bindings) is not tuple or any(
            type(binding) is not AccountRiskPolicyBinding
            for binding in account_risk_policy_bindings
        ):
            raise TypeError("account_risk_policy_bindings must be a tuple")
        if type(account_risk_policy_required_routes) is not frozenset or any(
            type(route) is not tuple
            or len(route) != 2
            or not all(_text(value) for value in route)
            or route[1] != "service.order_entry"
            for route in account_risk_policy_required_routes
        ):
            raise TypeError("account_risk_policy_required_routes must be a frozenset")
        account_risk_bindings_by_route: dict[tuple[str, WireScope], AccountRiskPolicyBinding] = {}
        for binding in account_risk_policy_bindings:
            route = (binding.instance_id, "service.order_entry")
            if route not in capability_map:
                raise ValueError("account Risk policy binding is outside the local capability")
            key = (binding.instance_id, binding.scope)
            if key in account_risk_bindings_by_route:
                raise ValueError("duplicate account Risk policy binding")
            account_risk_bindings_by_route[key] = binding
        if any(route not in capability_map for route in account_risk_policy_required_routes):
            raise ValueError("required account Risk policy route is not declared")
        if any(
            not any(binding.instance_id == route[0] for binding in account_risk_policy_bindings)
            for route in account_risk_policy_required_routes
        ):
            raise ValueError("required account Risk policy route has no binding")
        if type(trusted_group_write_policies) is not tuple or any(
            type(policy) is not TrustedGroupWritePolicy for policy in trusted_group_write_policies
        ):
            raise TypeError("trusted_group_write_policies must be a tuple of trusted policies")
        if type(trusted_group_reservation_policies) is not tuple or any(
            type(policy) is not TrustedGroupReservationPolicy
            for policy in trusted_group_reservation_policies
        ):
            raise TypeError(
                "trusted_group_reservation_policies must be a tuple of trusted policies"
            )
        if type(trusted_group_resolution_policies) is not tuple or any(
            type(policy) is not TrustedGroupResolutionPolicy
            for policy in trusted_group_resolution_policies
        ):
            raise TypeError("trusted_group_resolution_policies must be a tuple of trusted policies")
        if type(trusted_paper_owner_write_policies) is not tuple or any(
            type(policy) is not TrustedPaperOwnerWritePolicy
            for policy in trusted_paper_owner_write_policies
        ):
            raise TypeError("trusted_paper_owner_write_policies must be a tuple of trusted policies")
        if type(trusted_paper_legacy_import_policies) is not tuple or any(
            type(policy) is not TrustedPaperLegacyImportPolicy
            for policy in trusted_paper_legacy_import_policies
        ):
            raise TypeError(
                "trusted_paper_legacy_import_policies must be a tuple of trusted policies"
            )
        policy_keys: set[tuple[object, ...]] = set()
        logical_runs: dict[tuple[str, str, str], str] = {}
        policy_routes: list[tuple[object, frozenset[str], frozenset[str], str]] = [
            (policy, frozenset({policy.operation}), frozenset({policy.operation}), "write")
            for policy in trusted_group_write_policies
        ]
        policy_routes.extend(
            (
                policy,
                policy.operations,
                policy.write_operations,
                "reservation",
            )
            for policy in trusted_group_reservation_policies
        )
        policy_routes.extend(
            (
                policy,
                frozenset({policy.operation}),
                frozenset({policy.operation}),
                "resolution",
            )
            for policy in trusted_group_resolution_policies
        )
        for policy, operations, write_operations, policy_kind in policy_routes:
            instance_id = policy.instance_id  # type: ignore[attr-defined]
            port = policy.port  # type: ignore[attr-defined]
            operation_tuple = tuple(sorted(operations))
            key = (
                policy_kind,
                instance_id,
                port,
                operation_tuple,
                policy.principal_ref,  # type: ignore[attr-defined]
                policy.identity,  # type: ignore[attr-defined]
                policy.scope_ref,  # type: ignore[attr-defined]
                policy.scope,  # type: ignore[attr-defined]
            )
            if key in policy_keys:
                raise ValueError("duplicate trusted group route policy")
            policy_keys.add(key)
            capability = capability_map.get((instance_id, port))
            if (
                capability is None
                or not operations.issubset(capability.operations)
                or not write_operations.issubset(capability.write_operations)
                or operations.intersection(capability.write_operations) != write_operations
            ):
                raise ValueError("trusted group policy is outside the local capability")
            matching_grants = [
                resource
                for principal_grant in trusted_grants.get(
                    policy.principal_ref,
                    (),  # type: ignore[attr-defined]
                )
                if principal_grant.identity == policy.identity  # type: ignore[attr-defined]
                for resource in principal_grant.resources
                if resource.instance_id == instance_id
                and resource.port == port
                and resource.scope_ref == policy.scope_ref  # type: ignore[attr-defined]
                and resource.scope == policy.scope  # type: ignore[attr-defined]
                and operations.issubset(resource.operations)
            ]
            if len(matching_grants) != 1:
                raise ValueError("trusted group policy does not match one exact grant")
            policy_scope = policy.scope  # type: ignore[attr-defined]
            account_ref = (
                policy_scope.account_ref if policy_kind == "write" else policy.account_ref  # type: ignore[attr-defined]
            )
            logical_key = (
                policy_scope.execution_domain_ref,
                policy_scope.strategy_ref,
                account_ref,
            )
            policy_run_id = policy.run_id  # type: ignore[attr-defined]
            prior_run = logical_runs.setdefault(logical_key, policy_run_id)
            if prior_run != policy_run_id:
                raise ValueError("conflicting trusted group logical runs")

        paper_policy_keys: set[tuple[object, ...]] = set()
        for policy in trusted_paper_owner_write_policies:
            key = (
                policy.instance_id,
                policy.principal_ref,
                policy.identity,
                policy.scope_ref,
                policy.scope,
            )
            if key in paper_policy_keys:
                raise ValueError("duplicate trusted paper owner policy")
            paper_policy_keys.add(key)
            capability = capability_map.get((policy.instance_id, policy.port))
            if (
                capability is None
                or policy.operation not in capability.operations
                or policy.operation not in capability.write_operations
            ):
                raise ValueError("trusted paper owner policy is outside the local capability")
            matching_grants = [
                resource
                for principal_grant in trusted_grants.get(policy.principal_ref, ())
                if principal_grant.identity == policy.identity
                for resource in principal_grant.resources
                if resource.instance_id == policy.instance_id
                and resource.port == policy.port
                and resource.scope_ref == policy.scope_ref
                and resource.scope == policy.scope
                and resource.operations == frozenset({policy.operation})
            ]
            if len(matching_grants) != 1:
                raise ValueError("trusted paper owner policy does not match one exact grant")

        paper_legacy_import_policy_keys: set[tuple[object, ...]] = set()
        for policy in trusted_paper_legacy_import_policies:
            key = (
                policy.instance_id,
                policy.principal_ref,
                policy.identity,
                policy.scope_ref,
                policy.scope,
            )
            if key in paper_legacy_import_policy_keys:
                raise ValueError("duplicate trusted paper legacy import policy")
            paper_legacy_import_policy_keys.add(key)
            capability = capability_map.get((policy.instance_id, policy.port))
            if (
                capability is None
                or capability.operations != policy.operations
                or capability.write_operations != policy.write_operations
            ):
                raise ValueError("trusted paper legacy import policy is outside the capability")
            matching_grants = [
                resource
                for principal_grant in trusted_grants.get(policy.principal_ref, ())
                if principal_grant.identity == policy.identity
                for resource in principal_grant.resources
                if resource.instance_id == policy.instance_id
                and resource.port == policy.port
                and resource.scope_ref == policy.scope_ref
                and resource.scope == policy.scope
                and resource.operations == policy.operations
            ]
            if len(matching_grants) != 1:
                raise ValueError("trusted paper legacy import policy does not match one exact grant")

        self.service_id = service_id
        self.policy_version = policy_version
        self.requirements_version = requirements_version
        self.access_policy_ref = effective_access_policy_ref
        self.requirements_ref = effective_requirements_ref
        self._account_risk_policy_bindings = tuple(account_risk_policy_bindings)
        self._account_risk_policy_required_routes = account_risk_policy_required_routes
        self.lease_seconds = float(lease_seconds)
        self.max_sessions = max_sessions
        self.max_registration_keys = max_registration_keys
        self._clock = clock
        self._reply_size_checker = reply_size_checker
        self._migration_deployment_epoch = migration_deployment_epoch
        self._migration_writes_enabled = True
        self._migration_write_enablement_sha256: str | None = None
        self._capabilities = capability_map
        self._grants = trusted_grants
        self._trusted_group_write_policies = trusted_group_write_policies
        self._trusted_group_reservation_policies = trusted_group_reservation_policies
        self._trusted_group_resolution_policies = trusted_group_resolution_policies
        self._trusted_paper_owner_write_policies = trusted_paper_owner_write_policies
        self._trusted_paper_legacy_import_policies = trusted_paper_legacy_import_policies
        self._handlers: dict[
            tuple[str, str], Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]]
        ] = {}
        self._ready = ready
        self._lock = threading.RLock()
        self._closed = False
        self._epoch = uuid.uuid4().hex
        db_path = str(path)
        self._migration_state_path = None if db_path == ":memory:" else Path(db_path)
        if self._migration_state_path is None and migration_deployment_epoch is not None:
            raise MigrationGateError(_MIGRATION_WRITE_ENABLEMENT_INVALID)
        owned_migration_guard = None
        marker = None
        self._db: sqlite3.Connection | None = None
        self._owner_key: str | None = None
        self._owner_file: Any = None
        try:
            migration_snapshot = migration_start_guard
            if self._migration_state_path is not None:
                if migration_snapshot is None:
                    owned_migration_guard = acquire_migration_start_guard(
                        self._migration_state_path,
                        deployment_epoch=migration_deployment_epoch,
                    )
                    try:
                        migration_snapshot = owned_migration_guard.__enter__()
                    except BaseException:
                        owned_migration_guard = None
                        raise
                marker = migration_snapshot.require_active(
                    self._migration_state_path,
                    migration_deployment_epoch,
                )
            elif migration_snapshot is not None:
                marker = migration_snapshot.require_active(path, migration_deployment_epoch)
                if marker is not None:
                    raise MigrationGateError(_MIGRATION_WRITE_ENABLEMENT_INVALID)
            if marker is not None and marker.status == STATUS_COMMITTED:
                # The destination can start and serve reads, but no business
                # writer or persistent background task may proceed until the
                # operator explicitly enables it after target-side checks.
                self._migration_writes_enabled = False
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
            if owned_migration_guard is not None:
                guard_to_exit = owned_migration_guard
                owned_migration_guard = None
                guard_to_exit.__exit__(None, None, None)
        except BaseException as startup_error:
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
            if owned_migration_guard is not None:
                guard_to_exit = owned_migration_guard
                owned_migration_guard = None
                try:
                    guard_to_exit.__exit__(
                        type(startup_error), startup_error, startup_error.__traceback__
                    )
                except BaseException:
                    cleanup_failed = True
            if cleanup_failed:
                raise ServiceRegistryStartupCleanupError(self) from None
            self._closed = True
            raise

    @property
    def service_epoch(self) -> str:
        return self._epoch

    def _record_migration_write_attempt(self, *, fact_kind: str) -> bool:
        """Seal before an authorized write, bound to the deployment epoch."""

        if not self._migration_writes_enabled:
            raise MigrationGateError(_MIGRATION_WRITE_DISABLED)
        state_path = self._migration_state_path
        if state_path is None:
            return False
        return record_migration_write_attempt(
            state_path,
            deployment_epoch=self._migration_deployment_epoch,
            fact_kind=fact_kind,
        )

    @property
    def migration_writes_enabled(self) -> bool:
        """Whether this process has passed the explicit MIG03 write enable."""

        with self._lock:
            return self._migration_writes_enabled

    def enable_migration_writes(
        self,
        *,
        migration_id: str,
        deployment_epoch: str,
        readiness_sha256: str,
    ) -> None:
        """Enable a committed destination only after operator readback checks.

        The readiness digest binds the operator's checked read/projection,
        policy and local readiness evidence. This method verifies that the
        active durable marker is still the same unsealed destination and that
        this Registry is ready; it does not treat an arbitrary reference as
        proof of external fencing.
        """

        if (
            type(migration_id) is not str
            or not migration_id
            or type(deployment_epoch) is not str
            or not deployment_epoch
            or type(readiness_sha256) is not str
            or len(readiness_sha256) != 64
            or any(character not in "0123456789abcdef" for character in readiness_sha256)
        ):
            raise MigrationGateError(_MIGRATION_WRITE_ENABLEMENT_INVALID)
        with self._lock:
            self._ensure_open()
            if (
                not self._ready
                or self._migration_state_path is None
                or deployment_epoch != self._migration_deployment_epoch
            ):
                raise MigrationGateError(_MIGRATION_WRITE_ENABLEMENT_INVALID)
            try:
                marker = enable_migration_cutover(
                    self._migration_state_path,
                    migration_id=migration_id,
                    deployment_epoch=deployment_epoch,
                )
            except MigrationGateError:
                raise
            if marker.status != STATUS_ENABLED:
                raise MigrationGateError(_MIGRATION_WRITE_ENABLEMENT_INVALID)
            self._migration_writes_enabled = True
            self._migration_write_enablement_sha256 = readiness_sha256

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
                expires_at REAL NOT NULL,
                access_policy_ref TEXT NOT NULL DEFAULT '',
                requirements_ref TEXT NOT NULL DEFAULT '',
                risk_policy_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                risk_policy_ref TEXT,
                risk_policy_version TEXT
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
        session_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(sessions)")
        }
        for name, declaration in (
            ("access_policy_ref", "TEXT NOT NULL DEFAULT ''"),
            ("requirements_ref", "TEXT NOT NULL DEFAULT ''"),
            ("risk_policy_status", "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'"),
            ("risk_policy_ref", "TEXT"),
            ("risk_policy_version", "TEXT"),
        ):
            if name not in session_columns:
                self._db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {declaration}")

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

    @contextmanager
    def _read_snapshot(self) -> Iterator[None]:
        """Hold one SQLite read snapshot without expiring or deleting leases."""

        with self._lock:
            if self._closed or self._db is None:
                raise sqlite3.ProgrammingError("registry is closed")
            self._db.execute("BEGIN")
            try:
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

    def active_registration_ids(self, *, instance_id: str) -> frozenset[str]:
        """Return this instance's current leases for local resource maintenance.

        This is an owner-side API, never a remotely callable registry operation.
        Expiration uses the registry's authoritative clock and transaction even
        when no client is sending requests. Readiness does not revoke a lease;
        unregister, expiry, epoch and policy changes do. A read failure must
        propagate rather than masquerade as an empty set and release resources.
        """

        if not _text(instance_id) or not any(
            capability.instance_id == instance_id for capability in self._capabilities.values()
        ):
            raise ValueError("instance_id is not a declared capability")
        with self._transaction():
            self._expire_sessions(self._now())
            rows = self._db.execute(
                "SELECT * FROM sessions "
                "WHERE instance_id=? AND service_epoch=? "
                "AND policy_version=? AND requirements_version=?",
                (instance_id, self._epoch, self.policy_version, self.requirements_version),
            ).fetchall()
            return frozenset(
                row["registration_id"]
                for row in rows
                if self._registration_policy_matches(row)
            )

    def active_registration_contexts(
        self, *, instance_id: str, port: str
    ) -> tuple[ActiveRegistrationContext, ...]:
        """Project eligible registrations from one read-only SQLite snapshot.

        This is an owner-thread lifecycle input, not a remotely callable
        operation. It deliberately does not require a separate component
        authority generation to be active: the receiving component uses this
        eligibility snapshot to activate or reconcile that generation.
        Expired and stale-version rows are omitted without deleting them.
        """

        if not _text(instance_id) or not _text(port):
            raise ValueError("registration capability is invalid")
        if (instance_id, port) not in self._capabilities:
            raise ValueError("instance_id and port are not a declared capability")
        now = self._now()
        result: list[ActiveRegistrationContext] = []
        with self._read_snapshot():
            rows = self._db.execute(
                "SELECT * FROM sessions WHERE instance_id=? AND port=? "
                "AND service_epoch=? AND policy_version=? AND requirements_version=? "
                "AND expires_at>? ORDER BY registration_id",
                (
                    instance_id,
                    port,
                    self._epoch,
                    self.policy_version,
                    self.requirements_version,
                    now,
                ),
            ).fetchall()
            for row in rows:
                identity = self._stored_identity(row["identity_json"])
                scope = self._stored_scope(row["scope_json"])
                principal = row["principal"]
                operations = frozenset(json.loads(row["operations_json"]))
                write_operations = frozenset(json.loads(row["write_operations_json"]))
                if not self._identity_is_trusted(principal, identity):
                    continue
                if not self._registration_policy_matches(row):
                    continue
                if not operations or not write_operations.issubset(operations):
                    continue
                if any(
                    not self._current_resource_allows(
                        principal,
                        identity,
                        instance_id=instance_id,
                        port=port,
                        scope_ref=row["scope_ref"],
                        scope=scope,
                        operation=operation,
                    )
                    for operation in operations
                ):
                    continue
                run_id = self._scope_strategy_write_run_id_in_snapshot(scope)
                result.append(
                    ActiveRegistrationContext(
                        context=ServiceCallContext(
                            identity=identity,
                            scope=scope,
                            registration_id=row["registration_id"],
                            principal_ref=principal,
                        ),
                        instance_id=instance_id,
                        port=port,
                        scope_ref=row["scope_ref"],
                        operations=operations,
                        write_operations=write_operations,
                        service_epoch=row["service_epoch"],
                        policy_version=row["policy_version"],
                        requirements_version=row["requirements_version"],
                        expires_at=float(row["expires_at"]),
                        strategy_write_run_id=run_id,
                    )
                )
        return tuple(result)

    def _scope_strategy_write_run_id_in_snapshot(self, scope: WireScope) -> str | None:
        """Return one exact durable run shared by every account in the scope.

        Group coordinators can be registered for a fixed account vector. Their
        lifecycle context must prove the same persistent logical run on every
        account before an owner may activate a group route. Missing or
        conflicting claims stay ineligible.
        """

        if scope.strategy_ref is None:
            return None
        if scope.account_ref is not None and not scope.account_refs:
            return self._strategy_write_run_id_in_snapshot(
                scope.execution_domain_ref,
                scope.strategy_ref,
                scope.account_ref,
            )
        if scope.account_ref is not None or not scope.account_refs or scope.run_id is None:
            return None
        account_refs = scope.account_refs
        run_ids = tuple(
            self._strategy_write_run_id_in_snapshot(
                scope.execution_domain_ref,
                scope.strategy_ref,
                account_ref,
            )
            for account_ref in account_refs
        )
        if any(run_id is None for run_id in run_ids):
            return None
        first = run_ids[0]
        if first != scope.run_id or any(run_id != first for run_id in run_ids[1:]):
            return None
        return first

    def _strategy_write_run_id_in_snapshot(
        self,
        execution_domain_ref: str | None,
        strategy_ref: str | None,
        account_ref: str | None,
    ) -> str | None:
        if execution_domain_ref is None or strategy_ref is None or account_ref is None:
            return None
        row = self._db.execute(
            "SELECT run_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            (execution_domain_ref, strategy_ref, account_ref),
        ).fetchone()
        return None if row is None else row["run_id"]

    def strategy_write_run_id(
        self, execution_domain_ref: str, strategy_ref: str, account_ref: str
    ) -> str | None:
        """Read the persistent logical strategy run without requiring a lease."""

        refs = (execution_domain_ref, strategy_ref, account_ref)
        if any(not _text(value) for value in refs):
            raise ValueError("strategy claim identifiers are invalid")
        with self._read_snapshot():
            return self._strategy_write_run_id_in_snapshot(*refs)

    def bind_trusted_group_run(
        self,
        execution_domain_ref: str,
        strategy_ref: str,
        account_ref: str,
        *,
        registration_id: str,
    ) -> str:
        """Bind one active, exact group-owner registration to its trusted run.

        Only an eligible registration with the exact operator policy and its
        corresponding writable operation may seed the logical run. Group
        reservation owners seed on ``reservations.reserve``; account owners
        seed on ``orders.submit_group_leg``. Query/release registrations,
        expired rows, and caller-selected runs cannot seed a claim. Repeating
        the same mapping is idempotent; a different run is rejected. This
        never sets or modifies ``active_registration_id``.
        """

        refs = (execution_domain_ref, strategy_ref, account_ref, registration_id)
        if any(not _text(value) for value in refs):
            raise ValueError("strategy claim identifiers are invalid")
        with self._transaction():
            now = self._now()
            registration = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND service_epoch=? "
                "AND policy_version=? AND requirements_version=? AND expires_at>?",
                (
                    registration_id,
                    self._epoch,
                    self.policy_version,
                    self.requirements_version,
                    now,
                ),
            ).fetchone()
            if registration is None or not self._registration_policy_matches(registration):
                raise ServiceRegistryGroupWritePolicyError
            identity = self._stored_identity(registration["identity_json"])
            scope = self._stored_scope(registration["scope_json"])
            operations = frozenset(json.loads(registration["operations_json"]))
            write_operations = frozenset(json.loads(registration["write_operations_json"]))
            principal = registration["principal"]
            operation = (
                "orders.submit_group_leg"
                if registration["port"] == "service.order_entry"
                else "reservations.reserve"
                if registration["port"] == "service.group_reservations"
                else None
            )
            policy = (
                None
                if operation is None
                else self._trusted_group_policy(
                    principal,
                    identity,
                    instance_id=registration["instance_id"],
                    port=registration["port"],
                    scope_ref=registration["scope_ref"],
                    scope=scope,
                    operation=operation,
                )
            )
            policy_account_ref = (
                policy.scope.account_ref
                if type(policy) is TrustedGroupWritePolicy
                else policy.account_ref
                if type(policy) is TrustedGroupReservationPolicy
                else None
            )
            capability = self._capabilities.get((registration["instance_id"], registration["port"]))
            if (
                operation is None
                or policy is None
                or scope.execution_domain_ref != execution_domain_ref
                or scope.strategy_ref != strategy_ref
                or policy_account_ref != account_ref
                or principal != policy.principal_ref
                or identity != policy.identity
                or not self._identity_is_trusted(principal, identity)
                or capability is None
                or operation not in operations
                or operation not in write_operations
                or operation not in capability.write_operations
                or not self._current_resource_allows(
                    principal,
                    identity,
                    instance_id=registration["instance_id"],
                    port=registration["port"],
                    scope_ref=registration["scope_ref"],
                    scope=scope,
                    operation=operation,
                )
            ):
                raise ServiceRegistryGroupWritePolicyError
            run_id = policy.run_id
            row = self._db.execute(
                "SELECT run_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
                (execution_domain_ref, strategy_ref, account_ref),
            ).fetchone()
            if row is not None:
                if row["run_id"] != run_id:
                    raise ServiceRegistryStrategyRunConflictError
                return run_id
            self._db.execute(
                "INSERT INTO strategy_write_claims "
                "(execution_domain_ref, strategy_ref, account_ref, run_id, created_at, active_registration_id) "
                "VALUES(?,?,?,?,?,NULL)",
                (execution_domain_ref, strategy_ref, account_ref, run_id, self._now()),
            )
        return run_id

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
    def _failure_with_result(code: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": False, "code": code, "result": dict(result)}

    @staticmethod
    def _payload_fields(
        request: WireEnvelope, expected: frozenset[str]
    ) -> Mapping[str, Any] | None:
        payload = request.payload
        if not isinstance(payload, Mapping) or frozenset(payload.keys()) != expected:
            return None
        return payload

    def _account_risk_receipt_for(
        self,
        instance_id: str,
        port: str,
        scope: WireScope,
        identity: WireIdentity,
    ) -> tuple[str, str | None, str | None] | None:
        bindings = tuple(
            binding
            for binding in self._account_risk_policy_bindings
            if binding.instance_id == instance_id
            and port == "service.order_entry"
            and self._scope_matches(binding.scope, scope, identity)
        )
        if len(bindings) > 1:
            return None
        if not bindings:
            if (instance_id, port) in self._account_risk_policy_required_routes:
                return None
            return "NOT_APPLICABLE", None, None
        binding = bindings[0]
        return "APPLIED", binding.policy_ref, binding.policy_version

    def _registration_policy_matches(self, row: sqlite3.Row | Mapping[str, Any]) -> bool:
        if (
            row["policy_version"] != self.policy_version
            or row["requirements_version"] != self.requirements_version
            or row["access_policy_ref"] != self.access_policy_ref
            or row["requirements_ref"] != self.requirements_ref
        ):
            return False
        identity = self._stored_identity(row["identity_json"])
        scope = self._stored_scope(row["scope_json"])
        expected = self._account_risk_receipt_for(
            row["instance_id"], row["port"], scope, identity
        )
        if expected is None:
            return False
        return (
            row["risk_policy_status"],
            row["risk_policy_ref"],
            row["risk_policy_version"],
        ) == expected

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
                        **(
                            {"market_wire_version": capability.market_wire_version}
                            if capability.port == "service.market_source"
                            and capability.market_wire_version is not None
                            else {}
                        ),
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

    def _trusted_group_write_policy(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> TrustedGroupWritePolicy | None:
        if identity.role is not WireRole.SERVICE:
            return None
        for policy in self._trusted_group_write_policies:
            if (
                policy.principal_ref == principal
                and policy.identity == identity
                and policy.instance_id == instance_id
                and policy.port == port
                and policy.scope_ref == scope_ref
                and policy.scope == scope
                and policy.operation == operation
            ):
                return policy
        return None

    def _trusted_group_reservation_policy(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> TrustedGroupReservationPolicy | None:
        if identity.role is not WireRole.SERVICE or port != "service.group_reservations":
            return None
        for policy in self._trusted_group_reservation_policies:
            if (
                policy.principal_ref == principal
                and policy.identity == identity
                and policy.instance_id == instance_id
                and policy.port == port
                and policy.scope_ref == scope_ref
                and policy.scope == scope
                and operation in policy.operations
            ):
                return policy
        return None

    def _trusted_group_resolution_policy(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> TrustedGroupResolutionPolicy | None:
        if identity.role is not WireRole.SERVICE:
            return None
        for policy in self._trusted_group_resolution_policies:
            if (
                policy.principal_ref == principal
                and policy.identity == identity
                and policy.instance_id == instance_id
                and policy.port == port
                and policy.scope_ref == scope_ref
                and policy.scope == scope
                and policy.operation == operation
            ):
                return policy
        return None

    def _trusted_group_policy(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> (
        TrustedGroupWritePolicy
        | TrustedGroupReservationPolicy
        | TrustedGroupResolutionPolicy
        | None
    ):
        return (
            self._trusted_group_write_policy(
                principal,
                identity,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            )
            or self._trusted_group_reservation_policy(
                principal,
                identity,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            )
            or self._trusted_group_resolution_policy(
                principal,
                identity,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            )
        )

    @staticmethod
    def _is_approved_target_write(
        identity: WireIdentity, *, port: str, operation: str
    ) -> bool:
        """Recognize only the code-owned application approved-target route.

        This predicate is only one part of authorization. The caller must also
        enforce the exact trusted PrincipalGrant and ResourceGrant, including
        principal, identity, instance, scope_ref, WireScope and operation.
        ServiceHost only exposes this capability when its code-owned Execution
        adapter is wired with an approved-target authority. The Execution
        operation still validates and consumes that authority; this predicate
        never authorizes ordinary strategy writes.
        """

        return identity.role is WireRole.APPLICATION_CLIENT and (
            port,
            operation,
        ) == _APPROVED_TARGET_OPERATION

    def _trusted_paper_owner_write(
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
        if identity.role is not WireRole.APPLICATION_CLIENT or (
            port,
            operation,
        ) != _PAPER_OWNER_OPERATION:
            return False
        return any(
            policy.principal_ref == principal
            and policy.identity == identity
            and policy.instance_id == instance_id
            and policy.scope_ref == scope_ref
            and policy.scope == scope
            for policy in self._trusted_paper_owner_write_policies
        )

    def _trusted_paper_legacy_import_write(
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
        if identity.role is not WireRole.APPLICATION_CLIENT or port != _PAPER_LEGACY_IMPORT_PORT:
            return False
        return any(
            policy.principal_ref == principal
            and policy.identity == identity
            and policy.instance_id == instance_id
            and policy.port == port
            and policy.scope_ref == scope_ref
            and policy.scope == scope
            and operation in policy.write_operations
            for policy in self._trusted_paper_legacy_import_policies
        )

    def _trusted_group_write_run_matches(
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
        policy = self._trusted_group_policy(
            principal,
            identity,
            instance_id=instance_id,
            port=port,
            scope_ref=scope_ref,
            scope=scope,
            operation=operation,
        )
        if policy is None:
            return False
        account_ref = (
            policy.scope.account_ref
            if type(policy) is TrustedGroupWritePolicy
            else policy.account_ref
        )
        return (
            self._strategy_write_run_id_in_snapshot(
                scope.execution_domain_ref, scope.strategy_ref, account_ref
            )
            == policy.run_id
        )

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
        if (
            identity.role is WireRole.SERVICE
            and port == "service.group_reservations"
            and any(
                self._trusted_group_reservation_policy(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=scope_ref,
                    scope=scope,
                    operation=operation,
                )
                is None
                for operation in requested_operations
            )
        ):
            return "SCOPE_DENIED", capability, frozenset()
        granted_write_operations = granted & capability.write_operations
        if granted_write_operations:
            if identity.role is WireRole.STRATEGY_CLIENT:
                # Strategy writes are admitted below only when the concrete
                # scope supplies both strategy and run identifiers.
                if scope.strategy_ref is None or scope.run_id is None:
                    return "SCOPE_DENIED", capability, frozenset()
            elif scope.strategy_ref is not None:
                # Service group writes require their exact operator policy.
                # The separately code-owned approved-target application route
                # is admitted only by its exact trusted ResourceGrant; its
                # Execution adapter rechecks the approval authority at dispatch.
                if any(
                    not _is_exact_owner_cancel_scope(
                        identity, port=port, scope=scope, operation=operation
                    )
                    and not self._is_approved_target_write(
                        identity, port=port, operation=operation
                    )
                    and self._trusted_group_policy(
                        principal,
                        identity,
                        instance_id=instance_id,
                        port=port,
                        scope_ref=scope_ref,
                        scope=scope,
                        operation=operation,
                    ) is None
                    and not self._trusted_paper_owner_write(
                        principal,
                        identity,
                        instance_id=instance_id,
                        port=port,
                        scope_ref=scope_ref,
                        scope=scope,
                        operation=operation,
                    )
                    and not self._trusted_paper_legacy_import_write(
                        principal,
                        identity,
                        instance_id=instance_id,
                        port=port,
                        scope_ref=scope_ref,
                        scope=scope,
                        operation=operation,
                    )
                    for operation in granted_write_operations
                ):
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
        risk_receipt = self._account_risk_receipt_for(
            instance_id, port, request.header.scope, request.header.identity
        )
        if risk_receipt is None:
            return self._failure("POLICY_REQUIREMENT_UNMET")
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
                    if (
                        active["policy_version"] != self.policy_version
                        or active["requirements_version"] != self.requirements_version
                        or not self._registration_policy_matches(active)
                    ):
                        return self._failure("POLICY_REQUIREMENT_UNMET")
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
                        if isinstance(claim_error, tuple):
                            return self._failure_with_result(claim_error[0], claim_error[1])
                        return self._failure(claim_error)

            identity_json = json.dumps(
                self._identity_dict(request.header.identity), sort_keys=True, separators=(",", ":")
            )
            scope_json = json.dumps(
                self._scope_dict(request.header.scope), sort_keys=True, separators=(",", ":")
            )
            self._db.execute(
                "INSERT INTO sessions (registration_id, principal, registration_key, request_digest, "
                "identity_json, instance_id, port, scope_ref, scope_json, operations_json, "
                "write_operations_json, policy_version, requirements_version, service_epoch, expires_at, "
                "access_policy_ref, requirements_ref, risk_policy_status, risk_policy_ref, "
                "risk_policy_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    self.access_policy_ref,
                    self.requirements_ref,
                    *risk_receipt,
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
    ) -> str | tuple[str, Mapping[str, Any]] | None:
        if scope.strategy_ref is None or scope.run_id is None:
            return "SCOPE_DENIED"
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return "SCOPE_DENIED"
        rows = tuple(
            self._db.execute(
                "SELECT run_id, active_registration_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, account_ref),
            ).fetchone()
            for account_ref in accounts
        )
        if any(row is not None and row["active_registration_id"] is not None for row in rows):
            return "DUPLICATE_ACTIVE_RUN"
        durable_runs = {row["run_id"] for row in rows if row is not None}
        if len(durable_runs) > 1:
            return "DUPLICATE_ACTIVE_RUN"
        if durable_runs and scope.run_id not in durable_runs:
            # Every claim for this exact scope is released: the previous
            # session unregistered, its lease expired, or its process died
            # with an earlier service epoch. All released claims name one
            # other logical run; report it so a fresh client session can
            # adopt that durable run instead of minting a competing one.
            # Registration still requires the exact matching run id, so two
            # simultaneously-live runs can never share the scope, and active
            # or conflicting claim vectors stay a plain refusal.
            return (
                "DUPLICATE_ACTIVE_RUN",
                {"strategy_write_run_id": next(iter(durable_runs))},
            )
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
            "access_policy_ref": row["access_policy_ref"],
            "access_policy_version": row["policy_version"],
            "risk_policy_status": row["risk_policy_status"],
            "risk_policy_ref": row["risk_policy_ref"],
            "risk_policy_version": row["risk_policy_version"],
            "requirements_ref": row["requirements_ref"],
            # Compatibility alias for clients which still consume access policy
            # version under its original wire name.
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
                    or not self._registration_policy_matches(row)
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
        payload = request.payload
        required_fields = {"registration_id", "service_epoch", "instance_id", "arguments"}
        allowed_fields = required_fields | {"max_reply_message_bytes"}
        if (
            not isinstance(payload, Mapping)
            or set(payload) not in (required_fields, allowed_fields)
            or not _text(payload["registration_id"])
            or not _text(payload["service_epoch"])
            or not _text(payload["instance_id"])
            or not isinstance(payload["arguments"], Mapping)
        ):
            return self._failure("INVALID_REQUEST")
        reply_limit = payload.get("max_reply_message_bytes")
        if "max_reply_message_bytes" in payload and (
            type(reply_limit) is not int or not 1 <= reply_limit <= MAX_WIRE_MESSAGE_BYTES
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
                or not self._registration_policy_matches(row)
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
            if reply_limit is not None and self._reply_size_checker is None:
                return self._failure("CAPABILITY_UNAVAILABLE")
            # Do not dispatch a write operation unless the durable strategy
            # claim still matches the exact run bound to this registration.
            if (
                operation in write_ops
                and identity.role is WireRole.STRATEGY_CLIENT
                and not self._strategy_claim_exists(scope, row["registration_id"])
            ):
                return self._failure("DUPLICATE_ACTIVE_RUN")
            if (
                identity.role is WireRole.SERVICE
                and scope.strategy_ref is not None
                and (operation in write_ops or row["port"] == "service.group_reservations")
            ):
                if _is_exact_owner_cancel_scope(
                    identity, port=row["port"], scope=scope, operation=operation
                ):
                    if not self._strategy_owner_run_claim_exists(scope):
                        return self._failure("DUPLICATE_ACTIVE_RUN")
                elif not self._trusted_group_write_run_matches(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=row["port"],
                    scope_ref=row["scope_ref"],
                    scope=scope,
                    operation=operation,
                ):
                    return self._failure("DUPLICATE_ACTIVE_RUN")

            def result_fits(result: Mapping[str, Any]) -> bool:
                normalized_result = self._normalize_result(result)
                encoded_result = json.dumps(
                    normalized_result,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8", errors="strict")
                if len(encoded_result) > _MAX_RESULT_BYTES:
                    return False
                checker = self._reply_size_checker
                if checker is None:
                    return True
                complete_payload = {"ok": True, "code": "OK", "result": normalized_result}
                fits = checker(request, complete_payload, reply_limit)
                if type(fits) is not bool:
                    raise TypeError("reply size checker must return bool")
                return fits

            context = ServiceCallContext(
                identity,
                scope,
                row["registration_id"],
                principal,
                result_fits,
            )
            arguments = MappingProxyType(dict(payload["arguments"]))
        write_fact_kind = None
        if operation in write_ops:
            write_fact_kind = "dispatch"
        elif capability.port == "service.account_read" and (
            operation == "account.ack"
            or (
                operation == "account.read_after"
                and arguments.get("track_delivery", False) is True
            )
        ):
            # Tracked reads durably advance offered_cursor just like an ACK is
            # a durable cursor transition. Fence both before entering Gateway.
            write_fact_kind = "cursor"
        elif capability.port == "service.risk_query" and operation == "risk.snapshot":
            # The public Risk snapshot runs policy initialization and expiry
            # updates in its SQLite transaction, despite being a query route.
            write_fact_kind = "reservation"
        if write_fact_kind is not None:
            try:
                self._record_migration_write_attempt(fact_kind=write_fact_kind)
            except MigrationGateError as error:
                if error.code == _MIGRATION_WRITE_DISABLED:
                    return self._failure(_MIGRATION_WRITE_DISABLED)
                # Marker/epoch/lock uncertainty must prevent handler entry.
                return self._failure("UNAVAILABLE")
            except OSError:
                # Marker/epoch/lock uncertainty must prevent handler entry.
                return self._failure("UNAVAILABLE")
        try:
            result = handler(context, arguments)
            if not isinstance(result, Mapping):
                return self._failure("HANDLER_FAILED")
            if not result_fits(result):
                # The handler may already have committed an external effect.
                # A final reply-size failure cannot prove that retry is safe.
                return self._failure("HANDLER_FAILED")
            return self._success(self._normalize_result(result))
        except ServiceResultTooLargeError as error:
            if (
                type(error) is ServiceResultTooLargeError
                and (capability.port, operation)
                in {
                    ("service.account_read", "account.read_after"),
                    ("service.monitor_query", "telemetry.snapshot"),
                }
                and operation not in write_ops
            ):
                return self._failure("REPLY_TOO_LARGE")
            return self._failure("HANDLER_FAILED")
        except Exception as error:
            if type(error) is ServiceOperationDeniedError and (capability.port, operation) in {
                ("service.market_source", "market.subscribe"),
                ("service.monitor_control", "monitor.control"),
                ("service.monitor_control", "monitor.control_status"),
            }:
                return self._failure("SCOPE_DENIED")
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
        if (
            identity.role is WireRole.SERVICE
            and port == "service.group_reservations"
            and (
                self._trusted_group_reservation_policy(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=scope_ref,
                    scope=scope,
                    operation=operation,
                )
                is None
            )
        ):
            return False
        if (
            identity.role is WireRole.SERVICE
            and port == "service.order_entry"
            and operation == "orders.resolve_group_leg"
            and self._trusted_group_resolution_policy(
                principal,
                identity,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            )
            is None
        ):
            return False
        if operation in capability.write_operations and not _is_exact_owner_cancel_scope(
            identity, port=port, scope=scope, operation=operation
        ):
            if identity.role is WireRole.STRATEGY_CLIENT:
                if scope.strategy_ref is None or scope.run_id is None:
                    return False
            elif scope.strategy_ref is not None:
                if not self._is_approved_target_write(
                    identity, port=port, operation=operation
                ) and self._trusted_group_policy(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=scope_ref,
                    scope=scope,
                    operation=operation,
                ) is None and not self._trusted_paper_owner_write(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=scope_ref,
                    scope=scope,
                    operation=operation,
                ) and not self._trusted_paper_legacy_import_write(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=scope_ref,
                    scope=scope,
                    operation=operation,
                ):
                    return False
            elif (identity.role is WireRole.SERVICE and scope.portfolio_ref is not None) or (
                scope.manual_book_ref is None and scope.portfolio_ref is None
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

    def _strategy_owner_run_claim_exists(self, scope: WireScope) -> bool:
        """Require a live durable strategy/account/run claim for owner.cancel."""

        if (
            scope.account_ref is None
            or scope.account_refs
            or scope.strategy_ref is None
            or scope.run_id is None
        ):
            return False
        row = self._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref),
        ).fetchone()
        return bool(
            row is not None
            and row["run_id"] == scope.run_id
            and _text(row["active_registration_id"])
        )
