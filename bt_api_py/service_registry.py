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
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
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
    encode_wire_value,
    validate_request,
    wire_contract_digest,
)

from .service_migration_gate import (
    STATUS_COMMITTED,
    STATUS_ENABLED,
    MigrationGateError,
    MigrationStartSnapshot,
    enable_migration_cutover,
    record_migration_write_attempt,
)
from .service_migration_gate import (
    migration_start_guard as acquire_migration_start_guard,
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
_MAX_STOP_NEW_RECEIPTS = 10_000
_MIGRATION_WRITE_DISABLED = "MIGRATION_WRITE_DISABLED"
_MIGRATION_WRITE_ENABLEMENT_INVALID = "MIGRATION_WRITE_ENABLEMENT_INVALID"
_APPROVED_TARGET_OPERATION = ("service.order_entry", "orders.submit_approved_target")
_RECOVERY_CLOSE_OPERATION = ("service.order_entry", "orders.submit_recovery_close")
_OWNER_CANCEL_OPERATION = ("service.owner_control", "owner.cancel")
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
_GATEWAY_CONTROL_WRITE_OPERATIONS = frozenset({"direct.control.apply", "direct.control.resume", "direct.control.drain_target", "direct.control.reconcile"})
_GATEWAY_CONTROL_READ_OPERATIONS = frozenset({"direct.control.identity", "direct.control.status", "direct.control.targets", "direct.control.list", "direct.control.resume_status", "direct.control.target_status"})
_GATEWAY_CONTROL_OPERATIONS = _GATEWAY_CONTROL_WRITE_OPERATIONS | _GATEWAY_CONTROL_READ_OPERATIONS
_GATEWAY_DELEGATED_READ_OPERATIONS = frozenset({"direct.delegated_command_status", "direct.delegated_cancel_status"})
_GATEWAY_STRATEGY_DELEGATED_CANCEL = frozenset({"direct.delegated_cancel"})
_GATEWAY_DELEGATED_WRITE_OPERATIONS = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"})
_GATEWAY_DELEGATE_STREAM_OPERATIONS = frozenset({'direct.delegate_events.read_after', 'direct.delegate_events.progress', 'direct.delegate_events.ack'})
_GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_cancel_status', 'direct.delegate_actor_release_status'}) | _GATEWAY_DELEGATE_STREAM_OPERATIONS
_GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS = frozenset({'direct.delegate_actor_cancel', 'direct.delegate_actor_release'})
_GATEWAY_DELEGATE_ACTOR_OPERATIONS = _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS
_GATEWAY_ALL_DELEGATE_OPERATIONS = _GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_OPERATIONS
_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS = frozenset({
    "account.events.read_after", "account.events.progress", "account.events.ack",
    "account.facts.read_after", "account.facts.progress", "account.facts.ack", "account.facts.status",
    "account.facts.snapshot_info", "account.facts.snapshot_read", "account.facts.snapshot_confirm",
})
_GATEWAY_PARENT_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.query', 'direct.parent.cancel', 'direct.parent.operation'})
_GATEWAY_PARENT_READ_OPERATIONS = frozenset({'direct.parent.query', 'direct.parent.operation'})
_GATEWAY_PARENT_WRITE_OPERATIONS = _GATEWAY_PARENT_OPERATIONS - _GATEWAY_PARENT_READ_OPERATIONS
_GATEWAY_STRATEGY_FINANCIAL_OPERATIONS = frozenset({'direct.strategy_financial'})
_STOP_NEW_OPERATIONS = frozenset(
    {
        "direct.reserve",
        "direct.dispatch",
        "direct.delegated_reserve",
        "direct.delegated_dispatch",
        "groups.submit",
        "orders.submit",
        "orders.submit_approved_target",
        "orders.submit_group_leg",
        "reservations.reserve",
    }
)
_READ_ONLY_MARKET_OPERATIONS = frozenset(
    {
        "market.snapshot",
        "market.head",
        "market.read_after",
        "market.read_after_bounded",
        "market.clock_observation",
    }
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
        "STRATEGY_STOPPED",
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


def _is_exact_owner_reduce_scope(
    identity: WireIdentity,
    *,
    port: str,
    scope: WireScope,
    operation: str,
) -> bool:
    """Recognize the separately granted exact-run strategy reduction route."""

    return (
        identity.role is WireRole.SERVICE
        and port == "service.owner_control"
        and operation == "owner.reduce"
        and scope.account_ref is not None
        and not scope.account_refs
        and scope.strategy_ref is not None
        and scope.run_id is not None
        and scope.manual_book_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


_REGISTRY_WIRE_ARGUMENTS = object()


@dataclass(frozen=True, slots=True)
class ServiceCallContext:
    """Trusted context passed to a code-registered business handler."""

    identity: WireIdentity
    scope: WireScope
    registration_id: str
    principal_ref: str
    result_fits: Callable[[Mapping[str, Any]], bool] | None = None
    dispatch_admission: ServiceDispatchAdmission | None = None
    _wire_arguments: object = field(default=None, repr=False, compare=False)

    def _is_registry_wire_arguments(self, arguments: object) -> bool:
        token = self._wire_arguments
        return (type(token) is tuple and len(token) == 2
                and token[0] is _REGISTRY_WIRE_ARGUMENTS and token[1] is arguments)


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
    wire value. The account-owner route and the finite group-write operation
    set are fixed so an ordinary SERVICE ACL cannot turn another order route
    into a coordinator write.
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
            or self.operation
            not in {
                "orders.submit_group_leg",
                "orders.cancel_group_leg",
                "orders.submit_group_compensation_close",
                "orders.submit_group_roll_exit",
            }
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


class ServiceRegistryStopNewError(RuntimeError):
    """The owner could not persist a definitive strategy stop receipt."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
                if resource.operations == frozenset({"owner.reduce"}) and not (
                    _is_exact_owner_reduce_scope(
                        entry.identity,
                        port=resource.port,
                        scope=resource.scope,
                        operation="owner.reduce",
                    )
                ):
                    raise ValueError("owner.reduce requires an exact service strategy run")
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
                    and not (
                        resource.operations == frozenset({"owner.reduce"})
                        and _is_exact_owner_reduce_scope(
                            entry.identity,
                            port=resource.port,
                            scope=resource.scope,
                            operation="owner.reduce",
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


class ServiceDispatchAdmissionError(RuntimeError):
    """Finite failure of a Registry-issued delayed dispatch admission."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_DISPATCH_ADMISSION_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _DispatchAdmissionBinding:
    registration_id: str
    service_epoch: str
    instance_id: str
    principal: str
    identity: WireIdentity
    scope: WireScope
    arguments: bytes
    pid: int
    operation: str
    issuer_thread: threading.Thread
    parent_grant: object | None = None
    delegate_grant: object | None = None
    parent_operation: str | None = None


class ServiceDispatchAdmission:
    """One Registry-issued, argument-bound delayed dispatch authorization.

    A handler may hand this to one account owner thread. That thread may enter
    ``scope`` serially; concurrent use, reentry, fork reuse and transfer to a
    second owner thread are rejected. The scope holds Registry RLock then one
    SQLite read snapshot through the caller's *local* journal commit. It must
    never contain provider I/O. Authorization is checked on scope entry and
    again by the yielded callback after the journal obtains its write lock and
    immediately before its local commit transition. A later lease expiry is
    not a hard external fence and an already sent provider request cannot be
    recalled. The task must call ``close``. Closing is a nonblocking local
    finalizer: it prevents later scope entries and rechecks, but does not
    revoke a commit already past its last recheck. Actual authority revocation,
    STOP_NEW and Registry close serialize through the Registry lock. This
    applies to cooperating changes through this Registry; an out-of-band WAL
    writer is not serialized by that lock and can change the database while
    the fixed read snapshot remains active.
    """

    def __init__(
        self,
        issuer: object,
        registry: ServiceRegistry,
        *,
        registration_id: str,
        service_epoch: str,
        instance_id: str,
        principal: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments: Mapping[str, Any],
        operation: str = "direct.dispatch",
    ) -> None:
        if issuer is not _DISPATCH_ADMISSION_ISSUER or type(registry) is not ServiceRegistry:
            raise ServiceDispatchAdmissionError("NOT_ISSUED")
        if type(operation) is not str or operation not in ({"direct.reserve", "direct.dispatch", "direct.cancel"} | _GATEWAY_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS):
            raise ServiceDispatchAdmissionError("OPERATION_INVALID")
        self._registry = registry
        # Wire canonical bytes make a detached, immutable snapshot of the
        # complete arguments, including nested values and ordering.
        command_value = arguments.get('command')
        parent_operation = (operation if operation in _GATEWAY_PARENT_OPERATIONS else
            'direct.parent.cancel' if operation in {'direct.delegate_actor_cancel', 'direct.delegate_actor_release'}
            and isinstance(command_value, Mapping) and command_value.get('parent_ref') is not None else
            'direct.parent.query' if operation == 'direct.delegate_actor_release_status'
            and isinstance(command_value, Mapping) and command_value.get('parent_ref') is not None else None)
        self._binding = _DispatchAdmissionBinding(
            registration_id, service_epoch, instance_id, principal, identity, scope,
            encode_wire_value(arguments), os.getpid(), operation, threading.current_thread(),
            registry.gateway_parent_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope, operation=parent_operation) if parent_operation is not None else None,
            registry.gateway_delegate_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope, operation=operation) if operation in _GATEWAY_ALL_DELEGATE_OPERATIONS else None,
            parent_operation,
        )
        self._state_lock = threading.Lock()
        self._owner_thread: threading.Thread | None = None
        self._creation_used = False
        self._active_marker: object | None = None
        self._busy = False
        self._closed = False

    def matches(self, arguments: Mapping[str, Any]) -> bool:
        """Check complete canonical arguments and current Registry issuance."""
        if os.getpid() != self._binding.pid or not isinstance(arguments, Mapping):
            return False
        try:
            encoded = encode_wire_value(arguments)
        except Exception:
            return False
        with self._state_lock:
            if self._closed or encoded != self._binding.arguments:
                return False
        with self._registry._lock:
            return (
                not self._closed
                and not self._registry._closed
                and self._registry._dispatch_admissions.get(self) is self._binding
            )

    def matches_context(self, context: ServiceCallContext) -> bool:
        """Match the exact authenticated context this admission was issued for."""
        if os.getpid() != self._binding.pid or type(context) is not ServiceCallContext:
            return False
        if (
            context.dispatch_admission is not self
            or context.identity != self._binding.identity
            or context.scope != self._binding.scope
            or context.registration_id != self._binding.registration_id
            or context.principal_ref != self._binding.principal
        ):
            return False
        with self._state_lock:
            if self._closed:
                return False
        with self._registry._lock:
            return (
                not self._closed
                and not self._registry._closed
                and self._registry._dispatch_admissions.get(self) is self._binding
            )

    def matches_operation(self, operation: str) -> bool:
        """Match the exact operation of this live Registry-issued admission."""
        if (os.getpid() != self._binding.pid or type(operation) is not str
                or operation != self._binding.operation):
            return False
        with self._state_lock:
            if self._closed:
                return False
        with self._registry._lock:
            return (not self._closed and not self._registry._closed
                    and self._registry._dispatch_admissions.get(self) is self._binding)

    def matches_parent_grant(self, grant: object) -> bool:
        with self._state_lock:
            return not self._closed and self._binding.parent_grant is not None and self._binding.parent_grant == grant

    @property
    def service_epoch(self):
        """Epoch of this live Registry-issued admission; never request data."""
        if os.getpid() != self._binding.pid:
            return None
        with self._state_lock:
            if self._closed:
                return None
        with self._registry._lock:
            if self._registry._closed or self._registry._dispatch_admissions.get(self) is not self._binding:
                return None
            return self._binding.service_epoch

    def matches_delegate_grant(self, grant: object) -> bool:
        with self._state_lock:
            return not self._closed and self._binding.delegate_grant is not None and self._binding.delegate_grant == grant

    def scope(self):
        """Enter on the single owner thread; serial reentry remains permitted."""
        return self._scope(creation=False)

    def creation_scope(self):
        """Use cancel once on the issuer thread before transferring to the owner."""
        return self._scope(creation=True)

    @contextmanager
    def _scope(self, *, creation: bool) -> Iterator[Callable[[], None]]:
        if os.getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
        thread = threading.current_thread()
        with self._state_lock:
            if self._closed:
                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
            if self._busy:
                raise ServiceDispatchAdmissionError("ADMISSION_BUSY")
            if creation:
                if self._binding.operation != "direct.cancel":
                    raise ServiceDispatchAdmissionError("OPERATION_MISMATCH")
                if thread is not self._binding.issuer_thread:
                    raise ServiceDispatchAdmissionError("WRONG_ISSUER_THREAD")
                if self._creation_used or self._owner_thread is not None:
                    raise ServiceDispatchAdmissionError("CREATION_SCOPE_CONSUMED")
                self._creation_used = True
            else:
                if self._owner_thread is not None and self._owner_thread is not thread:
                    raise ServiceDispatchAdmissionError("WRONG_OWNER_THREAD")
                self._owner_thread = thread
            self._busy = True
            marker = object()
            self._active_marker = marker
        try:
            authorized = False
            try:
                with self._registry._read_snapshot():
                    def validate_current() -> None:
                        if os.getpid() != self._binding.pid:
                            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
                        with self._state_lock:
                            if (self._closed or not self._busy or self._active_marker is not marker
                                    or threading.current_thread() is not thread):
                                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
                        if self._registry._dispatch_admissions.get(self) is not self._binding:
                            raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
                        try:
                            code = self._registry._direct_dispatch_admission_error(
                                self._binding.registration_id, self._binding.service_epoch,
                                self._binding.instance_id, self._binding.principal,
                                self._binding.identity, self._binding.scope,
                                self._binding.operation,
                            )
                        except Exception:
                            raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        if code is not None:
                            raise ServiceDispatchAdmissionError(code)
                        if self._binding.parent_operation is not None:
                            row = self._registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                              (self._binding.registration_id,)).fetchone()
                            current_grant = None if row is None else self._registry.gateway_parent_grant(
                                instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                identity=self._binding.identity, scope=self._binding.scope,
                                operation=self._binding.parent_operation)
                            if current_grant is None or current_grant != self._binding.parent_grant:
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                            row = self._registry._db.execute('SELECT operations_json,scope_ref FROM sessions WHERE registration_id=?',
                                                             (self._binding.registration_id,)).fetchone()
                            if (row is None or self._binding.parent_operation not in frozenset(json.loads(row['operations_json']))
                                    or not self._registry._current_resource_allows(self._binding.principal,
                                        self._binding.identity, instance_id=self._binding.instance_id,
                                        port='service.direct_write', scope_ref=row['scope_ref'],
                                        scope=self._binding.scope, operation=self._binding.parent_operation)):
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        if self._binding.operation in _GATEWAY_ALL_DELEGATE_OPERATIONS:
                            row = self._registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                              (self._binding.registration_id,)).fetchone()
                            current_grant = None if row is None else self._registry.gateway_delegate_grant(
                                instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                identity=self._binding.identity, scope=self._binding.scope,
                                operation=self._binding.operation)
                            if current_grant is None or current_grant != self._binding.delegate_grant:
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        # close() must be able to win while a clock/DB check
                        # is in progress. Its lock is the final permit-state
                        # linearization point, after the authority check.
                        with self._state_lock:
                            if (self._closed or not self._busy or self._active_marker is not marker
                                    or threading.current_thread() is not thread):
                                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")

                    def recheck() -> None:
                        # The marker may wait on a sidecar lock or fsync. Do
                        # not seal for an already invalid request, and do not
                        # rely on authority or time from before that wait.
                        validate_current()
                        if self._binding.operation not in ({"account.events.progress", "account.facts.progress", "account.facts.status", "account.facts.snapshot_info"} | _GATEWAY_PARENT_READ_OPERATIONS | (_GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS - {'direct.delegate_events.read_after', 'direct.delegate_events.ack'}) | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS):
                            try:
                                fact_kind = ("cursor" if self._binding.operation in (_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_DELEGATE_STREAM_OPERATIONS)
                                             else "dispatch")
                                self._registry._record_migration_write_attempt(fact_kind=fact_kind)
                            except Exception:
                                raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        validate_current()

                    recheck()
                    authorized = True
                    yield recheck
            except _StaleOwnerError:
                if authorized:
                    raise
                raise ServiceDispatchAdmissionError("STALE_EPOCH") from None
            except sqlite3.Error:
                if authorized:
                    raise
                raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
        finally:
            with self._state_lock:
                self._busy = False
                self._active_marker = None

    def close(self) -> None:
        if os.getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
        with self._state_lock:
            self._closed = True

    invalidate = close


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
        reply_result_budget_checker: Callable[[WireEnvelope, int | None], int] | None = None,
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
            or (
                reply_result_budget_checker is not None
                and not callable(reply_result_budget_checker)
            )
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
        # Cache only parsed immutable DTOs keyed by the exact durable JSON
        # text. Lease, expiry, epoch, policy and Risk decisions are always
        # re-read and re-evaluated from the current SQLite transaction.
        self._registration_identity_scope_cache: OrderedDict[
            str, tuple[str, str, WireIdentity, WireScope]
        ] = OrderedDict()
        self._clock = clock
        self._reply_size_checker = reply_size_checker
        self._reply_result_budget_checker = reply_result_budget_checker
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
        self._restricted_readiness: dict[tuple[str, str], Callable[[], bool]] = {}
        self._ready = ready
        self._lock = threading.RLock()
        self._gateway_lease_watch = {}
        self._gateway_lease_grace = {}
        self._gateway_delegate_grants = {}
        self._gateway_parent_grants = {}
        # Trusted owner-local coordinators remain unavailable until a separate
        # complete writer/lease coverage capability exists.
        self._dispatch_claim_coordinators = ()
        # Decision B: a dispatch claim may follow its last authority check by
        # at most the candidate window, so a lease must outlive that window.
        self._dispatch_lease_margin_seconds = 0.0
        self._dispatch_admissions: weakref.WeakKeyDictionary[
            ServiceDispatchAdmission, _DispatchAdmissionBinding
        ] = weakref.WeakKeyDictionary()
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
        with self._lock, self._registry_claim_changes_locked():
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
                stop_new_at REAL,
                stop_new_command_id TEXT,
                PRIMARY KEY(execution_domain_ref, strategy_ref, account_ref)
            );
            CREATE TABLE IF NOT EXISTS strategy_stop_new_receipts (
                command_id TEXT PRIMARY KEY,
                scope_digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('applied', 'rejected')),
                effect_ref TEXT,
                effect_result_json TEXT NOT NULL,
                created_at REAL NOT NULL
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
        if "stop_new_at" not in columns:
            self._db.execute("ALTER TABLE strategy_write_claims ADD COLUMN stop_new_at REAL")
        if "stop_new_command_id" not in columns:
            self._db.execute(
                "ALTER TABLE strategy_write_claims ADD COLUMN stop_new_command_id TEXT"
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
    def _registry_claim_changes_locked(self):
        """Invalidate attached account views before a Registry mutation.

        The Registry lock serializes attachment against writers; the
        coordinator only holds its own lock for begin/finish, never for SQL.
        After the composition sealed coverage a successful change publishes
        READY; a failed operation ends UNAVAILABLE. Neither proves protection
        against another process; lease expiry is covered by the dispatch margin.
        """
        from bt_api_py.gateway_dispatch_claim import DispatchClaimError

        changes = []
        try:
            for coordinator in self._dispatch_claim_coordinators:
                try:
                    token = coordinator.begin_change('registry')
                except DispatchClaimError as error:
                    if error.code in {'DOMAIN_POISONED', 'COORDINATOR_CLOSED'}:
                        continue  # These coordinators can no longer grant claims.
                    raise
                changes.append((coordinator, token))
        except BaseException:
            # Could not begin every change: a coordination fault; close the
            # domain on the coordinators already invalidated.
            for coordinator, _token in changes:
                with suppress(BaseException):
                    coordinator.poison_domain('registry')
            raise
        try:
            yield
        except BaseException:
            # A rejected or rolled-back Registry operation is not a coordination
            # fault: the epoch moved, so finish UNAVAILABLE (claims refused until
            # the next successful Registry change). Only a failed finish poisons.
            for coordinator, token in changes:
                try:
                    coordinator.finish_change(token, 'UNAVAILABLE')
                except BaseException:
                    with suppress(BaseException):
                        coordinator.poison_domain('registry')
            raise
        try:
            for coordinator, token in changes:
                # READY only after the composition sealed coverage; it never
                # replaces the per-dispatch admission check.
                coordinator.finish_change_current(token)
        except BaseException:
            for coordinator, _token in changes:
                try:
                    coordinator.poison_domain('registry')
                except BaseException:
                    pass
            raise

    def attach_dispatch_claim_coordinator(self, coordinator, *, account_ref, owner_generation):
        """Attach a trusted production coordinator without enabling claims."""
        from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator

        if (type(coordinator) is not GatewayDispatchClaimCoordinator
                or coordinator._test_only
                or not coordinator.matches_owner(account_ref, owner_generation)):
            raise ValueError('invalid dispatch claim coordinator')
        with self._lock:
            self._ensure_open()
            if coordinator in self._dispatch_claim_coordinators:
                raise ValueError('duplicate dispatch claim coordinator')
            # A real complete-writer proof is not available, including for a
            # noncooperating cross-process SQLite WAL writer.  Do not READY.
            token = coordinator.begin_change('registry')
            coordinator.finish_change(token, 'UNAVAILABLE')
            self._dispatch_claim_coordinators += (coordinator,)
            # Covers the claim window (50 ms) with margin for owner queueing.
            self._dispatch_lease_margin_seconds = max(self._dispatch_lease_margin_seconds, 0.5)

    @contextmanager
    def _transaction(self, *, check_epoch: bool = True) -> Iterator[None]:
        with self._lock:
            if self._closed or self._db is None:
                raise sqlite3.ProgrammingError("registry is closed")
            with self._registry_claim_changes_locked():
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
        # Only an explicitly configured strategy's real current-epoch lease
        # expiry can create an automatic DRAIN event. Revocation, explicit
        # unregister and stale service epochs are not expiry evidence.
        if self._gateway_lease_watch:
            rows = self._db.execute(
                "SELECT * FROM sessions WHERE service_epoch=? AND expires_at<=? AND port='service.direct_write'",
                (self._epoch, now)).fetchall()
            for row in rows:
                identity = self._stored_identity(row['identity_json'])
                scope = self._stored_scope(row['scope_json'])
                policy = self._gateway_lease_watch.get((row['instance_id'], scope.account_ref))
                writes = frozenset(json.loads(row['write_operations_json']))
                if (policy is None or scope.strategy_ref not in policy or scope.run_id is None
                        or identity.role is not WireRole.STRATEGY_CLIENT
                        or identity.delegate_ref is not None or identity.actor_ref != scope.strategy_ref
                        or identity.principal_ref != row['principal']
                        or not writes.intersection({'direct.reserve', 'direct.dispatch'})
                        or row['policy_version'] != self.policy_version
                        or row['requirements_version'] != self.requirements_version
                        or not self._identity_is_trusted(row['principal'], identity)
                        or not self._registration_policy_matches(row)
                        or not any(self._current_resource_allows(row['principal'], identity,
                            instance_id=row['instance_id'], port=row['port'], scope_ref=row['scope_ref'],
                            scope=scope, operation=op) for op in writes.intersection({'direct.reserve', 'direct.dispatch'}))):
                    continue
                event_id = 'lease:' + hashlib.sha256(json.dumps(
                    [row['instance_id'], row['registration_id'], row['service_epoch']],
                    separators=(',', ':')).encode()).hexdigest()
                if self._db.execute('SELECT count(*) FROM gateway_lease_expirations').fetchone()[0] >= 100000:
                    raise ValueError('gateway lease event capacity exceeded')
                # D63-28: the cause is applied only once the lease has stayed
                # expired for the configured duration. Until then the event
                # waits; a registration of the same strategy withdraws it.
                apply_at = row['expires_at'] + self._gateway_lease_grace[(row['instance_id'], scope.account_ref)]
                self._db.execute(
                    "INSERT OR IGNORE INTO gateway_lease_expirations (event_id,instance_id,account_ref,"
                    "strategy_ref,run_id,principal,registration_id,service_epoch,expires_at,observed_at,"
                    "state,apply_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'WAITING',?)",
                    (event_id, row['instance_id'], scope.account_ref, scope.strategy_ref, scope.run_id,
                     row['principal'], row['registration_id'], row['service_epoch'], row['expires_at'], now,
                     apply_at))
            self._db.execute(
                "UPDATE gateway_lease_expirations SET state='PENDING' WHERE state='WAITING' AND apply_at<=?",
                (now,))
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

    def configure_gateway_lease_drain(self, *, instance_id: str, account_ref: str,
                                      strategies: frozenset[str], expiry_seconds: float) -> None:
        """Install exact trusted owner policy; no wire request can set it.

        ``expiry_seconds`` has no default (D63-28): a lease that has stayed
        expired this long becomes a PENDING event. Before that the event is
        WAITING and fences nothing; a registration of the same strategy turns it
        into RETURNED and no cause is applied for it.
        """
        if (not _text(instance_id) or not _text(account_ref) or type(strategies) is not frozenset
                or not strategies or any(not _text(value) for value in strategies)
                or type(expiry_seconds) not in (int, float) or not math.isfinite(expiry_seconds)
                or not 0 <= expiry_seconds <= 86400
                or (instance_id, 'service.direct_write') not in self._capabilities):
            raise ValueError('invalid gateway lease policy')
        key = (instance_id, account_ref)
        with self._transaction():
            existing = self._gateway_lease_watch.get(key)
            if existing is not None and (existing != strategies
                                         or self._gateway_lease_grace[key] != float(expiry_seconds)):
                raise ValueError('gateway lease policy changed')
            self._db.execute('CREATE TABLE IF NOT EXISTS gateway_lease_expirations ('
                'event_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,account_ref TEXT NOT NULL,'
                'strategy_ref TEXT NOT NULL,run_id TEXT NOT NULL,principal TEXT NOT NULL,'
                'registration_id TEXT NOT NULL,service_epoch TEXT NOT NULL,expires_at REAL NOT NULL,'
                'observed_at REAL NOT NULL,state TEXT NOT NULL,apply_at REAL)')
            columns = {row[1] for row in self._db.execute('PRAGMA table_info(gateway_lease_expirations)')}
            if 'apply_at' not in columns:
                # A table written before the duration existed: its events were
                # applied at expiry, which is what a missing value keeps meaning.
                self._db.execute('ALTER TABLE gateway_lease_expirations ADD COLUMN apply_at REAL')
            self._gateway_lease_watch[key] = strategies
            self._gateway_lease_grace[key] = float(expiry_seconds)

    def pending_gateway_lease_expirations(self, *, instance_id: str, account_ref: str, limit: int = 32):
        if (instance_id, account_ref) not in self._gateway_lease_watch or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('invalid gateway lease query')
        with self._transaction():
            self._expire_sessions(self._now())
            rows = self._db.execute("SELECT * FROM gateway_lease_expirations WHERE instance_id=? AND account_ref=? AND state='PENDING' ORDER BY event_id LIMIT ?",
                                    (instance_id, account_ref, limit)).fetchall()
            return tuple(dict(row) for row in rows)

    def acknowledge_gateway_lease_expiration(self, *, instance_id: str, account_ref: str, event_id: str):
        if (instance_id, account_ref) not in self._gateway_lease_watch or not _text(event_id):
            raise ValueError('invalid gateway lease event')
        with self._transaction():
            row = self._db.execute('SELECT state FROM gateway_lease_expirations WHERE instance_id=? AND account_ref=? AND event_id=?',
                                   (instance_id, account_ref, event_id)).fetchone()
            if row is None or row['state'] not in ('PENDING', 'APPLIED'):
                raise ValueError('gateway lease event unavailable')
            self._db.execute("UPDATE gateway_lease_expirations SET state='APPLIED' WHERE event_id=?", (event_id,))

    @contextmanager
    def gateway_lease_control_fence(self, *, instance_id: str, account_ref: str, event_id: str):
        """Hold one persisted, actually expired lease event through a local control commit."""
        if not all(_text(value) for value in (instance_id, account_ref, event_id)):
            raise ValueError('invalid gateway lease event')
        with self._read_snapshot():
            def snapshot():
                row = self._db.execute("SELECT * FROM gateway_lease_expirations WHERE instance_id=? AND account_ref=? AND event_id=? AND state='APPLIED'",
                                       (instance_id, account_ref, event_id)).fetchone()
                if row is None:
                    raise ValueError('gateway lease event unavailable')
                event = dict(row)
                expected = 'lease:' + hashlib.sha256(json.dumps(
                    [instance_id, event['registration_id'], event['service_epoch']], separators=(',', ':')).encode()).hexdigest()
                if (event_id != expected or event['strategy_ref'] not in self._gateway_lease_watch.get((instance_id, account_ref), ())
                        or any(not _text(event[name]) for name in ('principal', 'registration_id', 'service_epoch', 'run_id'))
                        or type(event['expires_at']) not in (int, float) or type(event['observed_at']) not in (int, float)
                        or not math.isfinite(event['expires_at']) or not math.isfinite(event['observed_at'])
                        or event['observed_at'] < event['expires_at']):
                    raise ValueError('invalid gateway lease event')
                return event
            original = snapshot()
            def recheck():
                if snapshot() != original:
                    raise ValueError('gateway lease event changed')
            yield original, recheck
    def register_handler(
        self,
        instance_id: str,
        operation: str,
        handler: Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]],
        *,
        restricted_readiness: Callable[[], bool] | None = None,
    ) -> None:
        """Register a handler, optionally gated by a restricted readiness check.

        A restricted handler remains unavailable unless its trusted owner
        callback returns exactly ``True``. It is accepted only for the
        recovery-close and owner-cancel write routes and is the only dispatch
        exception when the Registry is globally unready. While globally
        unready, it also permits renewal of one existing registration that
        already holds that exact route; it never changes global readiness or
        widens the registration.
        """

        if not _text(instance_id) or not _text(operation) or operation.startswith("service."):
            raise ValueError("handler key is invalid")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if restricted_readiness is not None and not callable(restricted_readiness):
            raise TypeError("restricted_readiness must be callable")
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            capabilities = tuple(
                cap
                for cap in self._capabilities.values()
                if cap.instance_id == instance_id and operation in cap.operations
            )
            if not capabilities:
                raise ValueError("handler operation is outside declared capability")
            if operation == _RECOVERY_CLOSE_OPERATION[1]:
                if (
                    restricted_readiness is None
                    or any(cap.port != _RECOVERY_CLOSE_OPERATION[0] for cap in capabilities)
                    or not any(
                        (cap.port, operation) == _RECOVERY_CLOSE_OPERATION
                        and operation in cap.write_operations
                        for cap in capabilities
                    )
                ):
                    raise ValueError(
                        "recovery close requires its restricted write readiness callback"
                    )
            elif operation == _OWNER_CANCEL_OPERATION[1] and restricted_readiness is not None:
                if (
                    any(cap.port != _OWNER_CANCEL_OPERATION[0] for cap in capabilities)
                    or not any(
                        (cap.port, operation) == _OWNER_CANCEL_OPERATION
                        and operation in cap.write_operations
                        for cap in capabilities
                    )
                ):
                    raise ValueError(
                        "owner cancel requires its restricted write readiness callback"
                    )
            elif restricted_readiness is not None:
                raise ValueError(
                    "restricted readiness requires a supported restricted write capability"
                )
            key = (instance_id, operation)
            if key in self._handlers:
                raise ValueError("handler is already registered")
            self._handlers[key] = handler
            if restricted_readiness is not None:
                self._restricted_readiness[key] = restricted_readiness

    def set_ready(self, ready: bool) -> None:
        if type(ready) is not bool:
            raise TypeError("ready must be bool")
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            self._ready = ready

    def revoke_principal(self, principal: str) -> bool:
        """Revoke one locally trusted principal; never accepts wire requests."""
        if not _principal_ref(principal):
            raise ValueError('invalid principal')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if principal not in self._grants:
                return False
            grants = dict(self._grants)
            del grants[principal]
            self._grants = MappingProxyType(grants)
            return True

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
        reservation owners seed on ``reservations.reserve``; Execution group
        owners seed on ``orders.submit_group_leg``; Risk compensation owners
        seed on ``orders.submit_group_compensation_close``. Query/release
        registrations, expired rows, and caller-selected runs cannot seed a
        claim. Repeating the same mapping is idempotent; a different run is
        rejected. This never sets or modifies ``active_registration_id``.
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
            group_order_writes = write_operations.intersection(
                {
                    "orders.submit_group_leg",
                    "orders.submit_group_compensation_close",
                    "orders.submit_group_roll_exit",
                }
            )
            operation = (
                next(iter(group_order_writes))
                if registration["port"] == "service.order_entry"
                and len(group_order_writes) == 1
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
        with self._lock, self._registry_claim_changes_locked():
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
        registration_id = row["registration_id"]
        identity_json = row["identity_json"]
        scope_json = row["scope_json"]
        cached = self._registration_identity_scope_cache.get(registration_id)
        if (
            cached is not None
            and cached[0] == identity_json
            and cached[1] == scope_json
        ):
            self._registration_identity_scope_cache.move_to_end(registration_id)
            identity, scope = cached[2], cached[3]
        else:
            # Never trust cached policy or authorization state. This bounded
            # memo avoids re-decoding only byte-identical persisted JSON; any
            # changed or malformed row takes the original strict parser path.
            identity = self._stored_identity(identity_json)
            scope = self._stored_scope(scope_json)
            self._registration_identity_scope_cache[registration_id] = (
                identity_json,
                scope_json,
                identity,
                scope,
            )
            self._registration_identity_scope_cache.move_to_end(registration_id)
            while len(self._registration_identity_scope_cache) > self.max_sessions:
                self._registration_identity_scope_cache.popitem(last=False)
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
        # Lazy: the direct path never imports the typed codec module.
        from bt_api_base.wire_codec_v1 import (
            typed_contract_declaration as _typed_contract_declaration,
        )

        if self._payload_fields(request, frozenset()) is None:
            return self._failure("INVALID_REQUEST")
        with self._transaction():
            if not self._identity_is_trusted(principal, request.header.identity):
                return self._failure("DENIED")
            result = {
                "service_id": self.service_id,
                "protocol_version": WIRE_PROTOCOL_VERSION,
                "wire_contract": wire_contract_digest(),
                # Iteration 60.08 (G5): typed minor/registry negotiation.
                "typed_contract": _typed_contract_declaration(),
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

    def configure_gateway_delegates(self, *, instance_id: str, scope_ref: str, grants: tuple) -> None:
        """Install operator-owned actor grants; never accept them from a request."""
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        if (not _text(instance_id) or not _text(scope_ref) or type(grants) is not tuple or not grants
                or any(type(grant) is not GatewayDelegateGrant or grant.identity.role is not WireRole.SERVICE
                       or grant.identity.delegate_ref is not None for grant in grants)):
            raise ValueError('invalid gateway delegate policy')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            for grant in grants:
                key = (instance_id, scope_ref, grant.identity.principal_ref, grant.account_scope, grant.strategy_scope)
                if key in self._gateway_delegate_grants:
                    raise ValueError('duplicate gateway delegate policy')
                self._gateway_delegate_grants[key] = grant

    def configure_gateway_parents(self, *, instance_id: str, scope_ref: str, grants: tuple) -> None:
        from bt_api_py.gateway_parent_service import GatewayParentGrant
        if (not _text(instance_id) or not _text(scope_ref) or type(grants) is not tuple or not grants
                or any(type(grant) is not GatewayParentGrant for grant in grants)):
            raise ValueError('invalid gateway parent policy')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            for grant in grants:
                key = (instance_id, scope_ref, grant.identity.principal_ref, grant.account_scope, grant.strategy_scope)
                if key in self._gateway_parent_grants:
                    raise ValueError('duplicate gateway parent policy')
                self._gateway_parent_grants[key] = grant

    def gateway_parent_grant(self, *, instance_id, scope_ref, identity, scope, operation):
        if (operation not in _GATEWAY_PARENT_OPERATIONS or type(identity) is not WireIdentity
                or identity.role not in {WireRole.SERVICE, WireRole.STRATEGY_CLIENT}
                or identity.delegate_ref is not None or type(scope) is not WireScope
                or scope.account_ref is None or scope.strategy_ref is None or scope.run_id is None
                or scope.account_refs or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                or scope.group_id is not None or scope.leg_id is not None
                or (identity.role is WireRole.STRATEGY_CLIENT and identity.actor_ref != scope.strategy_ref)):
            return None
        grant = self._gateway_parent_grants.get((instance_id, scope_ref, identity.principal_ref,
                                                 scope.account_ref, scope.strategy_ref))
        if (grant is None or grant.identity != identity or grant.execution_domain_ref != scope.execution_domain_ref
                or operation not in grant.operations):
            return None
        return grant

    def gateway_delegate_grant(self, *, instance_id, scope_ref, identity, scope, operation):
        logical = {'direct.delegated_reserve':'direct.reserve',
                   'direct.delegated_dispatch':'direct.dispatch',
                   'direct.delegate_actor_status':'direct.command_status',
                   'direct.delegate_actor_cancel':'direct.cancel',
                   'direct.delegate_actor_cancel_status':'direct.cancel_status',
                   'direct.delegate_actor_release':'direct.release',
                   'direct.delegate_actor_release_status':'direct.command_status',
                   'direct.delegate_events.read_after':'direct.command_status',
                   'direct.delegate_events.progress':'direct.command_status',
                   'direct.delegate_events.ack':'direct.command_status'}.get(operation)
        if (logical is None or type(identity) is not WireIdentity or identity.role is not WireRole.SERVICE
                or identity.delegate_ref is not None or type(scope) is not WireScope
                or scope.account_ref is None or scope.strategy_ref is None or scope.run_id is None
                or scope.account_refs or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                or scope.group_id is not None or scope.leg_id is not None):
            return None
        grant = self._gateway_delegate_grants.get((instance_id, scope_ref, identity.principal_ref,
                                                   scope.account_ref, scope.strategy_ref))
        if (grant is None or grant.identity != identity or grant.execution_domain_ref != scope.execution_domain_ref
                or logical not in grant.operations):
            return None
        return grant

    def _resource_scope_matches(self, resource, requested, identity):
        if self._scope_matches(resource.scope, requested, identity):
            return True
        if (identity.role is not WireRole.SERVICE or resource.port != 'service.direct_write'
                or not resource.operations or not resource.operations <= (_GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_PARENT_OPERATIONS)
                or resource.scope.run_id is not None or requested.run_id is None):
            return False
        if not all((self.gateway_parent_grant if op in _GATEWAY_PARENT_OPERATIONS else self.gateway_delegate_grant)(
                   instance_id=resource.instance_id, scope_ref=resource.scope_ref,
                   identity=identity, scope=requested, operation=op) is not None for op in resource.operations):
            return False
        return requested == replace(resource.scope, run_id=requested.run_id)

    def _gateway_delegate_strategy_run_current(self, scope):
        if not self._strategy_owner_run_claim_exists(scope):
            return False
        claim = self._db.execute(
            'SELECT active_registration_id FROM strategy_write_claims WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=? AND run_id=?',
            (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref, scope.run_id)).fetchone()
        row = None if claim is None else self._db.execute(
            'SELECT * FROM sessions WHERE registration_id=? AND service_epoch=? AND expires_at>?',
            (claim['active_registration_id'], self._epoch, self._now())).fetchone()
        if (row is None or row['policy_version'] != self.policy_version
                or row['requirements_version'] != self.requirements_version
                or not self._registration_policy_matches(row)):
            return False
        identity = self._stored_identity(row['identity_json'])
        actual_scope = self._stored_scope(row['scope_json'])
        return (identity.role is WireRole.STRATEGY_CLIENT and identity.actor_ref == scope.strategy_ref
                and actual_scope == scope and self._identity_is_trusted(row['principal'], identity)
                and any(self._current_resource_allows(row['principal'], identity, instance_id=row['instance_id'],
                    port=row['port'], scope_ref=row['scope_ref'], scope=actual_scope, operation=op)
                    for op in frozenset(json.loads(row['write_operations_json']))))

    def gateway_market_registration_current(self, context, *, instance_id, registration_id=None):
        """Under a Registry snapshot, resolve a unique separately authorized market session."""
        if (type(context) is not ServiceCallContext or not _text(instance_id)
                or (registration_id is not None and not _text(registration_id))
                or context.identity.role not in {WireRole.STRATEGY_CLIENT, WireRole.SERVICE}
                or context.identity.delegate_ref is not None):
            return None
        matches = []
        rows = self._db.execute(
            'SELECT * FROM sessions WHERE instance_id=? AND port=? AND principal=? AND service_epoch=? AND expires_at>?',
            (instance_id, 'service.market_source', context.principal_ref, self._epoch,
             self._now() + self._dispatch_lease_margin_seconds)).fetchall()
        for row in rows:
            identity = self._stored_identity(row['identity_json'])
            scope = self._stored_scope(row['scope_json'])
            operations = frozenset(json.loads(row['operations_json']))
            same_scope = (scope == context.scope if identity.role is WireRole.STRATEGY_CLIENT else
                          scope.execution_domain_ref == context.scope.execution_domain_ref
                          and scope.account_ref == context.scope.account_ref and not scope.account_refs
                          and scope.strategy_ref is None and scope.run_id is None
                          and scope.manual_book_ref is None and scope.portfolio_ref is None
                          and scope.group_id is None and scope.leg_id is None)
            if (identity != context.identity or not same_scope or not self._identity_is_trusted(context.principal_ref, identity)
                    or not self._registration_policy_matches(row)
                    or not {'market.subscribe', 'market.clock_observation'} <= operations
                    or any(not self._current_resource_allows(context.principal_ref, identity,
                        instance_id=instance_id, port=row['port'], scope_ref=row['scope_ref'],
                        scope=scope, operation=op) for op in ('market.subscribe', 'market.clock_observation'))):
                continue
            matches.append(row['registration_id'])
        if len(matches) != 1 or (registration_id is not None and matches[0] != registration_id):
            return None
        return matches[0]

    def _account_observer_registration_unique(self, *, instance_id, principal, identity, scope, registration_id, operation):
        """A stable account consumer can have only one live authenticated owner."""
        rows = self._db.execute(
            'SELECT * FROM sessions WHERE instance_id=? AND port=? AND principal=? AND service_epoch=? AND expires_at>?',
            (instance_id, 'service.account_read', principal, self._epoch, self._now())).fetchall()
        matches = []
        family = ('account.facts.' if operation.startswith('account.facts.') else 'account.events.')
        for row in rows:
            current_identity = self._stored_identity(row['identity_json'])
            current_scope = self._stored_scope(row['scope_json'])
            operations = frozenset(json.loads(row['operations_json']))
            if (current_identity != identity or current_scope.execution_domain_ref != scope.execution_domain_ref
                    or current_scope.account_ref != scope.account_ref or current_scope.strategy_ref != scope.strategy_ref
                    or not any(op.startswith(family) for op in operations)
                    or not self._identity_is_trusted(principal, current_identity)
                    or not self._registration_policy_matches(row)):
                continue
            matches.append(row['registration_id'])
        return matches == [registration_id]

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
        instrument_operations = frozenset({
            "account.instrument_spec.begin", "account.instrument_spec.status",
            "account.instrument_spec.cancel",
        })
        if (port == "service.account_read" and requested_operations & instrument_operations
                and (not instrument_operations <= requested_operations
                     or identity.role is not WireRole.STRATEGY_CLIENT)):
            return "SCOPE_DENIED", capability, frozenset()
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
            if self._resource_scope_matches(resource, scope, identity)
        ]
        if not grants:
            return "SCOPE_DENIED", capability, frozenset()
        allowed = frozenset().union(*(resource.operations for resource in grants))
        granted = requested_operations & capability.operations & allowed
        if not requested_operations or granted != requested_operations:
            return "SCOPE_DENIED", capability, frozenset()
        if requested_operations & _GATEWAY_ALL_DELEGATE_OPERATIONS:
            if (port != 'service.direct_write' or any(self.gateway_delegate_grant(
                    instance_id=instance_id, scope_ref=scope_ref, identity=identity, scope=scope, operation=op
                    ) is None for op in requested_operations & _GATEWAY_ALL_DELEGATE_OPERATIONS)
                    or not self._gateway_delegate_strategy_run_current(scope)):
                return 'SCOPE_DENIED', capability, frozenset()
        if requested_operations & _GATEWAY_PARENT_OPERATIONS:
            if (port != 'service.direct_write' or any(self.gateway_parent_grant(
                    instance_id=instance_id, scope_ref=scope_ref, identity=identity, scope=scope, operation=op
                    ) is None for op in requested_operations & _GATEWAY_PARENT_OPERATIONS)
                    or not self._gateway_delegate_strategy_run_current(scope)):
                return 'SCOPE_DENIED', capability, frozenset()
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
            if (identity.role is WireRole.APPLICATION_CLIENT and port == "service.direct_write"
                    and granted_write_operations <= _GATEWAY_CONTROL_WRITE_OPERATIONS):
                if (scope.account_ref is None or scope.account_refs or scope.manual_book_ref is not None
                        or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None
                        or identity.delegate_ref is not None):
                    return "SCOPE_DENIED", capability, frozenset()
            elif (identity.role is WireRole.SERVICE and port == 'service.direct_write'
                  and granted_write_operations <= (_GATEWAY_PARENT_WRITE_OPERATIONS | _GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS)
                  and all((self.gateway_parent_grant if op in _GATEWAY_PARENT_WRITE_OPERATIONS else self.gateway_delegate_grant)(
                          instance_id=instance_id,scope_ref=scope_ref,identity=identity,scope=scope,operation=op)
                          is not None for op in granted_write_operations)):
                if not self._gateway_delegate_strategy_run_current(scope):
                    return "DUPLICATE_ACTIVE_RUN", capability, frozenset()
            elif identity.role is WireRole.STRATEGY_CLIENT:
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
                    and not _is_exact_owner_reduce_scope(
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
                        request.header.scope,
                        now,
                        registration_id,
                        write_ops,
                    )
                    if claim_error is not None:
                        if isinstance(claim_error, tuple):
                            return self._failure_with_result(claim_error[0], claim_error[1])
                        return self._failure(claim_error)

            if (port == 'service.direct_write' and request.header.identity.role is WireRole.STRATEGY_CLIENT
                    and request.header.scope.strategy_ref is not None
                    and (instance_id, request.header.scope.account_ref) in self._gateway_lease_watch
                    and (granted & capability.write_operations).intersection(
                        {'direct.reserve', 'direct.dispatch'})):
                # The strategy is back before its lease had stayed expired for
                # the configured duration (due events were promoted above).
                self._db.execute(
                    "UPDATE gateway_lease_expirations SET state='RETURNED' WHERE instance_id=? AND "
                    "account_ref=? AND strategy_ref=? AND state='WAITING'",
                    (instance_id, request.header.scope.account_ref, request.header.scope.strategy_ref))
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
        self,
        scope: WireScope,
        now: float,
        registration_id: str,
        write_operations: frozenset[str],
    ) -> str | tuple[str, Mapping[str, Any]] | None:
        if scope.strategy_ref is None or scope.run_id is None:
            return "SCOPE_DENIED"
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return "SCOPE_DENIED"
        rows = tuple(
            self._db.execute(
                "SELECT run_id, active_registration_id, stop_new_command_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, account_ref),
            ).fetchone()
            for account_ref in accounts
        )
        if any(
            row is not None
            and row["run_id"] == scope.run_id
            and row["stop_new_command_id"] is not None
            and not write_operations.isdisjoint(_STOP_NEW_OPERATIONS)
            for row in rows
        ):
            return "STRATEGY_STOPPED"
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

    def stop_strategy_new(self, scope: WireScope, command_id: str) -> Mapping[str, Any]:
        """Persist one exact run fence and return its durable owner receipt.

        This method is an in-process owner hook for the Monitor control
        maintainer. It cannot select an account, strategy, or run by lookup:
        callers must supply one fully bound strategy scope, and the registry
        only fences a matching durable write claim.
        """

        if (
            type(scope) is not WireScope
            or scope.account_ref is None
            or scope.account_refs
            or scope.strategy_ref is None
            or scope.run_id is None
            or scope.manual_book_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
            or not _text(command_id)
        ):
            raise ServiceRegistryStopNewError("INVALID_SCOPE_OR_COMMAND")
        scope_digest = hashlib.sha256(
            json.dumps(
                {"service_id": self.service_id, "scope": self._scope_dict(scope)},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        effect_ref = "strategy-stop-new-v1:" + scope_digest
        now = self._now()
        with self._transaction():
            previous = self._db.execute(
                "SELECT * FROM strategy_stop_new_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["scope_digest"] != scope_digest:
                    raise ServiceRegistryStopNewError("COMMAND_SCOPE_CONFLICT")
                return self._stop_new_receipt_from_row(previous)
            claim = self._db.execute(
                "SELECT run_id, stop_new_at, stop_new_command_id "
                "FROM strategy_write_claims WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref),
            ).fetchone()
            if claim is None or claim["run_id"] != scope.run_id:
                # The Monitor ledger persists this definitive no-effect result.
                # Do not add a second owner row or seal a migration for a
                # request that cannot mutate the active strategy claim.
                return MappingProxyType(
                    {
                        "command_id": command_id,
                        "status": "rejected",
                        "effect_ref": None,
                        "effect_result": MappingProxyType(
                            {"action": "stop_new", "result": "run_not_active"}
                        ),
                    }
                )
            if (claim["stop_new_at"] is None) != (claim["stop_new_command_id"] is None):
                raise ServiceRegistryStopNewError("FENCE_STATE_INVALID")
            count = int(
                self._db.execute(
                    "SELECT COUNT(*) FROM strategy_stop_new_receipts"
                ).fetchone()[0]
            )
            if count >= _MAX_STOP_NEW_RECEIPTS:
                raise ServiceRegistryStopNewError("RECEIPT_CAPACITY")

            # This request has passed the exact active-run check. Seal MIG03
            # immediately before the first durable owner mutation, within the
            # same Registry transaction that will publish the fence/receipt.
            try:
                self._record_migration_write_attempt(fact_kind="ownership")
            except MigrationGateError as error:
                if error.code == _MIGRATION_WRITE_DISABLED:
                    raise ServiceRegistryStopNewError(_MIGRATION_WRITE_DISABLED) from None
                raise ServiceRegistryStopNewError("MIGRATION_BOUNDARY_UNAVAILABLE") from None
            except OSError:
                raise ServiceRegistryStopNewError("MIGRATION_BOUNDARY_UNAVAILABLE") from None

            if claim["stop_new_command_id"] is None:
                updated = self._db.execute(
                    "UPDATE strategy_write_claims SET stop_new_at=?, stop_new_command_id=? "
                    "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=? "
                    "AND run_id=? AND stop_new_command_id IS NULL",
                    (
                        now,
                        command_id,
                        scope.execution_domain_ref,
                        scope.strategy_ref,
                        scope.account_ref,
                        scope.run_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise ServiceRegistryStopNewError("FENCE_STATE_CHANGED")
                effect_result = {"action": "stop_new", "result": "fenced"}
            else:
                effect_result = {"action": "stop_new", "result": "already_fenced"}

            self._db.execute(
                "INSERT INTO strategy_stop_new_receipts "
                "(command_id, scope_digest, status, effect_ref, effect_result_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    command_id,
                    scope_digest,
                    "applied",
                    effect_ref,
                    json.dumps(effect_result, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            row = self._db.execute(
                "SELECT * FROM strategy_stop_new_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                raise ServiceRegistryStopNewError("RECEIPT_UNAVAILABLE")
            return self._stop_new_receipt_from_row(row)

    @staticmethod
    def _stop_new_receipt_from_row(row: sqlite3.Row) -> Mapping[str, Any]:
        try:
            result = json.loads(row["effect_result_json"])
        except (TypeError, json.JSONDecodeError):
            raise ServiceRegistryStopNewError("RECEIPT_INVALID") from None
        if (
            row["status"] not in {"applied", "rejected"}
            or not isinstance(result, dict)
            or set(result) != {"action", "result"}
            or result.get("action") != "stop_new"
            or (
                row["status"] == "applied"
                and (
                    row["effect_ref"]
                    != "strategy-stop-new-v1:" + str(row["scope_digest"])
                    or result.get("result") not in {"fenced", "already_fenced"}
                )
            )
            or (
                row["status"] == "rejected"
                and (row["effect_ref"] is not None or result.get("result") != "run_not_active")
            )
        ):
            raise ServiceRegistryStopNewError("RECEIPT_INVALID")
        return MappingProxyType(
            {
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "effect_ref": row["effect_ref"],
                "effect_result": MappingProxyType(result),
            }
        )

    def _strategy_write_is_stopped(self, scope: WireScope) -> bool:
        if (
            scope.strategy_ref is None
            or scope.run_id is None
            or (scope.account_ref is None and not scope.account_refs)
        ):
            return False
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        return any(
            row is not None
            and row["run_id"] == scope.run_id
            and row["stop_new_command_id"] is not None
            for row in (
                self._db.execute(
                    "SELECT run_id, stop_new_command_id FROM strategy_write_claims "
                    "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
                    (scope.execution_domain_ref, scope.strategy_ref, account_ref),
                ).fetchone()
                for account_ref in accounts
            )
        )

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
                if (
                    row["policy_version"] != self.policy_version
                    or row["requirements_version"] != self.requirements_version
                    or not self._registration_policy_matches(row)
                ):
                    return self._failure("POLICY_REQUIREMENT_UNMET")
                if not self._ready and not self._restricted_renewal_is_allowed(
                    principal,
                    row,
                    identity=stored_identity,
                    scope=stored_scope,
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

    def _restricted_renewal_is_allowed(
        self,
        principal: str,
        row: sqlite3.Row,
        *,
        identity: WireIdentity,
        scope: WireScope,
    ) -> bool:
        """Allow a degraded renewal only for an existing restricted write.

        The renewal cannot add operations or change a registration's identity,
        scope, or grant. Its already-held restricted write must still be
        declared, granted, in-scope, and accepted by its trusted readiness
        callback. Owner cancellation is even narrower: the registration must
        contain exactly the ``owner.cancel`` operation and no other write.
        This callback is expected to be a fast, side-effect-free health
        projection; it runs under the registry transaction so expiry and the
        renewal decision share one serialized snapshot.
        """

        instance_id = row["instance_id"]
        port = row["port"]
        scope_ref = row["scope_ref"]
        registration_id = row["registration_id"]
        operations = frozenset(json.loads(row["operations_json"]))
        write_operations = frozenset(json.loads(row["write_operations_json"]))
        owner_cancel_route = port == _OWNER_CANCEL_OPERATION[0]
        if owner_cancel_route:
            if (
                operations != frozenset({_OWNER_CANCEL_OPERATION[1]})
                or write_operations != frozenset({_OWNER_CANCEL_OPERATION[1]})
            ):
                return False
            expected_operation = _OWNER_CANCEL_OPERATION[1]
        elif port == _RECOVERY_CLOSE_OPERATION[0]:
            expected_operation = None
        else:
            return False
        capability = self._capabilities.get((instance_id, port))
        if capability is None:
            return False
        restricted = tuple(
            (operation, readiness)
            for (restricted_instance, operation), readiness in self._restricted_readiness.items()
            if restricted_instance == instance_id
            and operation in operations
            and operation in write_operations
            and operation in capability.write_operations
            and (instance_id, operation) in self._handlers
        )
        # service.renew has no operation field, so an ambiguous registration
        # with multiple restricted writes cannot select which exception it is
        # extending. Require exactly one such already-granted route.
        if len(restricted) != 1:
            return False
        operation, readiness = restricted[0]
        if expected_operation is not None and operation != expected_operation:
            return False
        if not self._current_resource_allows(
            principal,
            identity,
            instance_id=instance_id,
            port=port,
            scope_ref=scope_ref,
            scope=scope,
            operation=operation,
        ):
            return False
        if identity.role is WireRole.STRATEGY_CLIENT and not self._strategy_claim_exists(
            scope, registration_id
        ):
            return False
        if (
            identity.role is WireRole.SERVICE
            and _is_exact_owner_cancel_scope(
                identity,
                port=port,
                scope=scope,
                operation=operation,
            )
            and not self._strategy_owner_run_claim_exists(scope)
        ):
            return False
        if (
            identity.role is WireRole.SERVICE
            and scope.strategy_ref is not None
            and not (
                _is_exact_owner_cancel_scope(
                    identity, port=port, scope=scope, operation=operation
                )
                or _is_exact_owner_reduce_scope(
                    identity, port=port, scope=scope, operation=operation
                )
            )
            and not self._trusted_group_write_run_matches(
                principal,
                identity,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            )
        ):
            return False
        try:
            return readiness() is True
        except Exception:
            return False

    def _unregister(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        return self._renew_or_unregister(principal, request, renew=False)

    def _direct_dispatch_admission_error(
        self,
        registration_id: str,
        service_epoch: str,
        instance_id: str,
        principal: str,
        identity: WireIdentity,
        scope: WireScope,
        operation: str = "direct.dispatch",
    ) -> str | None:
        """Recheck the exact registered direct operation under a Registry snapshot."""
        if type(operation) is not str or operation not in ({"direct.reserve", "direct.dispatch", "direct.cancel"} | _GATEWAY_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS):
            return "CAPABILITY_UNAVAILABLE"
        if self._closed or self._db is None:
            return "CLOSED"
        if service_epoch != self._epoch:
            return "STALE_EPOCH"
        row = self._db.execute(
            "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
            (registration_id, principal, service_epoch),
        ).fetchone()
        margin = self._dispatch_lease_margin_seconds if operation == "direct.dispatch" else 0.0
        if (row is None or type(row["expires_at"]) not in (int, float)
                or row["expires_at"] <= self._now() + margin):
            return "NOT_REGISTERED"
        observer = operation in _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS
        expected_port = "service.account_read" if observer else "service.direct_write"
        if row["instance_id"] != instance_id or row["port"] != expected_port:
            return "DENIED"
        if self._stored_identity(row["identity_json"]) != identity:
            return "DENIED"
        if self._stored_scope(row["scope_json"]) != scope:
            return "SCOPE_DENIED"
        if observer and not self._account_observer_registration_unique(instance_id=instance_id,
                principal=principal, identity=identity, scope=scope, registration_id=registration_id, operation=operation):
            return 'SCOPE_DENIED'
        expected_role = (WireRole.APPLICATION_CLIENT if operation in _GATEWAY_CONTROL_OPERATIONS else
                         WireRole.SERVICE if operation in _GATEWAY_ALL_DELEGATE_OPERATIONS else WireRole.STRATEGY_CLIENT)
        allowed_roles = ({WireRole.STRATEGY_CLIENT, WireRole.SERVICE, WireRole.APPLICATION_CLIENT} if observer else
                         {WireRole.STRATEGY_CLIENT, WireRole.SERVICE} if operation in _GATEWAY_PARENT_OPERATIONS else {expected_role})
        if identity.role not in allowed_roles or identity.delegate_ref is not None or not self._identity_is_trusted(principal, identity):
            return "DENIED"
        if not self._ready:
            return "POLICY_REQUIREMENT_UNMET"
        if (
            row["policy_version"] != self.policy_version
            or row["requirements_version"] != self.requirements_version
            or not self._registration_policy_matches(row)
        ):
            return "POLICY_REQUIREMENT_UNMET"
        capability = self._capabilities.get((instance_id, row["port"]))
        if (
            capability is None
            or operation not in capability.operations
            or (operation not in (_GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS) and operation not in capability.write_operations)
            or operation not in frozenset(json.loads(row["operations_json"]))
            or (operation not in (_GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS) and operation not in frozenset(json.loads(row["write_operations_json"])))
            or (instance_id, operation) not in self._handlers
        ):
            return "CAPABILITY_UNAVAILABLE"
        if not self._current_resource_allows(
            principal, identity, instance_id=instance_id, port=row["port"],
            scope_ref=row["scope_ref"], scope=scope, operation=operation,
        ):
            return "SCOPE_DENIED"
        if operation in _GATEWAY_PARENT_OPERATIONS:
            if (self.gateway_parent_grant(instance_id=instance_id,scope_ref=row['scope_ref'],identity=identity,
                                          scope=scope,operation=operation) is None
                    or not self._gateway_delegate_strategy_run_current(scope)):
                return 'SCOPE_DENIED'
        elif operation in _GATEWAY_ALL_DELEGATE_OPERATIONS:
            if self.gateway_delegate_grant(instance_id=instance_id,scope_ref=row['scope_ref'],identity=identity,
                                           scope=scope,operation=operation) is None:
                return 'SCOPE_DENIED'
            if not self._gateway_delegate_strategy_run_current(scope):
                return "DUPLICATE_ACTIVE_RUN"
        elif operation not in (_GATEWAY_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS) and not self._strategy_claim_exists(scope, registration_id):
            return "DUPLICATE_ACTIVE_RUN"
        if operation in _STOP_NEW_OPERATIONS and self._strategy_write_is_stopped(scope):
            return "STRATEGY_STOPPED"
        return None

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
        read_only_market = request.header.operation in _READ_ONLY_MARKET_OPERATIONS
        now = self._now()
        with (self._read_snapshot() if read_only_market else self._transaction()):
            if not read_only_market:
                self._expire_sessions(now)
            row = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
                (payload["registration_id"], principal, self._epoch),
            ).fetchone()
            if row is None:
                return self._failure("NOT_REGISTERED")
            if read_only_market and (
                type(row["expires_at"]) not in (int, float)
                or row["expires_at"] <= now
            ):
                return self._failure("NOT_REGISTERED")
            identity = self._stored_identity(row["identity_json"])
            scope = self._stored_scope(row["scope_json"])
            if request.header.identity != identity or request.header.scope != scope:
                return self._failure(
                    "DENIED" if request.header.identity != identity else "SCOPE_DENIED"
                )
            if not self._identity_is_trusted(principal, identity):
                return self._failure("DENIED")
            instance_id = row["instance_id"]
            operation = request.header.operation
            restricted_readiness = self._restricted_readiness.get(
                (instance_id, operation)
            )
            if not self._ready and restricted_readiness is None:
                return self._failure("POLICY_REQUIREMENT_UNMET")
            if (
                row["policy_version"] != self.policy_version
                or row["requirements_version"] != self.requirements_version
                or not self._registration_policy_matches(row)
            ):
                return self._failure("POLICY_REQUIREMENT_UNMET")
            if payload["instance_id"] != instance_id:
                return self._failure("DENIED")
            capability = self._capabilities.get((instance_id, row["port"]))
            granted = frozenset(json.loads(row["operations_json"]))
            write_ops = frozenset(json.loads(row["write_operations_json"]))
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
            if (capability.port == 'service.direct_write' and operation in {'direct.reserve', 'direct.dispatch', 'direct.delegated_reserve', 'direct.delegated_dispatch'}
                    and scope.account_ref is not None and scope.strategy_ref is not None
                    and self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gateway_lease_expirations'").fetchone()
                    and self._db.execute("SELECT 1 FROM gateway_lease_expirations WHERE instance_id=? AND account_ref=? AND strategy_ref=? AND state='PENDING' LIMIT 1",
                        (instance_id, scope.account_ref, scope.strategy_ref)).fetchone()):
                return self._failure("STRATEGY_STOPPED")
            if capability.port == "service.account_read" and operation in {
                "account.instrument_spec.begin", "account.instrument_spec.status",
                "account.instrument_spec.cancel",
            } and any(
                required not in granted or not self._current_resource_allows(
                    principal, identity, instance_id=instance_id, port=row["port"],
                    scope_ref=row["scope_ref"], scope=scope, operation=required,
                ) for required in (
                    "account.instrument_spec.begin", "account.instrument_spec.status",
                    "account.instrument_spec.cancel",
                )
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
                if (capability.port == 'service.direct_write' and operation in _GATEWAY_PARENT_OPERATIONS
                        and self.gateway_parent_grant(instance_id=instance_id,scope_ref=row['scope_ref'],
                            identity=identity,scope=scope,operation=operation) is not None):
                    if not self._gateway_delegate_strategy_run_current(scope):
                        return self._failure('DUPLICATE_ACTIVE_RUN')
                elif (capability.port == 'service.direct_write' and operation in _GATEWAY_ALL_DELEGATE_OPERATIONS
                        and self.gateway_delegate_grant(instance_id=instance_id,scope_ref=row['scope_ref'],
                            identity=identity,scope=scope,operation=operation) is not None):
                    if not self._gateway_delegate_strategy_run_current(scope):
                        return self._failure("DUPLICATE_ACTIVE_RUN")
                elif _is_exact_owner_cancel_scope(
                    identity, port=row["port"], scope=scope, operation=operation
                ) or _is_exact_owner_reduce_scope(
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
            budget_checker = self._reply_result_budget_checker
            use_result_budget = (
                capability.port == "service.market_source"
                and operation == "market.read_after_bounded"
                and budget_checker is not None
            )
            if use_result_budget:
                result_budget: int | None = None

                def result_fits(result: Mapping[str, Any]) -> bool:
                    nonlocal result_budget
                    normalized_result = self._normalize_result(result)
                    encoded_result = encode_wire_value(normalized_result)
                    if len(encoded_result) > _MAX_RESULT_BYTES:
                        return False
                    if result_budget is None:
                        if budget_checker is None:
                            return False
                        candidate_budget = budget_checker(request, reply_limit)
                        if type(candidate_budget) is not int:
                            raise TypeError("reply result budget checker must return int")
                        result_budget = candidate_budget
                    return result_budget >= 0 and len(encoded_result) <= result_budget

            else:

                def result_fits(result: Mapping[str, Any]) -> bool:
                    normalized_result = self._normalize_result(result)
                    encoded_result = encode_wire_value(normalized_result)
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

            admission = None
            if ((capability.port == "service.direct_write"
                    and operation in ({"direct.reserve", "direct.dispatch", "direct.cancel"} | _GATEWAY_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS))
                    or (capability.port == "service.account_read" and operation in _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS)):
                try:
                    admission_error = self._direct_dispatch_admission_error(
                        row["registration_id"], self._epoch, instance_id, principal, identity, scope,
                        operation,
                    )
                except Exception:
                    return self._failure("UNAVAILABLE")
                if admission_error is not None:
                    return self._failure(admission_error)
                admission = ServiceDispatchAdmission(
                    _DISPATCH_ADMISSION_ISSUER, self,
                    registration_id=row["registration_id"], service_epoch=self._epoch,
                    instance_id=instance_id, principal=principal, identity=identity, scope=scope,
                    arguments=payload["arguments"], operation=operation,
                )
                self._dispatch_admissions[admission] = admission._binding
            arguments = MappingProxyType(dict(payload["arguments"]))
            context = ServiceCallContext(
                identity,
                scope,
                row["registration_id"],
                principal,
                result_fits,
                admission,
                (_REGISTRY_WIRE_ARGUMENTS, arguments),
            )
        if restricted_readiness is not None:
            try:
                if restricted_readiness() is not True:
                    return self._failure("POLICY_REQUIREMENT_UNMET")
            except Exception:
                return self._failure("POLICY_REQUIREMENT_UNMET")
        elif not self._ready:
            # Preserve the global readiness fence if it changed after the
            # earlier authorization-phase check.
            return self._failure("POLICY_REQUIREMENT_UNMET")
        write_fact_kind = None
        if operation in write_ops:
            write_fact_kind = "dispatch"
        elif capability.port == "service.account_read" and (
            operation in {"account.ack", "account.events.read_after", "account.events.ack", "account.facts.read_after", "account.facts.ack", "account.facts.snapshot_read", "account.facts.snapshot_confirm"}
            or (
                operation == "account.read_after"
                and arguments.get("track_delivery", False) is True
            )
        ):
            # Tracked reads durably advance offered_cursor just like an ACK is
            # a durable cursor transition. Fence both before entering Gateway.
            write_fact_kind = "cursor"
        elif capability.port == "service.direct_write" and operation in {
            "direct.events.read_after", "direct.events.ack", "direct.delegate_events.read_after", "direct.delegate_events.ack"
        }:
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
        # A stopped submit is still a write attempt and must cross the MIG03
        # read-only/seal boundary above. It is rejected here, before the
        # execution handler, so it never becomes a provider dispatch.
        if operation in _STOP_NEW_OPERATIONS and self._strategy_write_is_stopped(scope):
            return self._failure("STRATEGY_STOPPED")
        try:
            result = handler(context, arguments)
            if not isinstance(result, Mapping):
                return self._failure("HANDLER_FAILED")
            if (capability.port == "service.telemetry_ingress" and operation == "monitor.heartbeat") or (
                capability.port == "service.market_source" and operation == "market.clock_observation"
            ) or (
                capability.port == "service.direct_write"
                and operation in {"direct.strategy_position", "direct.strategy_close_capacity", "direct.strategy_financial"}
            ) or (
                capability.port == "service.direct_write" and operation in {"direct.reserve", "direct.delegated_reserve"}
                and result.get("status") == "REQUEST_REJECTED"
                and result.get("code") in {"STRATEGY_LEDGER_UNAVAILABLE", "STRATEGY_CLOSE_EXCEEDS",
                                           "SYSTEM_ORDER_UNKNOWN", "UNKNOWN_SCOPE_UNAVAILABLE", "STRATEGY_ORIGIN_UNAVAILABLE"}
            ) or (
                capability.port == "service.account_read" and operation == "account.strategy_net_comparison"
            ):
                with self._read_snapshot():
                    current = self._db.execute(
                        "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=? AND expires_at>?",
                        (context.registration_id, principal, self._epoch, self._now()),
                    ).fetchone()
                    if (current is None or current["instance_id"] != instance_id
                            or current["port"] != capability.port or not self._ready
                            or current["policy_version"] != self.policy_version
                            or current["requirements_version"] != self.requirements_version
                            or not self._registration_policy_matches(current)
                            or self._stored_identity(current["identity_json"]) != identity
                            or self._stored_scope(current["scope_json"]) != scope
                            or operation not in frozenset(json.loads(current["operations_json"]))
                            or not self._identity_is_trusted(principal, identity)
                            or not self._current_resource_allows(principal, identity, instance_id=instance_id,
                                port=capability.port, scope_ref=current["scope_ref"], scope=scope, operation=operation)):
                        return self._failure("SCOPE_DENIED")
            if ((capability.port == "service.direct_write" and operation in (_GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS))
                    or (capability.port == "service.account_read" and operation in _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS)):
                with self._read_snapshot():
                    code = self._direct_dispatch_admission_error(context.registration_id, self._epoch,
                        instance_id, principal, identity, scope, operation)
                    if code is not None:
                        return self._failure(code)
            if capability.port == "service.account_read" and operation in {
                "account.instrument_spec.begin", "account.instrument_spec.status",
                "account.instrument_spec.cancel",
            }:
                # Recheck the live registration and grant after the handler, so
                # a revoked or expired lease cannot publish a cached result.
                with self._read_snapshot():
                    current = self._db.execute(
                        "SELECT * FROM sessions WHERE registration_id=? AND principal=? "
                        "AND service_epoch=? AND expires_at>?",
                        (context.registration_id, principal, self._epoch, self._now()),
                    ).fetchone()
                    if (current is None or current["instance_id"] != instance_id
                            or current["port"] != capability.port
                            or current["policy_version"] != self.policy_version
                            or current["requirements_version"] != self.requirements_version
                            or not self._registration_policy_matches(current)
                            or self._stored_identity(current["identity_json"]) != identity
                            or self._stored_scope(current["scope_json"]) != scope
                            or operation not in frozenset(json.loads(current["operations_json"]))
                            or not self._ready or not self._identity_is_trusted(principal, identity)
                            or any(required not in frozenset(json.loads(current["operations_json"]))
                                or not self._current_resource_allows(
                                    principal, identity, instance_id=instance_id,
                                    port=capability.port, scope_ref=current["scope_ref"],
                                    scope=scope, operation=required)
                                for required in (
                                    "account.instrument_spec.begin", "account.instrument_spec.status",
                                    "account.instrument_spec.cancel"))):
                        return self._failure("SCOPE_DENIED")
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
                    ("service.account_read", "account.instrument_spec.begin"),
                    ("service.account_read", "account.instrument_spec.status"),
                    ("service.account_read", "account.instrument_spec.cancel"),
                    ("service.monitor_query", "telemetry.snapshot"),
                    ("service.market_source", "market.read_after_bounded"),
                    ("service.direct_write", "direct.events.read_after"),
                    ("service.direct_write", "direct.events.progress"),
                    ("service.direct_write", "direct.events.ack"),
                }
                and operation not in write_ops
            ):
                return self._failure("REPLY_TOO_LARGE")
            return self._failure("HANDLER_FAILED")
        except Exception as error:
            if type(error) is ServiceOperationDeniedError and (capability.port, operation) in {
                ("service.market_source", "market.subscribe"),
                # Iteration 60 WP6: a revoked market key is a definitive,
                # side-effect-free denial for these reads too.
                ("service.market_source", "market.head"),
                ("service.market_source", "market.snapshot"),
                ("service.market_source", "market.read_after"),
                ("service.market_source", "market.read_after_bounded"),
                ("service.market_source", "market.clock_observation"),
                ("service.market_source", "market.history"),
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
        if operation in capability.write_operations and not (
            _is_exact_owner_cancel_scope(
                identity, port=port, scope=scope, operation=operation
            )
            or _is_exact_owner_reduce_scope(
                identity, port=port, scope=scope, operation=operation
            )
        ):
            if (identity.role is WireRole.APPLICATION_CLIENT and port == "service.direct_write"
                    and operation in _GATEWAY_CONTROL_WRITE_OPERATIONS):
                if (scope.account_ref is None or scope.account_refs or scope.manual_book_ref is not None
                        or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None
                        or identity.delegate_ref is not None):
                    return False
            elif (identity.role is WireRole.SERVICE and port == 'service.direct_write'
                  and operation in _GATEWAY_PARENT_WRITE_OPERATIONS
                  and self.gateway_parent_grant(instance_id=instance_id,scope_ref=scope_ref,
                      identity=identity,scope=scope,operation=operation) is not None):
                pass
            elif (identity.role is WireRole.SERVICE and port == 'service.direct_write'
                  and operation in (_GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS)
                  and self.gateway_delegate_grant(instance_id=instance_id,scope_ref=scope_ref,
                      identity=identity,scope=scope,operation=operation) is not None):
                pass
            elif identity.role is WireRole.STRATEGY_CLIENT:
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
            and self._resource_scope_matches(resource, scope, identity)
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
        """Require a live durable strategy/account/run claim for owner controls."""

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
