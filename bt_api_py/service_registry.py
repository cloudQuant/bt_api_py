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
import sys
import threading
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import BuiltinFunctionType, MappingProxyType
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
from bt_api_base.market_protocol import MarketInstrumentKey

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
from .execution_parent_grant import (
    ExecutionParentGrant, OPERATIONS as _EXECUTION_PARENT_OPERATIONS,
    PORT as _EXECUTION_PARENT_PORT, QUERY as _EXECUTION_PARENT_QUERY,
    SUBMIT as _EXECUTION_PARENT_SUBMIT,
)
from .execution_action_grant import (
    ExecutionActionGrant, ExecutionActionRequest,
    allows as _execution_action_allows, grant_key as _execution_action_key,
)
from .execution_parent_replay import (
    ExecutionParentReplayProof,
    execution_parent_replay_namespace,
)

_EXECUTION_RELEASE_PORT = 'service.execution_delegate'
_EXECUTION_RELEASE_CHECK = 'execution.child.release_policy_check'
_EXECUTION_RELEASE_OPERATIONS = frozenset({_EXECUTION_RELEASE_CHECK})
_CALENDAR_READ_OPERATIONS = frozenset({"calendar.query.v1", "calendar.windows.v1"})
_CALENDAR_MONITOR_OPERATION = "calendar.monitor.windows.v1"
_PRODUCT_PORT = "service.instrument_product_read"
_PRODUCT_OPERATIONS = frozenset({"instrument.product.begin.v1", "instrument.product.status.v1", "instrument.product.cancel.v1"})


def _execution_release_local_policy_handler(_context, _arguments):
    """Pure local observation; does not issue a claim or any send authority."""
    return {'schema_version': 1, 'status': 'LOCAL_POLICY_ONLY'}

# Captured before any application-supplied Registry clock.  Intake does not
# call an operator callback while its caller holds an Execution journal lock.
_INTAKE_REALTIME = time.time
_INTAKE_MONOTONIC = time.monotonic
_INTAKE_GETPID = os.getpid
_INTAKE_CURRENT_THREAD = threading.current_thread
_INTAKE_GET_IDENT = threading.get_ident
# macOS clock_gettime(CLOCK_MONOTONIC) includes system sleep; Python's
# time.monotonic() uses mach_absolute_time(), which does not. Other platforms
# deliberately remain unavailable until their suspend semantics are verified.
_RELEASE_SLEEP_CLOCK = getattr(time, 'clock_gettime', None)
_RELEASE_SLEEP_CLOCK_ID = getattr(time, 'CLOCK_MONOTONIC', None) if sys.platform == 'darwin' else None
# This clock is separate from the release port's platform contract.
_PARENT_SLEEP_CLOCK_ID = (getattr(time, 'CLOCK_MONOTONIC', None) if sys.platform == 'darwin'
                          else getattr(time, 'CLOCK_BOOTTIME', None) if sys.platform.startswith('linux')
                          else None)

_INSTRUMENT_FIELDS = ('provider', 'venue', 'environment', 'product_type',
                      'instrument_id', 'native_symbol')


def _execution_action_facts(request):
    """Capture value facts without trusting a wire identity as authority."""
    identity, instrument = request.identity, request.instrument
    return (request.instance_id, identity.role, identity.principal_ref, identity.actor_ref,
            identity.delegate_ref, request.account_ref, request.strategy_ref,
            request.execution_domain_ref, request.grant_revision,
            tuple(getattr(instrument, name) for name in _INSTRUMENT_FIELDS),
            request.plan_version, request.action)


def _action_parent_facts_match(request, parent_arguments):
    """Compare a detached trusted journal projection or original submit arguments."""
    if (type(parent_arguments) not in (dict, MappingProxyType)
            or type(parent_arguments.get('spec')) not in (dict, MappingProxyType)):
        return False
    spec = parent_arguments['spec']
    instrument = spec.get('instrument')
    if type(instrument) not in (dict, MappingProxyType) or set(instrument) != set(_INSTRUMENT_FIELDS):
        return False
    if any(type(instrument[name]) is not str for name in _INSTRUMENT_FIELDS):
        return False
    return (tuple(instrument[name] for name in _INSTRUMENT_FIELDS)
            == tuple(getattr(request.instrument, name) for name in _INSTRUMENT_FIELDS)
            and type(spec.get('plan_version')) is str
            and spec['plan_version'] == request.plan_version
            and type(spec.get('account_scope')) is str
            and spec['account_scope'] == request.account_ref
            and type(spec.get('strategy_scope')) is str
            and spec['strategy_scope'] == request.strategy_ref
            and type(spec.get('execution_domain_ref')) is str
            and spec['execution_domain_ref'] == request.execution_domain_ref)


def _release_action_facts_match(request, arguments):
    """Match original wire claims; these claims are not journal evidence."""
    if type(arguments) not in (dict, MappingProxyType) or set(arguments) != {
            'schema_version', 'operation_id', 'parent_ref', 'child_id', 'account_ref',
            'strategy_ref', 'execution_domain_ref', 'instrument', 'plan_version', 'grant_revision'}:
        return False
    if type(arguments['schema_version']) is not int or arguments['schema_version'] != 1:
        return False
    if any(not _text(arguments[key]) for key in ('operation_id', 'parent_ref', 'child_id',
            'account_ref', 'strategy_ref', 'execution_domain_ref', 'plan_version', 'grant_revision')):
        return False
    instrument = arguments['instrument']
    return (type(instrument) in (dict, MappingProxyType) and set(instrument) == set(_INSTRUMENT_FIELDS)
            and all(type(instrument[name]) is str for name in _INSTRUMENT_FIELDS)
            and tuple(instrument[name] for name in _INSTRUMENT_FIELDS)
                == tuple(getattr(request.instrument, name) for name in _INSTRUMENT_FIELDS)
            and arguments['account_ref'] == request.account_ref
            and arguments['strategy_ref'] == request.strategy_ref
            and arguments['execution_domain_ref'] == request.execution_domain_ref
            and arguments['plan_version'] == request.plan_version
            and arguments['grant_revision'] == request.grant_revision)

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
_MAX_FREEZE_RECEIPTS = 10_000
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
_GATEWAY_CONTROL_WRITE_OPERATIONS = frozenset({"direct.control.apply", "direct.control.resume", "direct.control.drain_target", "direct.control.reconcile", "direct.control.cancel_scope"})
_GATEWAY_CONTROL_READ_OPERATIONS = frozenset({"direct.control.identity", "direct.control.status", "direct.control.targets", "direct.control.list", "direct.control.resume_status", "direct.control.target_status", "direct.control.cancel_scope_status"})
_GATEWAY_CONTROL_PURE_READ_OPERATIONS = frozenset({"direct.control.identity", "direct.control.cancel_scope_status"})
_GATEWAY_CONTROL_OPERATIONS = _GATEWAY_CONTROL_WRITE_OPERATIONS | _GATEWAY_CONTROL_READ_OPERATIONS
_GATEWAY_CONTROL_INTENT_WRITE_OPERATIONS = frozenset({"direct.control.intent.apply"})
_GATEWAY_AUTHENTICATED_CONTROL_WRITE_OPERATIONS = (
    _GATEWAY_CONTROL_WRITE_OPERATIONS | _GATEWAY_CONTROL_INTENT_WRITE_OPERATIONS
)
_GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS = (
    _GATEWAY_CONTROL_OPERATIONS | _GATEWAY_CONTROL_INTENT_WRITE_OPERATIONS
)
_GATEWAY_DELEGATED_READ_OPERATIONS = frozenset({"direct.delegated_command_status", "direct.delegated_cancel_status"})
_GATEWAY_STRATEGY_DELEGATED_CANCEL = frozenset({"direct.delegated_cancel"})
_GATEWAY_RISK_FIRST_RESERVE_V2 = "direct.risk_first_reserve.v2"
_GATEWAY_DELEGATED_WRITE_OPERATIONS = frozenset({
    "direct.delegated_reserve", "direct.delegated_dispatch", _GATEWAY_RISK_FIRST_RESERVE_V2,
})
_GATEWAY_DELEGATE_STREAM_OPERATIONS = frozenset({'direct.delegate_events.read_after', 'direct.delegate_events.progress', 'direct.delegate_events.ack'})
_GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_cancel_status', 'direct.delegate_actor_release_status', 'direct.delegate_actor_status_v2', 'direct.delegate_actor_release_status_v2'}) | _GATEWAY_DELEGATE_STREAM_OPERATIONS
_GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS = frozenset({'direct.delegate_actor_cancel', 'direct.delegate_actor_release'})
_GATEWAY_DELEGATE_ACTOR_OPERATIONS = _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS
_GATEWAY_ALL_DELEGATE_OPERATIONS = _GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_OPERATIONS
_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS = frozenset({
    "account.events.read_after", "account.events.progress", "account.events.ack",
    "account.facts.read_after", "account.facts.read_after.v2", "account.facts.progress", "account.facts.ack", "account.facts.status",
    "account.facts.snapshot_info", "account.facts.snapshot_read", "account.facts.snapshot_confirm",
    "account.cancel_attempts.read_after", "account.cancel_attempts.progress", "account.cancel_attempts.ack",
    "account.order_cut.current.v1", "account.order_cut.query_page.v1", "account.order_cut.receipt_page.v1",
})
_GATEWAY_ORDER_CUT_OPERATIONS = frozenset({"account.order_cut.current.v1", "account.order_cut.query_page.v1", "account.order_cut.receipt_page.v1"})
_GATEWAY_PARENT_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.query', 'direct.parent.cancel', 'direct.parent.operation', 'direct.parent.source', 'direct.parent.register_v2', 'direct.parent.operation_v2', 'direct.parent.cancel_v2', 'direct.parent.fault', 'direct.parent.recover'})
_GATEWAY_PARENT_READ_OPERATIONS = frozenset({'direct.parent.query', 'direct.parent.operation', 'direct.parent.source', 'direct.parent.operation_v2'})
_GATEWAY_ACCEPTANCE_OPERATION = 'direct.parent.acceptance_order'
_GATEWAY_PARENT_WRITE_OPERATIONS = _GATEWAY_PARENT_OPERATIONS - _GATEWAY_PARENT_READ_OPERATIONS
_GATEWAY_STRATEGY_FINANCIAL_OPERATIONS = frozenset({'direct.strategy_financial'})
# Account-wide reads of every strategy's funds and book, for an account operator only (G64-06).
_GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS = frozenset({'account.strategy_financial', 'account.strategy_book'})
_RISK_V2_RESERVE = "risk.order.reserve.v2"
_RISK_V2_DISPATCH = "risk.order.dispatch.v2"
_RISK_V2_ADMISSION_OPERATIONS = frozenset({_RISK_V2_RESERVE, _RISK_V2_DISPATCH})
_RISK_EXECUTION_PARENT_WRITE_OPERATIONS = frozenset(
    {"risk.parent.reserve", "risk.parent.release", _RISK_V2_RESERVE, _RISK_V2_DISPATCH}
)
_RISK_ADMIN_CANCEL = "risk.cancel.request"
_RISK_ADMIN_WRITE_OPERATIONS = frozenset({
    "risk.policy.publish", "risk.latch.release", _RISK_ADMIN_CANCEL,
})
_FREEZE_OPERATIONS = frozenset(
    {
        "direct.reserve",
        "direct.dispatch",
        "direct.delegated_reserve",
        "direct.delegated_dispatch",
        _GATEWAY_RISK_FIRST_RESERVE_V2,
        "groups.submit",
        "orders.submit",
        "orders.submit_approved_target",
        "orders.submit_group_leg",
        "reservations.reserve",
        _RISK_V2_RESERVE,
        _RISK_V2_DISPATCH,
        _EXECUTION_PARENT_SUBMIT,
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
# Only explicitly audited port/operation pairs may use an epoch-local key.
# Unlisted reads, cursor operations and plugins keep the durable key contract.
_TRANSIENT_READ_REGISTRATIONS = frozenset({
    ('service.account_read', 'account.read'),
    ('service.account_read', 'account.snapshot'),
    ('service.account_read', 'account.snapshot.typed'),
    ('service.account_read', 'account.head'),
    ('service.order_query', 'orders.query'),
    ('service.execution_parent', 'execution.parent.query'),
    ('service.monitor_query', 'monitor.query'),
    ('service.monitor_query', 'monitor.read_after'),
    *(('service.market_source', operation)
      for operation in _READ_ONLY_MARKET_OPERATIONS - {'market.clock_observation'}),
})
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


def _is_execution_release_scope(identity, port, scope):
    """A SERVICE scope independent of a strategy's live registration.

    A run claim cannot be inferred here; the owner must compare the original
    run, request and frozen child facts against its own trusted journal.
    """
    return (type(identity) is WireIdentity and identity.role is WireRole.SERVICE
            and identity.delegate_ref is None and port == _EXECUTION_RELEASE_PORT
            and type(scope) is WireScope and scope.account_ref is not None
            and scope.strategy_ref is not None and scope.run_id is None
            and not scope.account_refs and scope.manual_book_ref is None
            and scope.portfolio_ref is None and scope.group_id is None and scope.leg_id is None)


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


def _risk_admin_write_scope(identity: WireIdentity, scope: WireScope) -> bool:
    """Operator controls are account-scoped and never confer order ownership."""
    return (
        type(identity) is WireIdentity and identity.role is WireRole.APPLICATION_CLIENT
        and identity.delegate_ref is None and type(scope) is WireScope
        and scope.account_ref is not None and not scope.account_refs
        and scope.strategy_ref is None and scope.run_id is None
        and scope.manual_book_ref is None and scope.portfolio_ref is None
        and scope.group_id is None and scope.leg_id is None
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
    dispatch_admission: ServiceDispatchAdmission | RiskAdminCancelAdmission | None = None
    _wire_arguments: object = field(default=None, repr=False, compare=False)
    service_epoch: str | None = field(default=None, repr=False, compare=False)

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


class ServiceRegistryFreezeError(RuntimeError):
    """The owner could not persist a definitive strategy freeze receipt."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceRegistrySchemaError(RuntimeError):
    """The registry database schema cannot be migrated; the registry refuses to open."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Iteration 64 (design freeze section 9): the strategy-run fence blocks every
# submit-type operation, so it is a FREEZE. Ledgers written before the rename
# carry the STOP_NEW names below and are migrated once when the registry opens.
_LEGACY_FREEZE_RECEIPT_TABLE = "strategy_stop_new_receipts"
_FREEZE_RECEIPT_TABLE = "strategy_freeze_receipts"
_LEGACY_FREEZE_CLAIM_COLUMNS = (
    ("stop_new_at", "freeze_at"),
    ("stop_new_command_id", "freeze_command_id"),
)


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
    for capability in capabilities:
        if ((capability.port == _PRODUCT_PORT and
                (capability.operations != _PRODUCT_OPERATIONS or capability.write_operations))
                or (capability.port != _PRODUCT_PORT and capability.operations & _PRODUCT_OPERATIONS)):
            raise ValueError("invalid instrument product capability")
        if (_CALENDAR_MONITOR_OPERATION in capability.operations
                and (capability.port != "service.calendar_read" or capability.write_operations)):
            raise ValueError("monitor calendar operation requires its read-only port")
        if capability.port == _EXECUTION_RELEASE_PORT:
            if (not capability.operations <= _EXECUTION_RELEASE_OPERATIONS
                    or capability.write_operations):
                raise ValueError('invalid execution release capability')
        elif capability.operations & _EXECUTION_RELEASE_OPERATIONS:
            raise ValueError('execution release operation requires its own port')
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
                if (resource.port == _PRODUCT_PORT or resource.operations & _PRODUCT_OPERATIONS) and not (
                    entry.identity.role is WireRole.SERVICE and entry.identity.principal_ref == principal
                    and entry.identity.delegate_ref is None and resource.port == _PRODUCT_PORT
                    and resource.operations == _PRODUCT_OPERATIONS and resource.scope.account_ref is not None
                    and resource.scope == WireScope(resource.scope.execution_domain_ref, account_ref=resource.scope.account_ref)
                    and not capability.write_operations
                ):
                    raise ValueError("instrument product requires exact service account scope")
                if _CALENDAR_MONITOR_OPERATION in resource.operations and not (
                    entry.identity.role is WireRole.SERVICE
                    and entry.identity.principal_ref == principal
                    and entry.identity.delegate_ref is None
                    and resource.port == "service.calendar_read"
                    and resource.operations == frozenset({_CALENDAR_MONITOR_OPERATION})
                    and resource.scope.account_ref is not None
                    and resource.scope == WireScope(resource.scope.execution_domain_ref,
                                                    account_ref=resource.scope.account_ref)
                    and not capability.write_operations
                ):
                    raise ValueError("monitor calendar operation requires exact service account scope")
                if (resource.port == "service.calendar_read"
                        and resource.operations & _CALENDAR_READ_OPERATIONS
                        and entry.identity.role is not WireRole.STRATEGY_CLIENT):
                    raise ValueError("strategy calendar operations require strategy identity")
                if (resource.port == _EXECUTION_RELEASE_PORT
                        and not _is_execution_release_scope(entry.identity, resource.port, resource.scope)):
                    raise ValueError('execution release requires an exact service scope')
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
                    and not (
                        entry.identity.role is WireRole.STRATEGY_CLIENT
                        and entry.identity.delegate_ref is None
                        and resource.port == "service.calendar_read"
                        and resource.scope == WireScope(
                            resource.scope.execution_domain_ref,
                            account_ref=resource.scope.account_ref,
                            strategy_ref=entry.identity.actor_ref,
                            run_id=resource.scope.run_id,
                        )
                        and resource.scope.account_ref is not None
                        and resource.scope.strategy_ref is not None
                        and bool(resource.operations)
                        and resource.operations <= _CALENDAR_READ_OPERATIONS
                        and capability.operations <= (_CALENDAR_READ_OPERATIONS | {_CALENDAR_MONITOR_OPERATION})
                        and not capability.write_operations
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
class _ExecutionUpstreamLease:
    """Process-local snapshot of a verified SDK Gateway registration."""
    runtime: object
    runtime_vars: dict
    bindings: list
    index: int
    binding: object
    binding_vars: dict
    scope: object
    scope_vars: dict
    binding_facts: tuple
    scope_facts: tuple
    peers: dict
    peer: object
    proofs: dict
    proof_key: tuple
    proof: tuple
    transports: dict
    transport_key: tuple
    adapter: object
    adapter_vars: dict
    client: object
    client_vars: dict
    server_key: bytes
    deadline: float
    owner_ident: int
    runtime_identity: object
    runtime_identity_vars: dict
    runtime_identity_facts: tuple
    peer_vars: dict
    peer_facts: tuple
    runtime_session_id: str
    runtime_run_id: str
    clock_lease: object
    configured_peer: object
    configured_peer_vars: dict
    configured_peer_facts: tuple


class _IntakeScopeContext:
    """Do not unwind the SQLite/lock scope from a foreign thread or fork."""

    def __init__(self, manager, getpid, get_ident, current_thread):
        self._manager = manager
        self._getpid = getpid
        self._get_ident = get_ident
        self._current_thread = current_thread
        self._pid = None
        self._thread_ident = None
        self._thread = None

    def __enter__(self):
        if self._pid is not None:
            raise ServiceDispatchAdmissionError('ADMISSION_BUSY')
        if (type(self._getpid) is not BuiltinFunctionType or self._getpid.__name__ != 'getpid'
                or self._getpid.__module__ not in {'posix', 'nt'}
                or type(self._get_ident) is not BuiltinFunctionType
                or self._get_ident.__name__ != 'get_ident' or self._get_ident.__module__ != '_thread'
                or self._current_thread is not threading.current_thread):
            raise ServiceDispatchAdmissionError('INTAKE_RUNTIME_UNTRUSTED')
        self._pid = self._getpid()
        self._thread_ident = self._get_ident()
        self._thread = self._current_thread()
        return self._manager.__enter__()

    def __exit__(self, exc_type, exc, traceback):
        if self._getpid() != self._pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        if self._get_ident() != self._thread_ident or self._current_thread() is not self._thread:
            raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
        return self._manager.__exit__(exc_type, exc, traceback)


@dataclass(frozen=True, slots=True)
class _DispatchAdmissionBinding:
    registration_id: str
    service_epoch: str
    instance_id: str
    principal: str
    identity: WireIdentity
    scope: WireScope
    arguments: bytes = field(repr=False)
    pid: int
    operation: str
    issuer_thread: threading.Thread
    parent_grant: object | None = None
    delegate_grant: object | None = None
    parent_operation: str | None = None
    execution_parent_grant: object | None = None
    execution_parent_generation: int | None = None
    acceptance_grant: object | None = None
    acceptance_generation: int | None = None
    # Exact composition-prover proof for one read-only receipt replay. It is
    # never a grant, a live lease, or permission to insert a new row.
    execution_parent_replay: object | None = None


@dataclass(frozen=True, slots=True)
class ServiceDispatchRegistrationObservation:
    """Current Registry facts for one active owner-bound admission scope.

    This value is an authenticated local observation, not a transferable
    grant. The control-intent owner uses it only while the issuing admission
    keeps the Registry snapshot active and rechecks it immediately before a
    local journal commit.
    """

    service_id: str
    instance_id: str
    port: str
    scope_ref: str
    registration_id: str
    service_epoch: str
    principal_ref: str
    identity: WireIdentity
    scope: WireScope
    operations: frozenset[str]
    write_operations: frozenset[str]


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
        execution_parent_replay: object | None = None,
    ) -> None:
        if issuer is not _DISPATCH_ADMISSION_ISSUER or type(registry) is not ServiceRegistry:
            raise ServiceDispatchAdmissionError("NOT_ISSUED")
        if type(operation) is not str or operation not in ({"direct.reserve", "direct.dispatch", "direct.cancel", _GATEWAY_ACCEPTANCE_OPERATION} | _RISK_V2_ADMISSION_OPERATIONS | _GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS | _EXECUTION_PARENT_OPERATIONS | _EXECUTION_RELEASE_OPERATIONS):
            raise ServiceDispatchAdmissionError("OPERATION_INVALID")
        self._registry = registry
        # Wire canonical bytes make a detached, immutable snapshot of the
        # complete arguments, including nested values and ordering.
        command_value = arguments.get('command')
        parent_operation = (operation if operation in _GATEWAY_PARENT_OPERATIONS else
            'direct.parent.cancel' if operation in {'direct.delegate_actor_cancel', 'direct.delegate_actor_release'}
            and isinstance(command_value, Mapping) and command_value.get('parent_ref') is not None else
            'direct.parent.query' if operation in {'direct.delegate_actor_release_status', 'direct.delegate_actor_release_status_v2'}
            and isinstance(command_value, Mapping) and command_value.get('parent_ref') is not None else None)
        encoded_arguments = encode_wire_value(arguments)
        # V2 dispatch arguments contain a one-time bearer capability; reserve
        # arguments may also contain private order details. Keep only a digest.
        if operation in _RISK_V2_ADMISSION_OPERATIONS or operation == _GATEWAY_RISK_FIRST_RESERVE_V2:
            encoded_arguments = hashlib.sha256(encoded_arguments).digest()
        self._binding = _DispatchAdmissionBinding(
            registration_id, service_epoch, instance_id, principal, identity, scope,
            encoded_arguments, os.getpid(), operation, threading.current_thread(),
            registry.gateway_parent_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope, operation=parent_operation) if parent_operation is not None else None,
            registry.gateway_delegate_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope, operation=operation) if operation in _GATEWAY_ALL_DELEGATE_OPERATIONS else None,
            parent_operation,
            registry.execution_parent_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope, operation=operation) if operation in _EXECUTION_PARENT_OPERATIONS else None,
            registry._execution_parent_generation_for(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope) if operation in _EXECUTION_PARENT_OPERATIONS else None,
            registry.gateway_parent_acceptance_grant(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope) if operation == _GATEWAY_ACCEPTANCE_OPERATION else None,
            registry._gateway_acceptance_generation_for(instance_id=instance_id, scope_ref=registry._db.execute(
                'SELECT scope_ref FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()['scope_ref'],
                identity=identity, scope=scope) if operation == _GATEWAY_ACCEPTANCE_OPERATION else None,
            execution_parent_replay=execution_parent_replay,
        )
        # A replay proof is issued only by the composition-installed prover for
        # this exact issuance and exact arguments. It is not a grant and it
        # cannot be supplied by a request field.
        if execution_parent_replay is not None and (
            type(execution_parent_replay) is not ExecutionParentReplayProof
            or operation != _EXECUTION_PARENT_SUBMIT
            or execution_parent_replay.registration_id != registration_id
            or execution_parent_replay.service_epoch != service_epoch
            or execution_parent_replay.instance_id != instance_id
            or execution_parent_replay.principal != principal
            or execution_parent_replay.identity != identity
            or execution_parent_replay.scope != scope
            or execution_parent_replay.request_bytes != encoded_arguments
            or execution_parent_replay.grant is not self._binding.execution_parent_grant
            or execution_parent_replay.generation != self._binding.execution_parent_generation
        ):
            raise ServiceDispatchAdmissionError("NOT_ISSUED")
        self._state_lock = threading.Lock()
        self._owner_thread: threading.Thread | None = None
        self._creation_used = False
        self._active_marker: object | None = None
        self._active_recheck = None
        self._busy = False
        self._closed = False
        self._execution_context: ServiceCallContext | None = None
        self._execution_upstream_lease: _ExecutionUpstreamLease | None = None

    def matches(self, arguments: Mapping[str, Any]) -> bool:
        """Check complete canonical arguments and current Registry issuance."""
        if os.getpid() != self._binding.pid or not isinstance(arguments, Mapping):
            return False
        try:
            encoded = encode_wire_value(arguments)
            if (self._binding.operation in _RISK_V2_ADMISSION_OPERATIONS
                    or self._binding.operation == _GATEWAY_RISK_FIRST_RESERVE_V2):
                encoded = hashlib.sha256(encoded).digest()
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

    def matches_execution_parent_grant(self, grant: object) -> bool:
        """Match issuance; the live grant is rechecked by scope before commit."""
        if (os.getpid() != self._binding.pid or type(grant) is not ExecutionParentGrant):
            return False
        with self._state_lock:
            return (not self._closed and self._binding.execution_parent_grant is not None
                    and self._binding.execution_parent_grant is grant)

    def execution_parent_grant_for(self, context: ServiceCallContext, operation: str,
                                   arguments: Mapping[str, Any]) -> ExecutionParentGrant | None:
        """Return the exact issuance grant, not proof of current authorization.

        Only the original Registry context and original frozen arguments are
        accepted. The owner must still use ``intake_scope`` before local work.
        """
        if (type(context) is not ServiceCallContext or context is not self._execution_context
                or not context._is_registry_wire_arguments(arguments)
                or not self.matches_context(context) or not self.matches_operation(operation)
                or not self.matches(arguments)):
            return None
        with self._registry._lock:
            with self._state_lock:
                grant = self._binding.execution_parent_grant
                if (self._closed or self._registry._closed
                        or self._registry._dispatch_admissions.get(self) is not self._binding
                        or context is not self._execution_context
                        or type(grant) is not ExecutionParentGrant):
                    return None
                return grant

    def execution_parent_replay_for(self, context: ServiceCallContext, operation: str,
                                    arguments: Mapping[str, Any]) -> ExecutionParentReplayProof | None:
        """Return the exact replay proof issued for this admission, or ``None``.

        The proof authorizes serving one already existing local receipt only.
        It is not a grant, a live lease, or permission to insert a new row; the
        intake rechecks the row and keeps refusing to insert under it.
        """
        if (type(context) is not ServiceCallContext or context is not self._execution_context
                or not context._is_registry_wire_arguments(arguments)
                or not self.matches_context(context) or not self.matches_operation(operation)
                or not self.matches(arguments)):
            return None
        with self._registry._lock, self._state_lock:
            proof = self._binding.execution_parent_replay
            if (self._closed or self._registry._closed
                    or self._registry._dispatch_admissions.get(self) is not self._binding
                    or context is not self._execution_context
                    or operation != _EXECUTION_PARENT_SUBMIT
                    or type(proof) is not ExecutionParentReplayProof):
                return None
            return proof

    def bind_execution_upstream(self, context: ServiceCallContext, arguments: Mapping[str, Any],
                                runtime: object, binding: object) -> None:
        """Bind the actual SDK Gateway registration before local journal work.

        This verifies the SDK's authenticated local registration observation;
        it is not a remote-liveness or in-process isolation guarantee.
        """
        from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime, _RequestNotSentAdapter
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient

        grant = self.execution_parent_grant_for(context, _EXECUTION_PARENT_SUBMIT, arguments)
        if (grant is None or type(runtime) is not ServiceClientRuntime or type(binding) is not RegisteredBinding
                or _INTAKE_GETPID() != self._binding.pid
                or _INTAKE_CURRENT_THREAD() is not self._binding.issuer_thread):
            raise ServiceDispatchAdmissionError('EXECUTION_UPSTREAM_UNAVAILABLE')
        try:
            runtime_vars, binding_vars = vars(runtime), vars(binding)
            scope = binding_vars['scope']
            if type(scope) is not WireScope:
                raise ValueError
            scope_vars = vars(scope)
            bindings = runtime_vars['_bindings']
            peers = runtime_vars['_peers']
            proofs = runtime_vars['_authenticated_registration_peers']
            transports = runtime_vars['_transports']
            if any(type(value) is not dict for value in (runtime_vars, binding_vars, scope_vars, peers, proofs, transports)) or type(bindings) is not list:
                raise ValueError
            indices = [index for index, value in enumerate(bindings) if value is binding]
            deadline = binding_vars['lease_deadline']
            if (len(indices) != 1 or runtime_vars['_state'] != 'registered'
                    or runtime_vars['_owner_thread_id'] != _INTAKE_GET_IDENT()
                    or type(runtime_vars['identity']) is not WireIdentity
                    or runtime_vars['identity'].role is not WireRole.SERVICE
                    or runtime_vars['identity'].delegate_ref is not None
                    or type(runtime_vars['client_session_id']) is not str
                    or type(runtime_vars['run_id']) is not str
                    or runtime_vars['_monotonic_clock'] is not _INTAKE_MONOTONIC
                    or type(_INTAKE_MONOTONIC) is not BuiltinFunctionType
                    or _INTAKE_MONOTONIC.__module__ != 'time' or _INTAKE_MONOTONIC.__name__ != 'monotonic'
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or _INTAKE_MONOTONIC() >= deadline
                    or binding_vars['mount'] != 'upstream.direct_write'
                    or binding_vars['active'] is not True or binding_vars['required'] is not True
                    or binding_vars['remote_lease_inactive'] is not False
                    or type(binding_vars['operations']) is not frozenset
                    or any(type(value) is not str for value in binding_vars['operations'])
                    or any(type(binding_vars[name]) is not str for name in
                           ('mount', 'service_id', 'instance_id', 'scope_ref', 'registration_id', 'service_epoch', 'transport_ref'))
                    or not {'direct.parent.register', 'direct.parent.query'} <= binding_vars['operations']
                    or scope.account_ref != grant.account_ref or scope.strategy_ref != grant.strategy_ref
                    or scope.execution_domain_ref != grant.execution_domain_ref
                    or scope.run_id != context.scope.run_id or scope.run_id is None
                    or scope.account_refs or scope.manual_book_ref is not None
                    or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None):
                raise ValueError
            proof_key = (binding_vars['service_id'], binding_vars['transport_ref'],
                         binding_vars['registration_id'], binding_vars['service_epoch'])
            proof = proofs.get(proof_key)
            observed = ServiceClientRuntime.authenticated_peer_for_binding(runtime, binding)
            clock_lease = ServiceClientRuntime._execution_upstream_lease_for(runtime, binding)
            transport_key = (binding_vars['service_id'], binding_vars['transport_ref'])
            adapter = transports.get(transport_key)
            if (clock_lease is None or type(proof) is not tuple or len(proof) != 2 or observed is not proof
                    or type(proof[0]) is not WireIdentity or proof[0].role is not WireRole.SERVICE
                    or proof[0].delegate_ref is not None or proof[0].actor_ref != binding_vars['service_id']
                    or type(peers.get(binding_vars['service_id'])) is not WireIdentity
                    or peers.get(binding_vars['service_id']) != proof[0]
                    or type(proof[1]) is not str or len(proof[1]) != 64
                    or type(adapter) is not _RequestNotSentAdapter):
                raise ValueError
            adapter_vars = vars(adapter)
            client = adapter_vars['_transport']
            if type(adapter_vars) is not dict or type(client) is not ZmqServiceClient:
                raise ValueError
            client_vars = vars(client)
            server_key = client_vars['_server_key']
            if (type(client_vars) is not dict or type(server_key) is not bytes or len(server_key) != 40
                    or hashlib.sha256(server_key).hexdigest() != proof[1]):
                raise ValueError
            fact_names = ('mount', 'service_id', 'instance_id', 'scope_ref', 'registration_id',
                          'service_epoch', 'operations', 'lease_deadline', 'required', 'active', 'remote_lease_inactive', 'transport_ref')
            scope_names = ('execution_domain_ref', 'account_ref', 'account_refs', 'strategy_ref',
                           'run_id', 'manual_book_ref', 'portfolio_ref', 'group_id', 'leg_id')
            identity_names = ('role', 'principal_ref', 'actor_ref', 'delegate_ref')
            runtime_identity_vars, peer_vars = vars(runtime_vars['identity']), vars(proof[0])
            configured_peer = peers[binding_vars['service_id']]
            configured_peer_vars = vars(configured_peer)
            if (type(runtime_identity_vars) is not dict or type(peer_vars) is not dict
                    or type(configured_peer_vars) is not dict):
                raise ValueError
            token = _ExecutionUpstreamLease(runtime, runtime_vars, bindings, indices[0], binding,
                binding_vars, scope, scope_vars, tuple(binding_vars[n] for n in fact_names),
                tuple(scope_vars[n] for n in scope_names), peers, proof[0], proofs, proof_key, proof,
                transports, transport_key, adapter, adapter_vars, client, client_vars, server_key,
                deadline, runtime_vars['_owner_thread_id'], runtime_vars['identity'],
                runtime_identity_vars, tuple(runtime_identity_vars[n] for n in identity_names),
                peer_vars, tuple(peer_vars[n] for n in identity_names),
                runtime_vars['client_session_id'], runtime_vars['run_id'], clock_lease,
                configured_peer, configured_peer_vars, tuple(configured_peer_vars[n] for n in identity_names))
        except Exception:
            raise ServiceDispatchAdmissionError('EXECUTION_UPSTREAM_UNAVAILABLE') from None
        with self._state_lock:
            if self._closed or self._busy or self._execution_upstream_lease is not None:
                raise ServiceDispatchAdmissionError('EXECUTION_UPSTREAM_UNAVAILABLE')
            self._execution_upstream_lease = token

    def scope(self):
        """Enter on the single owner thread; serial reentry remains permitted."""
        return self._scope(creation=False)

    def observe_gateway_control_intent_registration(
        self,
    ) -> ServiceDispatchRegistrationObservation:
        """Observe the exact current Risk intent registration inside ``scope``.

        The observation is deliberately unavailable for every other operation
        and outside the active owner-thread Registry snapshot. The legacy
        control writes remain binding vocabulary only: this route requires the
        sole registered write operation to be the additive intent operation.
        """

        operation = "direct.control.intent.apply"
        required_operations = frozenset(
            {
                "direct.control.identity",
                "direct.control.apply",
                "direct.control.status",
                "direct.control.cancel_scope",
                "direct.control.cancel_scope_status",
                operation,
            }
        )
        binding, registry = self._binding, self._registry
        with self._state_lock:
            if (
                os.getpid() != binding.pid
                or binding.operation != operation
                or binding.identity.role is not WireRole.APPLICATION_CLIENT
                or binding.identity.delegate_ref is not None
                or self._closed
                or not self._busy
                or self._active_marker is None
                or self._owner_thread is not threading.current_thread()
            ):
                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
            marker = self._active_marker
        if (
            not registry._lock._is_owned()
            or registry._db is None
            or not registry._db.in_transaction
            or registry._dispatch_admissions.get(self) is not binding
        ):
            raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
        try:
            code = registry._direct_dispatch_admission_error(
                binding.registration_id,
                binding.service_epoch,
                binding.instance_id,
                binding.principal,
                binding.identity,
                binding.scope,
                operation,
            )
            if code is not None:
                raise ServiceDispatchAdmissionError(code)
            row = registry._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND principal=? "
                "AND service_epoch=?",
                (binding.registration_id, binding.principal, binding.service_epoch),
            ).fetchone()
            if row is None:
                raise ValueError
            operations = frozenset(json.loads(row["operations_json"]))
            write_operations = frozenset(json.loads(row["write_operations_json"]))
            if (
                row["instance_id"] != binding.instance_id
                or row["port"] != "service.direct_write"
                or registry._stored_identity(row["identity_json"]) != binding.identity
                or registry._stored_scope(row["scope_json"]) != binding.scope
                or not required_operations.issubset(operations)
                or write_operations != frozenset({operation})
                or not registry._current_resource_allows_all(
                    binding.principal,
                    binding.identity,
                    instance_id=binding.instance_id,
                    port=row["port"],
                    scope_ref=row["scope_ref"],
                    scope=binding.scope,
                    operations=operations,
                )
                or any(
                    (binding.instance_id, registered_operation) not in registry._handlers
                    for registered_operation in operations
                )
            ):
                raise ValueError
            observation = ServiceDispatchRegistrationObservation(
                service_id=registry.service_id,
                instance_id=binding.instance_id,
                port=row["port"],
                scope_ref=row["scope_ref"],
                registration_id=binding.registration_id,
                service_epoch=binding.service_epoch,
                principal_ref=binding.principal,
                identity=binding.identity,
                scope=binding.scope,
                operations=operations,
                write_operations=write_operations,
            )
        except ServiceDispatchAdmissionError:
            raise
        except Exception:
            raise ServiceDispatchAdmissionError("SCOPE_DENIED") from None
        with self._state_lock:
            if (
                self._closed
                or not self._busy
                or self._active_marker is not marker
                or self._owner_thread is not threading.current_thread()
            ):
                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
        return observation

    def check_gateway_parent_readiness(self) -> None:
        """Check the fixed parent v2 operation set in this SOURCE admission's snapshot.

        This is a point-in-time read, not an admission for any other operation.
        It is valid only inside the active scope on its account owner thread.
        """
        required = ('direct.parent.source', 'direct.parent.register_v2',
                    'direct.parent.operation_v2', 'direct.parent.cancel_v2')
        binding, registry = self._binding, self._registry
        with self._state_lock:
            if (os.getpid() != binding.pid or binding.operation != 'direct.parent.source'
                    or binding.identity.role is not WireRole.SERVICE or self._closed
                    or not self._busy or self._active_marker is None
                    or self._owner_thread is not threading.current_thread()):
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
            marker = self._active_marker
        if (not registry._lock._is_owned() or registry._db is None or not registry._db.in_transaction
                or registry._dispatch_admissions.get(self) is not binding):
            raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
        row = registry._db.execute('SELECT scope_ref,write_operations_json FROM sessions WHERE registration_id=?',
                                   (binding.registration_id,)).fetchone()
        if row is None:
            raise ServiceDispatchAdmissionError('NOT_REGISTERED')
        capability = registry._capabilities.get((binding.instance_id, 'service.direct_write'))
        read_ops = {'direct.parent.source', 'direct.parent.operation_v2'}
        if (capability is None or read_ops & capability.write_operations
                or read_ops & frozenset(json.loads(row['write_operations_json']))):
            raise ServiceDispatchAdmissionError('CAPABILITY_UNAVAILABLE')
        for operation in required:
            code = registry._direct_dispatch_admission_error(
                binding.registration_id, binding.service_epoch, binding.instance_id,
                binding.principal, binding.identity, binding.scope, operation)
            current = registry.gateway_parent_grant(instance_id=binding.instance_id,
                scope_ref=row['scope_ref'], identity=binding.identity, scope=binding.scope, operation=operation)
            if code is not None or current is None or current != binding.parent_grant:
                raise ServiceDispatchAdmissionError(code or 'SCOPE_DENIED')
        with self._state_lock:
            if (self._closed or not self._busy or self._active_marker is not marker
                    or self._owner_thread is not threading.current_thread()):
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')

    def observe_gateway_parent_strategy_subject(self):
        """Read the current strategy subject in this SOURCE admission snapshot.

        This is an authenticated Registry observation, not a write grant,
        provider fact, global uniqueness proof, or cross-process fence.
        """
        binding, registry = self._binding, self._registry
        with self._state_lock:
            if (os.getpid() != binding.pid or binding.operation != 'direct.parent.source'
                    or binding.identity.role is not WireRole.SERVICE or binding.identity.delegate_ref is not None
                    or self._closed or not self._busy or self._active_marker is None
                    or self._owner_thread is not threading.current_thread()):
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
            marker = self._active_marker
        if (not registry._lock._is_owned() or registry._db is None or not registry._db.in_transaction
                or registry._dispatch_admissions.get(self) is not binding):
            raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
        try:
            code = registry._direct_dispatch_admission_error(
                binding.registration_id, binding.service_epoch, binding.instance_id,
                binding.principal, binding.identity, binding.scope, binding.operation)
            if code is not None:
                raise ValueError
            scope = binding.scope
            claim = registry._db.execute(
                'SELECT active_registration_id FROM strategy_write_claims WHERE execution_domain_ref=? '
                'AND strategy_ref=? AND account_ref=? AND run_id=?',
                (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref, scope.run_id)).fetchone()
            registration_id = None if claim is None else claim['active_registration_id']
            if not _text(registration_id):
                raise ValueError
            row = registry._db.execute(
                'SELECT * FROM sessions WHERE registration_id=? AND service_epoch=? AND expires_at>?',
                (registration_id, binding.service_epoch, registry._now())).fetchone()
            if (row is None or row['instance_id'] != binding.instance_id or row['port'] != 'service.direct_write'
                    or type(row['expires_at']) not in (int, float) or not math.isfinite(row['expires_at'])
                    or row['expires_at'] <= registry._now()
                    or row['policy_version'] != registry.policy_version
                    or row['requirements_version'] != registry.requirements_version
                    or not registry._registration_policy_matches(row)):
                raise ValueError
            identity = registry._stored_identity(row['identity_json'])
            actual_scope = registry._stored_scope(row['scope_json'])
            capability = registry._capabilities.get((binding.instance_id, row['port']))
            if (type(identity) is not WireIdentity or identity.role is not WireRole.STRATEGY_CLIENT
                    or identity.delegate_ref is not None or identity.actor_ref != scope.strategy_ref
                    or identity.principal_ref != row['principal'] or actual_scope != scope
                    or not registry._identity_is_trusted(row['principal'], identity)
                    or not registry._strategy_claim_exists(actual_scope, registration_id)
                    or capability is None):
                raise ValueError
            write_operations = (frozenset(json.loads(row['write_operations_json']))
                                & frozenset(json.loads(row['operations_json']))
                                & capability.write_operations & capability.operations)
            if not any((binding.instance_id, op) in registry._handlers
                       and registry._current_resource_allows(row['principal'], identity,
                           instance_id=binding.instance_id, port=row['port'], scope_ref=row['scope_ref'],
                           scope=actual_scope, operation=op) for op in write_operations):
                raise ValueError
            subject = {'identity': registry._identity_dict(identity), 'scope': registry._scope_dict(actual_scope)}
        except Exception:
            raise ServiceDispatchAdmissionError('SCOPE_DENIED') from None
        with self._state_lock:
            if (self._closed or not self._busy or self._active_marker is not marker
                    or self._owner_thread is not threading.current_thread()):
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
        return subject

    def intake_scope(self):
        getpid, get_ident = _INTAKE_GETPID, _INTAKE_GET_IDENT
        current_thread = _INTAKE_CURRENT_THREAD
        realtime, monotonic = _INTAKE_REALTIME, _INTAKE_MONOTONIC
        return _IntakeScopeContext(
            self._intake_scope(getpid, get_ident, current_thread, realtime, monotonic),
            getpid, get_ident, current_thread)

    def release_action_scope(self, context, arguments, action_binding):
        getpid, get_ident, current_thread = _INTAKE_GETPID, _INTAKE_GET_IDENT, _INTAKE_CURRENT_THREAD
        return _IntakeScopeContext(self._release_action_scope(context, arguments, action_binding),
                                   getpid, get_ident, current_thread)

    @contextmanager
    def _release_action_scope(self, context, arguments, action_binding):
        """Local SERVICE registration and policy intersection, not a send permit.

        Caller must compare the original frozen journal facts independently and
        recheck immediately before a future local commit. No Store, network,
        provider identity, original run or Gateway authorization is proven.
        """
        binding, registry = self._binding, self._registry
        if (binding.operation not in _EXECUTION_RELEASE_OPERATIONS
                or type(context) is not ServiceCallContext or context is not self._execution_context
                or not context._is_registry_wire_arguments(arguments)
                or not self.matches_context(context) or not self.matches(arguments)
                or type(action_binding) is not _ExecutionActionBinding
                or action_binding._registry is not registry):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        getpid, get_ident, current_thread = _INTAKE_GETPID, _INTAKE_GET_IDENT, _INTAKE_CURRENT_THREAD
        realtime, monotonic = _INTAKE_REALTIME, _INTAKE_MONOTONIC
        if getpid() != binding.pid or getpid() != registry._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        thread, ident = current_thread(), get_ident()
        if (action_binding._pid != binding.pid or action_binding._thread is not thread
                or action_binding._ident != ident):
            raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
        with self._state_lock:
            if self._closed or self._busy:
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
            if self._owner_thread is not None and self._owner_thread is not thread:
                raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
            self._owner_thread, self._busy = thread, True
            marker = self._active_marker = object()
        try:
            if registry._clock is not realtime:
                raise ServiceDispatchAdmissionError('INTAKE_CLOCK_UNTRUSTED')
            with registry._execution_intake_snapshot(binding.pid, ident, thread,
                                                       getpid, get_ident, current_thread):
                code = registry._execution_release_admission_error(binding.registration_id,
                    binding.service_epoch, binding.instance_id, binding.principal, binding.identity,
                    binding.scope, binding.operation)
                if code is not None:
                    raise ServiceDispatchAdmissionError(code)
                record = registry._execution_action_bindings.get(action_binding)
                if record is None:
                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                request, grant, generation, key, facts = record[:5]
                if (action_binding._request is not request or action_binding._grant is not grant
                        or action_binding._generation != generation or record[5:] != (binding.pid, thread, ident)
                        or type(request) is not ExecutionActionRequest or request.action != 'release_child'
                        or request.instance_id != binding.instance_id or request.identity != binding.identity
                        or request.account_ref != binding.scope.account_ref
                        or request.strategy_ref != binding.scope.strategy_ref
                        or request.execution_domain_ref != binding.scope.execution_domain_ref
                        or not _release_action_facts_match(request, arguments)
                        or encode_wire_value(arguments) != binding.arguments
                        or _execution_action_facts(request) != facts or _execution_action_key(request) != key
                        or registry._execution_action_grants.get(key) is not grant
                        or registry._execution_action_generations.get(key, 0) != generation
                        or not _execution_action_allows(grant, request)):
                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                           (binding.registration_id,)).fetchone()
                expiry = None if row is None else row['expires_at']
                lease = None if row is None else registry._execution_release_live_lease(row)
                now, mono = realtime(), monotonic()
                if (type(expiry) not in (int, float) or not math.isfinite(expiry)
                        or lease is None or not math.isfinite(now) or not math.isfinite(mono) or expiry <= now):
                    raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                deadline = mono + (expiry - now)
                if not math.isfinite(deadline):
                    raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                frozen_db, frozen_grants = registry._db, registry._grants
                frozen_capabilities = registry._capabilities
                frozen_handler = registry._handlers.get((binding.instance_id, binding.operation))
                frozen_policy = (registry.policy_version, registry.requirements_version,
                                 registry.access_policy_ref, registry.requirements_ref)
                registry._execution_intake_owner = thread
                registry._execution_intake_owner_ident = ident
                registry._execution_intake_get_ident = get_ident
                active = True

                def check():
                    if getpid() != binding.pid:
                        raise ServiceDispatchAdmissionError('WRONG_PROCESS')
                    if get_ident() != ident or current_thread() is not thread:
                        raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
                    with self._state_lock:
                        if (not active or self._closed or not self._busy or self._active_marker is not marker
                                or registry._execution_intake_owner is not thread
                                or registry._execution_intake_owner_ident != ident or registry._closed
                                or registry._db is not frozen_db or not registry._ready
                                or registry._epoch != binding.service_epoch or registry._clock is not realtime
                                or registry._grants is not frozen_grants or registry._capabilities is not frozen_capabilities
                                or registry._handlers.get((binding.instance_id, binding.operation)) is not frozen_handler
                                or registry._dispatch_admissions.get(self) is not binding
                                or registry._execution_action_bindings.get(action_binding) is not record
                                or registry._execution_release_monotonic_leases.get(binding.registration_id) is not lease
                                or registry._execution_action_grants.get(key) is not grant
                                or registry._execution_action_generations.get(key, 0) != generation
                                or (registry.policy_version, registry.requirements_version,
                                    registry.access_policy_ref, registry.requirements_ref) != frozen_policy
                                or _execution_action_facts(request) != facts
                                or not _execution_action_allows(grant, request)
                                or not _release_action_facts_match(request, arguments)
                                or encode_wire_value(arguments) != binding.arguments):
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                    if (realtime() >= expiry or monotonic() >= deadline
                            or registry._execution_release_mono_now() >= lease[3]):
                        raise ServiceDispatchAdmissionError('NOT_REGISTERED')

                try:
                    check()
                    yield check
                    check()
                finally:
                    active = False
                    if getpid() == binding.pid and current_thread() is thread:
                        registry._execution_intake_owner = None
                        registry._execution_intake_owner_ident = None
                        registry._execution_intake_get_ident = None
        except _StaleOwnerError:
            raise ServiceDispatchAdmissionError('STALE_EPOCH') from None
        except sqlite3.Error:
            raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
        finally:
            if getpid() == binding.pid and current_thread() is thread:
                with self._state_lock:
                    self._busy = False
                    self._active_marker = None

    def intake_action_scope(self, context, arguments, action_binding):
        """Intersect a real strategy admission with a local service action policy.

        The yielded check requires the original submit arguments; for query it
        requires the original submit arguments read from the selected local
        journal row. The caller must independently bind that row to the query
        before using it. The scope covers only local work and must not contain
        external calls. The service identity in the action policy is a local
        composition input, not evidence of authenticated service registration.
        """
        if (type(action_binding) is not _ExecutionActionBinding
                or self.execution_parent_grant_for(context, self._binding.operation, arguments) is None):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        if self._binding.operation not in _EXECUTION_PARENT_OPERATIONS:
            raise ServiceDispatchAdmissionError('OPERATION_MISMATCH')
        if type(arguments) not in (dict, MappingProxyType):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        if self._binding.operation == _EXECUTION_PARENT_QUERY:
            if (set(arguments) != {'request_id'} or type(arguments['request_id']) is not str
                    or not arguments['request_id']):
                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
            query_id = arguments['request_id']
        else:
            query_id = None
        getpid, get_ident = _INTAKE_GETPID, _INTAKE_GET_IDENT
        current_thread = _INTAKE_CURRENT_THREAD
        return _IntakeScopeContext(
            self._intake_scope(getpid, get_ident, current_thread, _INTAKE_REALTIME,
                               _INTAKE_MONOTONIC, action_binding=action_binding,
                               action_arguments=arguments, query_id=query_id),
            getpid, get_ident, current_thread)

    @contextmanager
    def _intake_scope(self, getpid, get_ident, current_thread, realtime, monotonic,
                      *, action_binding=None, action_arguments=None, query_id=None) -> Iterator[Callable[[], None]]:
        """Execution parent local-journal fence; never use around remote I/O.

        Preflight and the MIG03 attempt run before the caller takes its journal
        lock. The yielded check has no Registry SQL, marker I/O or operator
        callback. The Registry read snapshot and lock remain held, so only
        cooperating same-Registry changes are fenced. Out-of-band WAL writers
        and the MIG03 offline-quiescence requirement are not proven here.
        """
        # These functions were captured before entering the scope; the final
        # check and cleanup never resolve mutable process-global names.
        if self._binding.operation not in _EXECUTION_PARENT_OPERATIONS:
            raise ServiceDispatchAdmissionError('OPERATION_MISMATCH')
        # A replay proof is bound to this exact issuance and these exact
        # canonical arguments; it is carried only by the Registry-issued
        # admission, never by request data.
        replay_proof = self._binding.execution_parent_replay
        if (replay_proof is not None and (
                type(replay_proof) is not ExecutionParentReplayProof
                or self._binding.operation != _EXECUTION_PARENT_SUBMIT
                or replay_proof.request_bytes != self._binding.arguments
                or replay_proof.grant is not self._binding.execution_parent_grant
                or replay_proof.generation != self._binding.execution_parent_generation)):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        if (type(getpid) is not BuiltinFunctionType or getpid.__name__ != 'getpid'
                or getpid.__module__ not in {'posix', 'nt'}
                or type(get_ident) is not BuiltinFunctionType
                or get_ident.__name__ != 'get_ident' or get_ident.__module__ != '_thread'):
            raise ServiceDispatchAdmissionError('INTAKE_RUNTIME_UNTRUSTED')
        if getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        thread = current_thread()
        thread_ident = get_ident()
        with self._state_lock:
            if self._closed:
                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
            if self._busy:
                raise ServiceDispatchAdmissionError('ADMISSION_BUSY')
            if self._owner_thread is not None and self._owner_thread is not thread:
                raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
            self._owner_thread = thread
            self._busy = True
            marker = object()
            self._active_marker = marker
            upstream_lease = self._execution_upstream_lease
        registry = self._registry
        authorized = False
        try:
            if upstream_lease is not None:
                from bt_api_py.service_runtime import _ExecutionSuspendClock, _ExecutionUpstreamClockLease
                from bt_api_transport_zmq.service_v2 import _ServiceSocketBinding, _SocketGeneration
            if (type(realtime) is not BuiltinFunctionType
                    or realtime.__module__ != 'time' or realtime.__name__ != 'time'
                    or type(monotonic) is not BuiltinFunctionType
                    or monotonic.__module__ != 'time' or monotonic.__name__ != 'monotonic'
                    or registry._clock is not realtime):
                raise ServiceDispatchAdmissionError('INTAKE_CLOCK_UNTRUSTED')
            try:
                with registry._execution_intake_snapshot(self._binding.pid, thread_ident, thread,
                                                          getpid, get_ident, current_thread):
                    action_record = None
                    if action_binding is not None:
                        if type(action_binding) is not _ExecutionActionBinding:
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        action_record = registry._execution_action_bindings.get(action_binding)

                    def check_action():
                        if action_binding is None:
                            return
                        record = action_record
                        if (record is None or registry._execution_action_bindings.get(action_binding) is not record
                                or action_binding._registry is not registry
                                or action_binding._request is not record[0]
                                or action_binding._grant is not record[1]
                                or action_binding._generation != record[2]
                                or action_binding._pid != record[5] or record[5] != self._binding.pid
                                or record[5] != registry._execution_action_pid
                                or action_binding._thread is not record[6] or record[6] is not thread
                                or action_binding._ident != record[7] or record[7] != thread_ident):
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        request, grant, generation, key, facts = record[:5]
                        scope = self._binding.scope
                        try:
                            allowed = (type(request) is ExecutionActionRequest
                                and type(request.instrument) is MarketInstrumentKey
                                and _execution_action_facts(request) == facts
                                and _execution_action_key(request) == key
                                and registry._execution_action_grants.get(key) is grant
                                and registry._execution_action_generations.get(key, 0) == generation
                                and _execution_action_allows(grant, request)
                                and type(self._binding.execution_parent_grant) is ExecutionParentGrant
                                and request.instrument in self._binding.execution_parent_grant.instruments
                                and request.instance_id == self._binding.instance_id
                                and request.account_ref == scope.account_ref
                                and request.strategy_ref == scope.strategy_ref
                                and request.execution_domain_ref == scope.execution_domain_ref
                                and request.action == ('accept' if self._binding.operation == _EXECUTION_PARENT_SUBMIT else 'query'))
                        except (AttributeError, TypeError, ValueError):
                            allowed = False
                        if not allowed:
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')

                    def preflight():
                        if registry._clock is not realtime:
                            raise ServiceDispatchAdmissionError('INTAKE_CLOCK_UNTRUSTED')
                        if registry._dispatch_admissions.get(self) is not self._binding:
                            raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
                        code = registry._execution_parent_admission_error(
                            self._binding.registration_id, self._binding.service_epoch,
                            self._binding.instance_id, self._binding.principal,
                            self._binding.identity, self._binding.scope, self._binding.operation,
                            replay_proof=replay_proof)
                        if code is not None:
                            raise ServiceDispatchAdmissionError(code)
                        row = registry._db.execute(
                            'SELECT scope_ref,expires_at FROM sessions WHERE registration_id=?',
                            (self._binding.registration_id,)).fetchone()
                        grant = None if row is None else registry.execution_parent_grant(
                            instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                            identity=self._binding.identity, scope=self._binding.scope,
                            operation=self._binding.operation)
                        if (grant is None or grant is not self._binding.execution_parent_grant
                                or registry._execution_parent_generation_for(
                                    instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                    identity=self._binding.identity, scope=self._binding.scope)
                                != self._binding.execution_parent_generation):
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        check_action()
                        if (action_binding is not None and self._binding.operation == _EXECUTION_PARENT_SUBMIT
                                and not _action_parent_facts_match(action_record[0], action_arguments)):
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        return row

                    preflight()
                    if self._binding.operation == _EXECUTION_PARENT_SUBMIT:
                        try:
                            registry._record_migration_write_attempt(fact_kind='dispatch')
                        except Exception:
                            raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
                    row = preflight()
                    parent_lease = None
                    parent_read_lease = None
                    if self._binding.operation == _EXECUTION_PARENT_SUBMIT:
                        full_row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                                        (self._binding.registration_id,)).fetchone()
                        parent_lease = None if full_row is None else registry._execution_parent_live_lease(full_row)
                        if parent_lease is None:
                            if replay_proof is None:
                                raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                            # A proven replay keeps the original registration
                            # deadline; only the superseded grant binding is
                            # replaced by the proof rechecked in preflight.
                            parent_lease = registry._execution_parent_replay_lease(full_row)
                            if parent_lease is None:
                                raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                    elif (self._binding.operation == _EXECUTION_PARENT_QUERY
                          and self._binding.instance_id in registry._execution_parent_read_instances):
                        full_row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                                        (self._binding.registration_id,)).fetchone()
                        parent_read_lease = None if full_row is None else registry._execution_parent_live_read_lease(full_row)
                        if parent_read_lease is None:
                            raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                    if registry._execution_intake_owner is not None:
                        raise ServiceDispatchAdmissionError('ADMISSION_BUSY')
                    expiry = row['expires_at']
                    mono_start = monotonic()
                    wall_start = realtime()
                    if (type(expiry) not in (int, float) or not math.isfinite(expiry)
                            or not math.isfinite(mono_start) or not math.isfinite(wall_start)
                            or expiry <= wall_start):
                        raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                    mono_deadline = mono_start + (expiry - wall_start)
                    if not math.isfinite(mono_deadline):
                        raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                    grant_key = (self._binding.instance_id, row['scope_ref'],
                                 self._binding.identity.principal_ref,
                                 self._binding.scope.account_ref, self._binding.scope.strategy_ref)
                    frozen_grants, frozen_capabilities = registry._grants, registry._capabilities
                    frozen_db = registry._db
                    frozen_policy = (registry.policy_version, registry.requirements_version,
                                     registry.access_policy_ref, registry.requirements_ref,
                                     registry._account_risk_policy_bindings,
                                     registry._account_risk_policy_required_routes)
                    handler_key = (self._binding.instance_id, self._binding.operation)
                    frozen_handler = registry._handlers.get(handler_key)
                    binding_fact_names = ('mount', 'service_id', 'instance_id', 'scope_ref', 'registration_id',
                        'service_epoch', 'operations', 'lease_deadline', 'required', 'active',
                        'remote_lease_inactive', 'transport_ref')
                    scope_fact_names = ('execution_domain_ref', 'account_ref', 'account_refs', 'strategy_ref',
                        'run_id', 'manual_book_ref', 'portfolio_ref', 'group_id', 'leg_id')
                    identity_fact_names = ('role', 'principal_ref', 'actor_ref', 'delegate_ref')
                    registry._execution_intake_owner = thread
                    registry._execution_intake_owner_ident = thread_ident
                    registry._execution_intake_get_ident = get_ident

                    def check() -> None:
                        if getpid() != self._binding.pid:
                            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
                        with self._state_lock:
                            if (self._closed or not self._busy or self._active_marker is not marker
                                    or get_ident() != thread_ident):
                                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
                            if (registry._execution_intake_owner is not thread
                                    or registry._execution_intake_owner_ident != thread_ident
                                    or registry._execution_intake_get_ident is not get_ident
                                    or registry._closed or registry._db is not frozen_db
                                    or not registry._ready or registry._epoch != self._binding.service_epoch
                                    or registry._clock is not realtime
                                    or registry._grants is not frozen_grants
                                    or registry._capabilities is not frozen_capabilities
                                    or registry._handlers.get(handler_key) is not frozen_handler
                                    or registry._dispatch_admissions.get(self) is not self._binding
                                    or registry._execution_parent_grants.get(grant_key) is not self._binding.execution_parent_grant
                                    or registry._execution_parent_generations.get(grant_key, 0) != self._binding.execution_parent_generation
                                    or (registry.policy_version, registry.requirements_version,
                                        registry.access_policy_ref, registry.requirements_ref,
                                        registry._account_risk_policy_bindings,
                                        registry._account_risk_policy_required_routes) != frozen_policy):
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                            if realtime() >= expiry or monotonic() >= mono_deadline:
                                raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                            if (parent_lease is not None and
                                    (registry._execution_parent_monotonic_leases.get(self._binding.registration_id)
                                     is not parent_lease or registry._execution_parent_mono_now() >= parent_lease[5])):
                                raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                            if (parent_read_lease is not None and
                                    (registry._execution_parent_read_leases.get(self._binding.registration_id)
                                     is not parent_read_lease or registry._execution_parent_mono_now() >= parent_read_lease[5])):
                                raise ServiceDispatchAdmissionError('NOT_REGISTERED')
                            if parent_read_lease is not None:
                                registry._execution_parent_read_wall_now()
                            check_action()
                            if upstream_lease is not None:
                                token = upstream_lease
                                clock_lease = token.clock_lease
                                rv, bv, sv = token.runtime_vars, token.binding_vars, token.scope_vars
                                current_binding = tuple(bv.get(name) for name in binding_fact_names)
                                current_scope = tuple(sv.get(name) for name in scope_fact_names)
                                current_runtime_identity = tuple(token.runtime_identity_vars.get(name) for name in identity_fact_names)
                                current_peer = tuple(token.peer_vars.get(name) for name in identity_fact_names)
                                current_configured_peer = tuple(token.configured_peer_vars.get(name) for name in identity_fact_names)
                                if (type(clock_lease) is not _ExecutionUpstreamClockLease
                                        or clock_lease.runtime is not token.runtime
                                        or clock_lease.binding is not token.binding
                                        or clock_lease.identity is not token.runtime_identity
                                        or clock_lease.pid != getpid() or clock_lease.thread != get_ident()
                                        or type(clock_lease.clock) is not _ExecutionSuspendClock
                                        or rv.get('_execution_upstream_clock') is not clock_lease.clock
                                        or type(rv.get('_execution_upstream_clock_leases')) is not dict
                                        or rv['_execution_upstream_clock_leases'].get(token.proof_key) is not clock_lease
                                        or type(clock_lease.proof) is not tuple or len(clock_lease.proof) != 3
                                        or type(clock_lease.proof[2]) is not _ServiceSocketBinding
                                        or clock_lease.proof[0] is not token.adapter
                                        or clock_lease.proof[1] is not token.client
                                        or type(rv.get('_strict_registration_transports')) is not dict
                                        or rv['_strict_registration_transports'].get(token.proof_key) is not clock_lease.proof
                                        or type(rv.get('_authenticated_registration_transports')) is not dict
                                        or rv['_authenticated_registration_transports'].get(token.proof_key) is not clock_lease.proof
                                        or type(clock_lease.generation) is not _SocketGeneration
                                        or clock_lease.proof[2]._generation is not clock_lease.generation
                                        or clock_lease.generation._revoked is not False
                                        or token.client_vars.get('_socket_binding') is not clock_lease.proof[2]
                                        or token.client_vars.get('_socket') is not clock_lease.proof[2].socket
                                        or token.client_vars.get('_receiver') is not clock_lease.proof[2].receiver
                                        or token.client_vars.get('_closed') is not False
                                        or token.client_vars.get('_close_pending') is not False
                                        or token.client_vars.get('_socket_retirement_pending') is not False
                                        or self._execution_upstream_lease is not token
                                        or object.__getattribute__(token.runtime, '__dict__') is not rv
                                        or object.__getattribute__(token.binding, '__dict__') is not bv
                                        or object.__getattribute__(token.scope, '__dict__') is not sv
                                        or object.__getattribute__(token.adapter, '__dict__') is not token.adapter_vars
                                        or object.__getattribute__(token.client, '__dict__') is not token.client_vars
                                        or object.__getattribute__(token.runtime_identity, '__dict__') is not token.runtime_identity_vars
                                        or object.__getattribute__(token.peer, '__dict__') is not token.peer_vars
                                        or object.__getattribute__(token.configured_peer, '__dict__') is not token.configured_peer_vars
                                        or rv.get('_bindings') is not token.bindings
                                        or token.index >= len(token.bindings)
                                        or token.bindings[token.index] is not token.binding
                                        or rv.get('_authenticated_registration_peers') is not token.proofs
                                        or token.proofs.get(token.proof_key) is not token.proof
                                        or rv.get('_peers') is not token.peers
                                        or token.peers.get(token.proof_key[0]) is not token.configured_peer
                                        or any(type(value) is not type(expected) for value, expected in zip(current_configured_peer, token.configured_peer_facts))
                                        or current_configured_peer[0] is not token.configured_peer_facts[0]
                                        or current_configured_peer[1:] != token.configured_peer_facts[1:]
                                        or rv.get('_transports') is not token.transports
                                        or token.transports.get(token.transport_key) is not token.adapter
                                        or token.adapter_vars.get('_transport') is not token.client
                                        or type(token.client_vars.get('_server_key')) is not bytes
                                        or token.client_vars.get('_server_key') != token.server_key
                                        or type(rv.get('_state')) is not str or rv.get('_state') != 'registered'
                                        or type(rv.get('_owner_thread_id')) is not int
                                        or rv.get('_owner_thread_id') != token.owner_ident
                                        or rv.get('_monotonic_clock') is not monotonic
                                        or rv.get('identity') is not token.runtime_identity
                                        or type(rv.get('client_session_id')) is not str
                                        or rv.get('client_session_id') != token.runtime_session_id
                                        or type(rv.get('run_id')) is not str or rv.get('run_id') != token.runtime_run_id
                                        or bv.get('scope') is not token.scope
                                        or any(type(value) is not type(expected) for value, expected in zip(current_binding, token.binding_facts))
                                        or current_binding[6] is not token.binding_facts[6]
                                        or current_binding[:6] != token.binding_facts[:6]
                                        or current_binding[7:] != token.binding_facts[7:]
                                        or any(type(value) is not type(expected) for value, expected in zip(current_scope, token.scope_facts))
                                        or type(current_scope[2]) is not tuple or len(current_scope[2]) != 0
                                        or current_scope != token.scope_facts
                                        or any(type(value) is not type(expected) for value, expected in zip(current_runtime_identity, token.runtime_identity_facts))
                                        or current_runtime_identity[0] is not token.runtime_identity_facts[0]
                                        or current_runtime_identity[1:] != token.runtime_identity_facts[1:]
                                        or any(type(value) is not type(expected) for value, expected in zip(current_peer, token.peer_facts))
                                        or current_peer[0] is not token.peer_facts[0]
                                        or current_peer[1:] != token.peer_facts[1:]
                                        or monotonic() >= token.deadline):
                                    raise ServiceDispatchAdmissionError('EXECUTION_UPSTREAM_UNAVAILABLE')
                                try:
                                    if _ExecutionSuspendClock.now(clock_lease.clock) >= clock_lease.deadline:
                                        raise ValueError
                                except (ValueError, TypeError, AttributeError):
                                    raise ServiceDispatchAdmissionError('EXECUTION_UPSTREAM_UNAVAILABLE') from None

                    try:
                        check()
                        authorized = True
                        if action_binding is None:
                            yield check
                        else:
                            def check_both(parent_arguments):
                                check()
                                if (self._binding.operation == _EXECUTION_PARENT_SUBMIT
                                        and parent_arguments is not action_arguments):
                                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                                if (self._binding.operation == _EXECUTION_PARENT_QUERY
                                        and (type(action_arguments) not in (dict, MappingProxyType)
                                             or action_arguments.get('request_id') != query_id
                                             or type(parent_arguments) not in (dict, MappingProxyType)
                                             or parent_arguments.get('request_id') != query_id)):
                                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                                if not _action_parent_facts_match(action_record[0], parent_arguments):
                                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                            yield check_both
                    finally:
                        if getpid() == self._binding.pid and current_thread() is thread:
                            registry._execution_intake_owner = None
                            registry._execution_intake_owner_ident = None
                            registry._execution_intake_get_ident = None
            except _StaleOwnerError:
                if authorized:
                    raise
                raise ServiceDispatchAdmissionError('STALE_EPOCH') from None
            except sqlite3.Error:
                if authorized:
                    raise
                raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
        finally:
            if getpid() == self._binding.pid and current_thread() is thread:
                with self._state_lock:
                    self._busy = False
                    self._active_marker = None

    def creation_scope(self):
        """Use cancel once on the issuer thread before transferring to the owner."""
        return self._scope(creation=True)

    def matches_scope_recheck(self, recheck: object) -> bool:
        """Check that a recheck is the one issued for this live scope."""
        if os.getpid() != self._binding.pid:
            return False
        with self._state_lock:
            active = self._active_recheck
            return (
                not self._closed
                and self._busy
                and active is not None
                and active[0] is self._active_marker
                and active[1] is recheck
                and active[2] is threading.current_thread()
            )

    @contextmanager
    def _scope(self, *, creation: bool) -> Iterator[Callable[[], None]]:
        if self._binding.operation in _EXECUTION_RELEASE_OPERATIONS:
            raise ServiceDispatchAdmissionError('OPERATION_MISMATCH')
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
                            checker = (self._registry._execution_parent_admission_error
                                       if self._binding.operation in _EXECUTION_PARENT_OPERATIONS
                                       else self._registry._direct_dispatch_admission_error)
                            code = checker(
                                self._binding.registration_id, self._binding.service_epoch,
                                self._binding.instance_id, self._binding.principal,
                                self._binding.identity, self._binding.scope,
                                self._binding.operation,
                            )
                        except Exception:
                            raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        if code is not None:
                            raise ServiceDispatchAdmissionError(code)
                        if self._binding.operation in _EXECUTION_PARENT_OPERATIONS:
                            row = self._registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                              (self._binding.registration_id,)).fetchone()
                            grant = None if row is None else self._registry.execution_parent_grant(
                                instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                identity=self._binding.identity, scope=self._binding.scope,
                                operation=self._binding.operation)
                            if (grant is not self._binding.execution_parent_grant or grant is None
                                    or self._registry._execution_parent_generation_for(
                                        instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                        identity=self._binding.identity, scope=self._binding.scope)
                                    != self._binding.execution_parent_generation):
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        if self._binding.operation == _GATEWAY_ACCEPTANCE_OPERATION:
                            row = self._registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                            (self._binding.registration_id,)).fetchone()
                            grant = None if row is None else self._registry.gateway_parent_acceptance_grant(
                                instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                identity=self._binding.identity, scope=self._binding.scope)
                            if (grant is None or grant is not self._binding.acceptance_grant
                                    or self._registry._gateway_acceptance_generation_for(
                                        instance_id=self._binding.instance_id, scope_ref=row['scope_ref'],
                                        identity=self._binding.identity, scope=self._binding.scope)
                                    != self._binding.acceptance_generation):
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
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
                        if self._binding.operation not in ({"account.events.progress", "account.facts.progress", "account.facts.status", "account.facts.snapshot_info", "account.cancel_attempts.progress", _EXECUTION_PARENT_QUERY, _GATEWAY_ACCEPTANCE_OPERATION} | _GATEWAY_ORDER_CUT_OPERATIONS | _GATEWAY_CONTROL_PURE_READ_OPERATIONS | _GATEWAY_PARENT_READ_OPERATIONS | (_GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS - {'direct.delegate_events.read_after', 'direct.delegate_events.ack'}) | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS):
                            try:
                                fact_kind = ("cursor" if self._binding.operation in (_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_DELEGATE_STREAM_OPERATIONS)
                                             else "dispatch")
                                self._registry._record_migration_write_attempt(fact_kind=fact_kind)
                            except Exception:
                                raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        validate_current()

                    with self._state_lock:
                        self._active_recheck = (marker, recheck, thread)
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
                self._active_recheck = None

    def close(self) -> None:
        if os.getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
        with self._state_lock:
            self._closed = True
            self._execution_context = None

    invalidate = close


_RISK_ADMIN_CANCEL_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _RiskAdminCancelBinding:
    registration_id: str
    registration_key: str
    registration_digest: str
    scope_ref: str
    service_epoch: str
    instance_id: str
    principal: str
    identity: WireIdentity
    scope: WireScope
    arguments_sha256: bytes = field(repr=False)
    pid: int
    issuer_thread: threading.Thread


class RiskAdminCancelAdmission:
    """Registry-local, single-use admission for one synchronous admin request.

    This revalidates the authenticated registration and operation at a local
    transaction boundary. It binds the complete request but does not grant the
    requested account, strategy or instrument cancellation: Risk must check
    its own current operator grant separately. The scoped section may contain
    only local work, never network or provider I/O. Entry, recheck and close
    remain on the issuing handler thread; authority does not extend past the
    final recheck or the Registry handler's return.

    The Registry cannot make an existing handler consume this admission. The
    opt-in must remain disabled until a trusted Risk handler explicitly binds
    the original context and arguments, rechecks the Risk scope grant, and
    invokes the yielded recheck immediately before its local commit. It does
    not establish authority against arbitrary code in the same process.
    """

    def __init__(self, issuer: object, registry: ServiceRegistry, *, binding: _RiskAdminCancelBinding,
                 arguments: Mapping[str, Any]) -> None:
        if (issuer is not _RISK_ADMIN_CANCEL_ISSUER or type(registry) is not ServiceRegistry
                or type(binding) is not _RiskAdminCancelBinding):
            raise ServiceDispatchAdmissionError("NOT_ISSUED")
        self._registry = registry
        self._binding = binding
        self._arguments = arguments
        self._context: ServiceCallContext | None = None
        self._state_lock = threading.Lock()
        self._used = False
        self._closed = False
        self._active_marker: object | None = None
        self._active_recheck = None

    def _bind_context(self, context: ServiceCallContext) -> None:
        if self._context is not None or type(context) is not ServiceCallContext:
            raise ServiceDispatchAdmissionError("NOT_ISSUED")
        self._context = context

    def matches(self, arguments: Mapping[str, Any]) -> bool:
        if os.getpid() != self._binding.pid or not isinstance(arguments, Mapping):
            return False
        try:
            digest = hashlib.sha256(encode_wire_value(arguments)).digest()
        except Exception:
            return False
        with self._state_lock:
            if self._closed or digest != self._binding.arguments_sha256:
                return False
        with self._registry._lock:
            return (not self._closed and not self._registry._closed
                    and self._registry._risk_admin_cancel_admissions.get(self) is self._binding)

    def matches_context(self, context: ServiceCallContext) -> bool:
        if (os.getpid() != self._binding.pid or context is not self._context
                or type(context) is not ServiceCallContext or context.dispatch_admission is not self
                or context.identity != self._binding.identity or context.scope != self._binding.scope
                or context.registration_id != self._binding.registration_id
                or context.principal_ref != self._binding.principal):
            return False
        with self._state_lock:
            if self._closed:
                return False
        with self._registry._lock:
            return (not self._closed and not self._registry._closed
                    and self._registry._risk_admin_cancel_admissions.get(self) is self._binding)

    def matches_operation(self, operation: str) -> bool:
        return (type(operation) is str and operation == _RISK_ADMIN_CANCEL
                and self._context is not None and self.matches_context(self._context))

    def matches_scope_recheck(self, recheck: object) -> bool:
        if os.getpid() != self._binding.pid:
            return False
        with self._state_lock:
            current = self._active_recheck
            active = (not self._closed and current is not None
                      and current[0] is self._active_marker and current[1] is recheck
                      and current[2] is threading.current_thread())
        if not active:
            return False
        with self._registry._lock:
            return (not self._closed and not self._registry._closed
                    and self._registry._risk_admin_cancel_admissions.get(self) is self._binding)

    @contextmanager
    def scope(self) -> Iterator[Callable[[], None]]:
        if os.getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
        thread = threading.current_thread()
        if thread is not self._binding.issuer_thread:
            raise ServiceDispatchAdmissionError("WRONG_ISSUER_THREAD")
        with self._state_lock:
            if self._closed:
                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
            if self._used:
                raise ServiceDispatchAdmissionError("ADMISSION_CONSUMED")
            self._used = True
            marker = object()
            self._active_marker = marker
        try:
            authorized = False
            try:
                with self._registry._read_snapshot():
                    def validate_current() -> None:
                        if os.getpid() != self._binding.pid:
                            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
                        if threading.current_thread() is not self._binding.issuer_thread:
                            raise ServiceDispatchAdmissionError("WRONG_ISSUER_THREAD")
                        with self._state_lock:
                            if (self._closed or self._active_marker is not marker
                                    or threading.current_thread() is not thread):
                                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
                        if self._registry._risk_admin_cancel_admissions.get(self) is not self._binding:
                            raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")
                        try:
                            code = self._registry._risk_admin_cancel_admission_error(self._binding)
                            digest = hashlib.sha256(encode_wire_value(self._arguments)).digest()
                        except Exception:
                            raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        if code is not None:
                            raise ServiceDispatchAdmissionError(code)
                        if digest != self._binding.arguments_sha256:
                            raise ServiceDispatchAdmissionError("ARGUMENTS_CHANGED")
                        with self._state_lock:
                            if (self._closed or self._active_marker is not marker
                                    or threading.current_thread() is not thread):
                                raise ServiceDispatchAdmissionError("ADMISSION_CLOSED")

                    def recheck() -> None:
                        validate_current()
                        try:
                            self._registry._record_migration_write_attempt(fact_kind="dispatch")
                        except Exception:
                            raise ServiceDispatchAdmissionError("UNAVAILABLE") from None
                        validate_current()

                    with self._state_lock:
                        self._active_recheck = (marker, recheck, thread)
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
                self._active_marker = None
                self._active_recheck = None

    def close(self) -> None:
        if os.getpid() != self._binding.pid:
            raise ServiceDispatchAdmissionError("WRONG_PROCESS")
        if threading.current_thread() is not self._binding.issuer_thread:
            raise ServiceDispatchAdmissionError("WRONG_ISSUER_THREAD")
        with self._state_lock:
            self._closed = True
            if self._active_marker is not None:
                # A handler must finish its local scope before returning.
                # Closing invalidates recheck even if the handler leaked the
                # context manager; the Registry must not report success.
                raise ServiceDispatchAdmissionError("ADMISSION_SCOPE_ACTIVE")


@dataclass(frozen=True, slots=True)
class GatewayNettingServicePolicy:
    """Trusted account/service configuration, not strategy consent or a trade grant."""

    instance_id: str
    identity: WireIdentity
    execution_domain_ref: str
    account_ref: str
    instruments: frozenset[MarketInstrumentKey]

    def __post_init__(self):
        if (not _text(self.instance_id) or type(self.identity) is not WireIdentity
                or self.identity.role is not WireRole.SERVICE or self.identity.delegate_ref is not None
                or not _text(self.execution_domain_ref) or not _text(self.account_ref)
                or type(self.instruments) is not frozenset or not self.instruments
                or any(type(item) is not MarketInstrumentKey for item in self.instruments)):
            raise ValueError('invalid gateway netting service policy')


_GATEWAY_PAIR_ISSUER = object()


class _GatewayParentPairAdmission:
    """A local Registry scope check, never a transfer or journal authorization.

    The original parent query requests bind only claimed parent identities. This
    object does not prove either parent exists or belongs to either run, or that
    quantity, price, trading session, source, or plan occupancy is available.
    It is not wired to Gateway or any service endpoint.
    """

    def __init__(self, issuer, registry, admissions, arguments, instrument, policy, generation):
        if issuer is not _GATEWAY_PAIR_ISSUER:
            raise ServiceDispatchAdmissionError('NOT_ISSUED')
        self._registry, self._admissions, self._arguments = registry, admissions, arguments
        self._instrument, self._policy, self._generation = instrument, policy, generation
        self._pid, self._thread = _INTAKE_GETPID(), _INTAKE_CURRENT_THREAD()
        self._ident = _INTAKE_GET_IDENT()

    @contextmanager
    def scope(self):
        """Hold Registry snapshot; caller must recheck before any local commit.

        Never use around remote I/O. Registry precedes any local journal lock.
        Same-Registry policy, session and ACL changes serialize here; external
        writers and historical parent-run binding are not established.
        """
        registry = self._registry
        if _INTAKE_GETPID() != self._pid or self._pid != registry._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        if _INTAKE_CURRENT_THREAD() is not self._thread or _INTAKE_GET_IDENT() != self._ident:
            raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
        acquired = []
        marker = object()
        try:
            for admission in sorted(self._admissions, key=id):
                with admission._state_lock:
                    if admission._closed or admission._busy or (admission._owner_thread is not None
                            and admission._owner_thread is not self._thread):
                        raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
                    admission._busy = True
                    admission._owner_thread = self._thread
                    admission._active_marker = marker
                    acquired.append(admission)
            with registry._read_snapshot():
                active = True

                def check():
                    if _INTAKE_GETPID() != self._pid:
                        raise ServiceDispatchAdmissionError('WRONG_PROCESS')
                    if _INTAKE_CURRENT_THREAD() is not self._thread or _INTAKE_GET_IDENT() != self._ident:
                        raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
                    key = (self._policy.instance_id, self._policy.identity, self._policy.execution_domain_ref,
                           self._policy.account_ref)
                    if (not active or registry._closed or registry._gateway_netting_pairs.get(self) is not self._policy
                            or registry._gateway_netting_policies.get(key) is not self._policy
                            or registry._gateway_netting_generations.get(key, 0) != self._generation
                            or self._instrument not in self._policy.instruments):
                        raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                    for admission, arguments in zip(self._admissions, self._arguments):
                        binding = admission._binding
                        with admission._state_lock:
                            if (admission._closed or not admission._busy or admission._active_marker is not marker
                                    or admission._owner_thread is not self._thread):
                                raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')
                        if (registry._dispatch_admissions.get(admission) is not binding
                                or binding.arguments != arguments or binding.pid != self._pid):
                            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                        operations = (('direct.parent.operation_v2', 'direct.parent.cancel_v2')
                                      if binding.operation == 'direct.parent.operation_v2'
                                      else ('direct.parent.query', 'direct.parent.cancel'))
                        for operation in operations:
                            code = registry._direct_dispatch_admission_error(
                                binding.registration_id, binding.service_epoch, binding.instance_id,
                                binding.principal, binding.identity, binding.scope, operation)
                            if code is not None:
                                raise ServiceDispatchAdmissionError(code)
                            row = registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                       (binding.registration_id,)).fetchone()
                            grant = None if row is None else registry.gateway_parent_grant(
                                instance_id=binding.instance_id, scope_ref=row['scope_ref'], identity=binding.identity,
                                scope=binding.scope, operation=operation)
                            if (grant is None or grant is not binding.parent_grant
                                    or self._instrument not in grant.instruments):
                                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
                    # close() can race with the checks above; repeat both final
                    # local admission-state fences before returning.
                    first, second = sorted(self._admissions, key=id)
                    with first._state_lock, second._state_lock:
                        if any(item._closed or item._active_marker is not marker
                               for item in (first, second)):
                            raise ServiceDispatchAdmissionError('ADMISSION_CLOSED')

                try:
                    check()
                    yield check
                    check()
                finally:
                    active = False
        except (_StaleOwnerError, sqlite3.Error):
            raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
        finally:
            for admission in acquired:
                with admission._state_lock:
                    if admission._active_marker is marker:
                        admission._busy = False
                        admission._active_marker = None


class _ExecutionActionBinding:
    """Registry-issued, thread-bound view of an operator action policy."""

    def __init__(self, registry, request, grant, generation):
        self._registry, self._request = registry, request
        self._grant, self._generation = grant, generation
        self._pid = _INTAKE_GETPID()
        self._thread = _INTAKE_CURRENT_THREAD()
        self._ident = _INTAKE_GET_IDENT()

    @contextmanager
    def scope(self):
        """Hold Registry through local Store commit, in Registry -> Store order.

        Caller must compare these bound request facts with the journal facts and
        call the yielded check immediately before its local commit. Do not run
        network operations or external callbacks in this scope.
        """
        registry, request = self._registry, self._request
        if _INTAKE_GETPID() != self._pid or self._pid != registry._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        if _INTAKE_CURRENT_THREAD() is not self._thread or _INTAKE_GET_IDENT() != self._ident:
            raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
        with registry._lock:
            if registry._intake_reentrant() or registry._execution_intake_owner is not None:
                raise ServiceDispatchAdmissionError('ADMISSION_BUSY')
            registry._ensure_open()
            key = _execution_action_key(request)
            active = True

            def check():
                if _INTAKE_GETPID() != self._pid:
                    raise ServiceDispatchAdmissionError('WRONG_PROCESS')
                if (_INTAKE_CURRENT_THREAD() is not self._thread
                        or _INTAKE_GET_IDENT() != self._ident):
                    raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
                if (not active or registry._closed
                        or registry._execution_action_grants.get(key) is not self._grant
                        or registry._execution_action_generations.get(key, 0) != self._generation
                        or not _execution_action_allows(self._grant, request)):
                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')

            check()
            registry._execution_intake_owner = self._thread
            registry._execution_intake_owner_ident = self._ident
            registry._execution_intake_get_ident = _INTAKE_GET_IDENT
            try:
                yield check
            finally:
                active = False
                registry._execution_intake_owner = None
                registry._execution_intake_owner_ident = None
                registry._execution_intake_get_ident = None


class ServiceRegistry:
    """SQLite-backed registration leases and authorization gate.

    ``principal`` passed to :meth:`handle` is the transport-authenticated key
    (for example, a CURVE public-key identity). Wire identity/scope values are
    always compared with ``grants``; they are never treated as credentials.

    Service-role portfolio writes remain denied. The only service-role
    strategy-scoped write is the exact account group-leg operation admitted by
    an operator policy and a persistent logical-run claim.

    Audited, strictly read-only registrations use epoch-local keys: a key is
    deduplicated while its session is active, but is not retained after expiry
    or restart. This intentionally changes the previous durable-key contract.
    Existing durable key bindings remain enforced; unlisted or mixed operations
    keep durable keys and cross the migration ownership write gate.
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
        enable_risk_v2_dispatch_admission: bool = False,
        enable_gateway_risk_first_reserve_admission: bool = False,
        enable_risk_admin_cancel_admission: bool = False,
        enable_risk_parent_execution_writes: bool = False,
        enable_gateway_retired_session_ack: bool = False,
    ) -> None:
        if not _text(service_id):
            raise ValueError("service_id is invalid")
        if type(enable_gateway_retired_session_ack) is not bool:
            raise TypeError("enable_gateway_retired_session_ack must be a bool")
        if not _text(policy_version) or not _text(requirements_version):
            raise ValueError("version references are invalid")
        if type(enable_risk_v2_dispatch_admission) is not bool:
            raise TypeError("enable_risk_v2_dispatch_admission must be a bool")
        if type(enable_gateway_risk_first_reserve_admission) is not bool:
            raise TypeError("enable_gateway_risk_first_reserve_admission must be a bool")
        if type(enable_risk_admin_cancel_admission) is not bool:
            raise TypeError("enable_risk_admin_cancel_admission must be a bool")
        if type(enable_risk_parent_execution_writes) is not bool:
            raise TypeError("enable_risk_parent_execution_writes must be a bool")
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
        self._gateway_retired_session_ack_enabled = enable_gateway_retired_session_ack
        # Despite its historical name, this opt-in covers both Risk v2
        # reserve and dispatch. Both remain disabled by default; a production
        # handler must consume and recheck its own operation-bound admission.
        self._risk_v2_dispatch_admission_enabled = enable_risk_v2_dispatch_admission
        self._risk_parent_execution_writes_enabled = enable_risk_parent_execution_writes
        self._gateway_risk_first_reserve_admission_enabled = enable_gateway_risk_first_reserve_admission
        # This opt-in only supplies a Registry admission. The Risk handler must
        # consume it and independently recheck its operator's scope grant.
        self._risk_admin_cancel_admission_configured = enable_risk_admin_cancel_admission
        self._risk_admin_cancel_admission_enabled = enable_risk_admin_cancel_admission
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
        self._ops_json_cache: OrderedDict[str, frozenset[str]] = OrderedDict()
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
        self._execution_intake_owner: threading.Thread | None = None
        self._execution_intake_owner_ident: int | None = None
        self._execution_intake_get_ident = None
        self._gateway_lease_watch = {}
        self._gateway_lease_grace = {}
        self._gateway_delegate_grants = {}
        self._gateway_parent_grants = {}
        self._gateway_netting_policies = {}
        self._gateway_netting_generations = {}
        self._gateway_netting_pairs = weakref.WeakKeyDictionary()
        self._gateway_acceptance_grants = {}
        self._gateway_acceptance_generations = {}
        self._execution_parent_grants = {}
        # Tombstones stay for the Registry lifetime: revoking and reinstalling
        # even the identical object must never revive an older permit.
        self._execution_parent_generations = {}
        self._execution_action_grants = {}
        self._execution_action_generations = {}
        self._execution_action_bindings = weakref.WeakKeyDictionary()
        self._execution_action_pid = _INTAKE_GETPID()
        # New local policy-check port only: epoch-local monotonic leases. Never
        # reconstruct a missing deadline from persisted wall-clock expiry.
        self._execution_release_monotonic_leases = {}
        self._execution_parent_monotonic_leases = {}
        self._execution_parent_read_leases = {}
        self._execution_parent_read_instances = set()
        # Composition-only, exact read-only prover for an existing fresh
        # intake receipt. Without it a stale write lease keeps failing shut.
        self._execution_parent_replay_prover = None
        self._parent_retry_unready = False
        self._hard_unready_generation = 0
        self._execution_parent_clock_lock = threading.Lock()
        self._execution_parent_clock_last = None
        self._execution_parent_clock_failed = False
        self._execution_parent_read_wall_lock = threading.Lock()
        self._execution_parent_read_wall_last = None
        self._execution_parent_read_wall_failed = False
        # Trusted owner-local coordinators remain unavailable until a separate
        # complete writer/lease coverage capability exists.
        self._dispatch_claim_coordinators = ()
        # Decision B: a dispatch claim may follow its last authority check by
        # at most the candidate window, so a lease must outlive that window.
        self._dispatch_lease_margin_seconds = 0.0
        self._dispatch_admissions: weakref.WeakKeyDictionary[
            ServiceDispatchAdmission, _DispatchAdmissionBinding
        ] = weakref.WeakKeyDictionary()
        self._risk_admin_cancel_admissions: weakref.WeakKeyDictionary[
            RiskAdminCancelAdmission, _RiskAdminCancelBinding
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
                if self._gateway_retired_session_ack_enabled:
                    self._retain_retired_gateway_sessions()
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

    def _intake_reentrant(self) -> bool:
        # The scope installs only its previously verified builtin get_ident.
        return (self._execution_intake_owner_ident is not None
                and self._execution_intake_get_ident() == self._execution_intake_owner_ident)

    def _record_migration_write_attempt(self, *, fact_kind: str) -> bool:
        """Seal before an authorized write, bound to the deployment epoch."""
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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

    def _migrate_freeze_schema(self) -> None:
        """Rename the legacy STOP_NEW fence schema in one write transaction.

        ``strategy_stop_new_receipts`` becomes ``strategy_freeze_receipts`` and
        ``strategy_write_claims.stop_new_at/stop_new_command_id`` become
        ``freeze_at/freeze_command_id``; rows are kept. A database that already
        holds both a legacy and a current name refuses to open
        (``REGISTRY_FREEZE_SCHEMA_CONFLICT``); any other failure rolls the whole
        step back and refuses to open (``REGISTRY_FREEZE_MIGRATION_FAILED``).
        """

        failed = ServiceRegistrySchemaError("REGISTRY_FREEZE_MIGRATION_FAILED")
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            raise failed from None
        try:
            tables = {
                str(row[0])
                for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
                    (_LEGACY_FREEZE_RECEIPT_TABLE, _FREEZE_RECEIPT_TABLE, "strategy_write_claims"),
                ).fetchall()
            }
            if _LEGACY_FREEZE_RECEIPT_TABLE in tables:
                if _FREEZE_RECEIPT_TABLE in tables:
                    raise ServiceRegistrySchemaError("REGISTRY_FREEZE_SCHEMA_CONFLICT")
                legacy_count = self._db.execute(
                    "SELECT COUNT(*) FROM strategy_stop_new_receipts"
                ).fetchone()[0]
                self._db.execute(
                    "ALTER TABLE strategy_stop_new_receipts RENAME TO strategy_freeze_receipts"
                )
                if (
                    self._db.execute("SELECT COUNT(*) FROM strategy_freeze_receipts").fetchone()[0]
                    != legacy_count
                ):
                    raise failed
            if "strategy_write_claims" in tables:
                columns = {
                    str(row[1])
                    for row in self._db.execute("PRAGMA table_info(strategy_write_claims)")
                }
                legacy = [old for old, _new in _LEGACY_FREEZE_CLAIM_COLUMNS if old in columns]
                current = [new for _old, new in _LEGACY_FREEZE_CLAIM_COLUMNS if new in columns]
                if legacy and current:
                    raise ServiceRegistrySchemaError("REGISTRY_FREEZE_SCHEMA_CONFLICT")
                for old, new in _LEGACY_FREEZE_CLAIM_COLUMNS:
                    if old in columns:
                        self._db.execute(
                            f"ALTER TABLE strategy_write_claims RENAME COLUMN {old} TO {new}"
                        )
                renamed = {
                    str(row[1])
                    for row in self._db.execute("PRAGMA table_info(strategy_write_claims)")
                }
                if any(old in renamed for old, _new in _LEGACY_FREEZE_CLAIM_COLUMNS):
                    raise failed
        except BaseException as error:
            self._db.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise failed from None
            raise
        try:
            self._db.execute("COMMIT")
        except sqlite3.Error:
            raise failed from None

    def _retain_retired_gateway_sessions(self) -> None:
        """Capture the preceding Gateway epoch within the fenced restart transaction."""
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS gateway_retired_sessions (
                registration_id TEXT PRIMARY KEY,
                service_epoch TEXT NOT NULL,
                successor_epoch TEXT NOT NULL,
                principal TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                scope_json TEXT NOT NULL
            )
            """
        )
        self._db.execute("DELETE FROM gateway_retired_sessions")
        self._db.execute(
            """
            INSERT INTO gateway_retired_sessions
                (registration_id, service_epoch, successor_epoch, principal, identity_json, scope_json)
            SELECT registration_id, service_epoch, ?, principal, identity_json, scope_json
            FROM sessions
            WHERE service_epoch = (SELECT value FROM registry_meta WHERE key='service_epoch')
              AND port IN ('service.account_read', 'service.direct_write', 'service.market_source')
            """,
            (self._epoch,),
        )

    def _create_schema(self) -> None:
        self._migrate_freeze_schema()
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
                freeze_at REAL,
                freeze_command_id TEXT,
                PRIMARY KEY(execution_domain_ref, strategy_ref, account_ref)
            );
            CREATE TABLE IF NOT EXISTS strategy_canonical_bindings (
                execution_domain_ref TEXT NOT NULL,
                strategy_ref TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                canonical_account_key TEXT NOT NULL,
                canonical_strategy_id TEXT NOT NULL,
                quarantined INTEGER NOT NULL CHECK(quarantined IN (0, 1)),
                PRIMARY KEY(execution_domain_ref, strategy_ref, account_ref)
            );
            CREATE INDEX IF NOT EXISTS strategy_canonical_lookup ON strategy_canonical_bindings
                (canonical_account_key, canonical_strategy_id);
            CREATE TABLE IF NOT EXISTS execution_subject_protected_accounts (
                account_ref TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS execution_subject_aliases (
                execution_domain_ref TEXT NOT NULL,
                strategy_ref TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                canonical_account_key TEXT,
                canonical_strategy_id TEXT,
                CHECK ((canonical_account_key IS NULL) = (canonical_strategy_id IS NULL)),
                PRIMARY KEY(execution_domain_ref, strategy_ref, account_ref)
            );
            CREATE TABLE IF NOT EXISTS execution_subject_pins (
                canonical_account_key TEXT NOT NULL,
                canonical_strategy_id TEXT NOT NULL,
                principal TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                run_id TEXT NOT NULL,
                PRIMARY KEY(canonical_account_key, canonical_strategy_id)
            );
            CREATE TABLE IF NOT EXISTS execution_subject_unknown_history (
                canonical_account_key TEXT NOT NULL,
                canonical_strategy_id TEXT NOT NULL,
                PRIMARY KEY(canonical_account_key, canonical_strategy_id)
            );
            CREATE TABLE IF NOT EXISTS strategy_freeze_receipts (
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
        if "freeze_at" not in columns:
            self._db.execute("ALTER TABLE strategy_write_claims ADD COLUMN freeze_at REAL")
        if "freeze_command_id" not in columns:
            self._db.execute(
                "ALTER TABLE strategy_write_claims ADD COLUMN freeze_command_id TEXT"
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
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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
            if self._intake_reentrant():
                raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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

    @contextmanager
    def _execution_intake_snapshot(self, owner_pid, owner_ident, owner_thread,
                                   getpid, get_ident, current_thread) -> Iterator[None]:
        """Hold the Registry snapshot; inherited fork state must never touch SQLite."""
        if getpid() != owner_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        if get_ident() != owner_ident or current_thread() is not owner_thread:
            raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
        self._lock.acquire()
        try:
            if self._execution_intake_owner is not None:
                raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
            if self._closed or self._db is None:
                raise sqlite3.ProgrammingError('registry is closed')
            db = self._db
            db.execute('BEGIN')
            try:
                row = db.execute("SELECT value FROM registry_meta WHERE key='service_epoch'").fetchone()
                if row is None or row['value'] != self._epoch:
                    raise _StaleOwnerError
                yield
            except BaseException:
                if (getpid() == owner_pid and get_ident() == owner_ident
                        and current_thread() is owner_thread):
                    db.rollback()
                raise
            else:
                if getpid() != owner_pid:
                    raise ServiceDispatchAdmissionError('WRONG_PROCESS')
                if get_ident() != owner_ident or current_thread() is not owner_thread:
                    raise ServiceDispatchAdmissionError('WRONG_OWNER_THREAD')
                try:
                    db.commit()
                except sqlite3.Error:
                    try:
                        db.rollback()
                    except sqlite3.Error:
                        pass
                    raise
        finally:
            if (getpid() == owner_pid and get_ident() == owner_ident
                    and current_thread() is owner_thread):
                self._lock.release()

    def _now(self) -> float:
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("clock returned an invalid value")
        return float(value)

    def _expire_sessions(self, now: float) -> None:
        # Only an explicitly configured strategy's real current-epoch lease
        # expiry can create an automatic DRAIN event. Revocation, explicit
        # unregister and stale service epochs are not expiry evidence.
        if self._gateway_lease_watch:
            expiry_write_checked = False
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
                if (not expiry_write_checked and self._db.execute(
                        'SELECT 1 FROM gateway_lease_expirations WHERE event_id=?', (event_id,)
                ).fetchone() is None):
                    self._record_migration_write_attempt(fact_kind='ownership')
                    expiry_write_checked = True
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
            if (not expiry_write_checked and self._db.execute(
                    "SELECT 1 FROM gateway_lease_expirations WHERE state='WAITING' AND apply_at<=? LIMIT 1",
                    (now,)).fetchone() is not None):
                self._record_migration_write_attempt(fact_kind='ownership')
            self._db.execute(
                "UPDATE gateway_lease_expirations SET state='PENDING' WHERE state='WAITING' AND apply_at<=?",
                (now,))
        # Eviction before transaction commit is conservative on rollback.
        for row in self._db.execute('SELECT registration_id FROM sessions WHERE service_epoch<>? OR expires_at<=?',
                                    (self._epoch, now)):
            self._execution_parent_monotonic_leases.pop(row['registration_id'], None)
            self._execution_parent_read_leases.pop(row['registration_id'], None)
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
            if self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='gateway_lease_expirations'").fetchone() is None:
                self._record_migration_write_attempt(fact_kind='ownership')
            self._db.execute('CREATE TABLE IF NOT EXISTS gateway_lease_expirations ('
                'event_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,account_ref TEXT NOT NULL,'
                'strategy_ref TEXT NOT NULL,run_id TEXT NOT NULL,principal TEXT NOT NULL,'
                'registration_id TEXT NOT NULL,service_epoch TEXT NOT NULL,expires_at REAL NOT NULL,'
                'observed_at REAL NOT NULL,state TEXT NOT NULL,apply_at REAL)')
            columns = {row[1] for row in self._db.execute('PRAGMA table_info(gateway_lease_expirations)')}
            if 'apply_at' not in columns:
                # A table written before the duration existed: its events were
                # applied at expiry, which is what a missing value keeps meaning.
                self._record_migration_write_attempt(fact_kind='ownership')
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
            if row['state'] == 'APPLIED':
                return
            self._record_migration_write_attempt(fact_kind='ownership')
            self._db.execute("UPDATE gateway_lease_expirations SET state='APPLIED' WHERE event_id=? AND state='PENDING'", (event_id,))

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
            if operation in _RISK_V2_ADMISSION_OPERATIONS and (
                not self._risk_v2_dispatch_admission_enabled
                or any(cap.port != "service.risk_order" for cap in capabilities)
                or any(operation not in cap.write_operations for cap in capabilities)
            ):
                raise ValueError("Risk v2 dispatch admission is not explicitly enabled")
            if operation == _GATEWAY_RISK_FIRST_RESERVE_V2 and (
                not self._gateway_risk_first_reserve_admission_enabled
                or any(cap.port != "service.direct_write" for cap in capabilities)
                or any(operation not in cap.write_operations for cap in capabilities)
            ):
                raise ValueError("Gateway Risk v2 admission is not explicitly enabled")
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

    def _enable_execution_parent_read_leases(self, instance_id):
        """Select the independent read fence before exposing a fresh intake."""
        with self._lock:
            self._ensure_open()
            capability = self._capabilities.get((instance_id, _EXECUTION_PARENT_PORT))
            if capability is None or _EXECUTION_PARENT_QUERY not in capability.operations:
                raise ValueError('execution parent query unavailable')
            if self._db.execute('SELECT 1 FROM sessions WHERE instance_id=? AND port=? AND service_epoch=? LIMIT 1',
                                (instance_id, _EXECUTION_PARENT_PORT, self._epoch)).fetchone() is not None:
                raise ValueError('execution parent sessions already exist')
            self._execution_parent_read_instances.add(instance_id)

    def set_ready(self, ready: bool) -> None:
        if type(ready) is not bool:
            raise TypeError("ready must be bool")
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            self._ready = ready
            self._parent_retry_unready = False
            if not ready:
                self._hard_unready_generation += 1
                self._execution_parent_monotonic_leases.clear()
                self._execution_parent_read_leases.clear()

    def _set_parent_retry_unready(self):
        """Pause all dispatch for an observed transient, without issuing a lease."""
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            # A stale retry must never weaken a preceding hard failure.
            if self._ready or self._parent_retry_unready:
                self._ready = False
                self._parent_retry_unready = True

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
        when no client is sending requests. Hard unready revokes Execution
        parent write tokens; transient readiness retry does not. Unregister,
        expiry, epoch and policy changes revoke their applicable leases. A read failure must
        propagate rather than masquerade as an empty set and release resources.
        """

        if not _text(instance_id) or not any(
            capability.instance_id == instance_id for capability in self._capabilities.values()
        ):
            raise ValueError("instance_id is not a declared capability")
        with self._lock:
            now = self._now()
            with self._read_snapshot():
                needs_expire = (
                    self._db.execute(
                        "SELECT 1 FROM sessions WHERE service_epoch <> ? OR expires_at <= ? LIMIT 1",
                        (self._epoch, now),
                    ).fetchone()
                    is not None
                )
                if not needs_expire and self._gateway_lease_watch:
                    needs_expire = (
                        self._db.execute(
                            "SELECT 1 FROM gateway_lease_expirations "
                            "WHERE state='WAITING' AND apply_at <= ? LIMIT 1",
                            (now,),
                        ).fetchone()
                        is not None
                    )
                if not needs_expire:
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
            with self._transaction():
                self._expire_sessions(now)
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
        return self.active_registration_contexts_for_ports(
            instance_id=instance_id, ports=(port,)
        )

    def active_registration_contexts_for_ports(
        self, *, instance_id: str, ports: tuple[str, ...]
    ) -> tuple[ActiveRegistrationContext, ...]:
        """Project eligible registrations for multiple ports of one instance in one snapshot."""

        if not _text(instance_id) or not ports:
            raise ValueError("registration capability is invalid")
        for port in ports:
            if not _text(port):
                raise ValueError("registration capability is invalid")
            if (instance_id, port) not in self._capabilities:
                raise ValueError("instance_id and port are not a declared capability")
        port_set = frozenset(ports)
        now = self._now()
        by_port: dict[str, list[ActiveRegistrationContext]] = {port: [] for port in port_set}
        with self._read_snapshot():
            if len(port_set) == 1:
                only_port = next(iter(port_set))
                rows = self._db.execute(
                    "SELECT * FROM sessions WHERE instance_id=? AND port=? "
                    "AND service_epoch=? AND policy_version=? AND requirements_version=? "
                    "AND expires_at>? ORDER BY registration_id",
                    (
                        instance_id,
                        only_port,
                        self._epoch,
                        self.policy_version,
                        self.requirements_version,
                        now,
                    ),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM sessions WHERE instance_id=? "
                    "AND service_epoch=? AND policy_version=? AND requirements_version=? "
                    "AND expires_at>? ORDER BY registration_id",
                    (
                        instance_id,
                        self._epoch,
                        self.policy_version,
                        self.requirements_version,
                        now,
                    ),
                ).fetchall()
            claims_by_key: dict[tuple[str, str, str], str] | None = None
            for row in rows:
                port = row["port"]
                if port not in port_set:
                    continue
                identity, scope = self._cached_identity_scope(row)
                principal = row["principal"]
                operations = self._cached_ops_set(row["operations_json"])
                write_operations = self._cached_ops_set(row["write_operations_json"])
                if not self._identity_is_trusted(principal, identity):
                    continue
                if not self._registration_policy_matches(row):
                    continue
                if not operations or not write_operations.issubset(operations):
                    continue
                if not self._current_resource_allows_all(
                    principal,
                    identity,
                    instance_id=instance_id,
                    port=port,
                    scope_ref=row["scope_ref"],
                    scope=scope,
                    operations=operations,
                ):
                    continue
                if scope.strategy_ref is not None and claims_by_key is None:
                    claims_by_key = {
                        (
                            claim_row["execution_domain_ref"],
                            claim_row["strategy_ref"],
                            claim_row["account_ref"],
                        ): claim_row["run_id"]
                        for claim_row in self._db.execute(
                            "SELECT execution_domain_ref, strategy_ref, account_ref, run_id "
                            "FROM strategy_write_claims"
                        )
                    }
                run_id = self._scope_strategy_write_run_id_in_snapshot(
                    scope, claims_by_key=claims_by_key
                )
                by_port[port].append(
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
        result: list[ActiveRegistrationContext] = []
        for port in ports:
            result.extend(by_port[port])
        return tuple(result)

    def _scope_strategy_write_run_id_in_snapshot(
        self,
        scope: WireScope,
        *,
        claims_by_key: Mapping[tuple[str, str, str], str] | None = None,
    ) -> str | None:
        """Return one exact durable run shared by every account in the scope.

        Group coordinators can be registered for a fixed account vector. Their
        lifecycle context must prove the same persistent logical run on every
        account before an owner may activate a group route. Missing or
        conflicting claims stay ineligible.
        """

        if scope.strategy_ref is None:
            return None

        def _lookup(account_ref: str | None) -> str | None:
            if (
                scope.execution_domain_ref is None
                or scope.strategy_ref is None
                or account_ref is None
            ):
                return None
            if claims_by_key is not None:
                return claims_by_key.get(
                    (scope.execution_domain_ref, scope.strategy_ref, account_ref)
                )
            return self._strategy_write_run_id_in_snapshot(
                scope.execution_domain_ref,
                scope.strategy_ref,
                account_ref,
            )

        if scope.account_ref is not None and not scope.account_refs:
            return _lookup(scope.account_ref)
        if scope.account_ref is not None or not scope.account_refs or scope.run_id is None:
            return None
        account_refs = scope.account_refs
        run_ids = tuple(_lookup(account_ref) for account_ref in account_refs)
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
            if not self._canonical_group_run_matches(
                execution_domain_ref, strategy_ref, account_ref, run_id, operation
            ):
                raise ServiceRegistryStrategyRunConflictError
            row = self._db.execute(
                "SELECT run_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
                (execution_domain_ref, strategy_ref, account_ref),
            ).fetchone()
            if row is not None:
                if row["run_id"] != run_id:
                    raise ServiceRegistryStrategyRunConflictError
                return run_id
            self._record_migration_write_attempt(fact_kind="ownership")
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
        if self._execution_action_pid != _INTAKE_GETPID():
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        with self._lock, self._registry_claim_changes_locked():
            if self._closed:
                return
            self._ready = False
            self._execution_parent_monotonic_leases.clear()
            self._execution_parent_read_leases.clear()
            if self._db is not None:
                self._db.close()
                self._db = None
            self._release_owner_lock()
            self._closed = True

    def handle(self, authenticated_principal: str, request: WireEnvelope) -> Mapping[str, Any]:
        """Authorize and handle one already-decoded v2 request envelope."""
        # Sample before the first Registry lock: queueing must consume lease
        # lifetime. A failed sample must not affect unrelated services.
        parent_started = None
        if type(request) is WireEnvelope:
            op = request.header.operation
            requested = request.payload.get('operations')
            if (op == 'service.renew' or (op == 'service.register'
                    and request.payload.get('port') == _EXECUTION_PARENT_PORT
                    and type(requested) is tuple and (_EXECUTION_PARENT_SUBMIT in requested
                        or (_EXECUTION_PARENT_QUERY in requested
                            and request.payload.get('instance_id') in self._execution_parent_read_instances)))):
                if self._execution_action_pid != _INTAKE_GETPID():
                    return self._failure('UNAVAILABLE')
                try:
                    parent_started = self._execution_parent_mono_now()
                except ServiceDispatchAdmissionError:
                    pass
        with self._lock:
            if self._intake_reentrant():
                return self._failure('UNAVAILABLE')
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
                return self._register(authenticated_principal, request, parent_started=parent_started)
            if operation == "service.renew":
                return self._renew(authenticated_principal, request, parent_started=parent_started)
            if operation == "service.unregister":
                return self._unregister(authenticated_principal, request)
            return self._dispatch(authenticated_principal, request)
        except sqlite3.Error:
            return self._failure("UNAVAILABLE")
        except MigrationGateError as error:
            return self._failure(_MIGRATION_WRITE_DISABLED if error.code == _MIGRATION_WRITE_DISABLED else "UNAVAILABLE")
        except OSError:
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

    def _cached_identity_scope(
        self, row: sqlite3.Row | Mapping[str, Any]
    ) -> tuple[WireIdentity, WireScope]:
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
            return cached[2], cached[3]
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
        return identity, scope

    def _cached_ops_set(self, raw_json: str) -> frozenset[str]:
        cached = self._ops_json_cache.get(raw_json)
        if cached is not None:
            self._ops_json_cache.move_to_end(raw_json)
            return cached
        parsed = frozenset(json.loads(raw_json))
        self._ops_json_cache[raw_json] = parsed
        self._ops_json_cache.move_to_end(raw_json)
        while len(self._ops_json_cache) > 2048:
            self._ops_json_cache.popitem(last=False)
        return parsed

    def _registration_policy_matches(self, row: sqlite3.Row | Mapping[str, Any]) -> bool:
        if (
            row["policy_version"] != self.policy_version
            or row["requirements_version"] != self.requirements_version
            or row["access_policy_ref"] != self.access_policy_ref
            or row["requirements_ref"] != self.requirements_ref
        ):
            return False
        identity, scope = self._cached_identity_scope(row)
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
            def visible_operations(capability: ServiceCapability) -> frozenset[str]:
                operations = capability.operations
                if (not self._gateway_risk_first_reserve_admission_enabled
                        or (capability.instance_id, _GATEWAY_RISK_FIRST_RESERVE_V2) not in self._handlers):
                    operations = operations - {_GATEWAY_RISK_FIRST_RESERVE_V2}
                return operations

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
                        "operations": sorted(visible_operations(capability)),
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
                    if visible_operations(capability)
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
    def _scope_matches(template: WireScope, requested: WireScope, identity: WireIdentity, *, port=None) -> bool:
        if identity.role is WireRole.STRATEGY_CLIENT:
            if template.run_id is not None:
                return (port == "service.calendar_read" and template.account_ref is not None
                        and template.strategy_ref == identity.actor_ref and requested == template)
            if template.strategy_ref is None:
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
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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
        if self._intake_reentrant():
            raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
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

    def configure_gateway_netting_policies(self, *, policies: tuple) -> None:
        """Install trusted account/service policies; wire requests cannot do so.

        This is not a strategy opt-in, does not authorize a transfer, and has no
        ServiceHost or Gateway integration. No configured policy means denied.
        """
        if (type(policies) is not tuple or not policies
                or any(type(policy) is not GatewayNettingServicePolicy for policy in policies)):
            raise ValueError('invalid gateway netting policies')
        entries = {(p.instance_id, p.identity, p.execution_domain_ref, p.account_ref): p for p in policies}
        if len(entries) != len(policies):
            raise ValueError('duplicate gateway netting policies')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if any(key in self._gateway_netting_policies for key in entries):
                raise ValueError('duplicate gateway netting policies')
            self._gateway_netting_policies.update(entries)
            for key in entries:
                self._gateway_netting_generations[key] = self._gateway_netting_generations.get(key, 0) + 1

    def revoke_gateway_netting_policy(self, policy: GatewayNettingServicePolicy) -> bool:
        if type(policy) is not GatewayNettingServicePolicy:
            raise ValueError('invalid gateway netting policy')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        key = (policy.instance_id, policy.identity, policy.execution_domain_ref, policy.account_ref)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._gateway_netting_policies.get(key) is not policy:
                return False
            del self._gateway_netting_policies[key]
            self._gateway_netting_generations[key] += 1
            return True

    def bind_gateway_parent_pair(self, first, first_arguments, second, second_arguments, *, instrument):
        """Bind two Registry-issued parent queries to an account policy.

        The returned scope establishes only current local service permissions;
        it neither proves parent existence/ownership or historic run binding,
        nor permits a transfer, quantity reservation, Gateway call, or trading.
        V2 source fields are request preconditions, not verified journal facts.
        """
        if (_INTAKE_GETPID() != self._execution_action_pid):
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        admissions, arguments = (first, second), (first_arguments, second_arguments)
        if (first is second or any(type(item) is not ServiceDispatchAdmission or item._registry is not self
                                  for item in admissions)
                or type(instrument) is not MarketInstrumentKey):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        encoded = []
        for admission, args in zip(admissions, arguments):
            binding = admission._binding
            scope = binding.scope
            common = {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id'}
            v2 = binding.operation == 'direct.parent.operation_v2'
            expected = common | {'operation_id', 'expected_source'} if v2 else common
            if (type(args) is not dict or set(args) != expected
                    or any(not _text(args[key]) for key in expected - {'expected_source'})
                    or args['account_scope'] != scope.account_ref or args['strategy_scope'] != scope.strategy_ref
                    or binding.operation not in {'direct.parent.query', 'direct.parent.operation_v2'}
                    or binding.identity.role is not WireRole.SERVICE
                    or binding.parent_grant is None or not admission.matches(args)):
                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
            if v2:
                source = args['expected_source']
                if (type(source) is not dict or set(source) != {'account_scope', 'journal_id', 'gateway_generation', 'minimum_revision'}
                        or source['account_scope'] != scope.account_ref
                        or not _text(source['journal_id']) or not _text(source['gateway_generation'])
                        or type(source['minimum_revision']) is not int or source['minimum_revision'] < 0):
                    raise ServiceDispatchAdmissionError('SCOPE_DENIED')
            encoded.append(encode_wire_value(args))
        left, right = (item._binding for item in admissions)
        if (left.instance_id != right.instance_id or left.service_epoch != right.service_epoch
                or left.operation != right.operation
                or left.principal != right.principal or left.identity != right.identity
                or left.scope.account_ref != right.scope.account_ref
                or left.scope.execution_domain_ref != right.scope.execution_domain_ref
                or left.scope.strategy_ref == right.scope.strategy_ref):
            raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        if left.operation == 'direct.parent.operation_v2':
            left_source, right_source = first_arguments['expected_source'], second_arguments['expected_source']
            if any(left_source[name] != right_source[name] for name in ('account_scope', 'journal_id', 'gateway_generation')):
                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
        key = (left.instance_id, left.identity, left.scope.execution_domain_ref, left.scope.account_ref)
        with self._lock:
            if self._intake_reentrant():
                raise ServiceDispatchAdmissionError('ADMISSION_BUSY')
            self._ensure_open()
            policy = self._gateway_netting_policies.get(key)
            if policy is None or instrument not in policy.instruments:
                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
            pair = _GatewayParentPairAdmission(_GATEWAY_PAIR_ISSUER, self, admissions, tuple(encoded),
                                               instrument, policy, self._gateway_netting_generations[key])
            self._gateway_netting_pairs[pair] = policy
            return pair

    def configure_gateway_parent_acceptance(self, *, grants: tuple) -> None:
        """Install dedicated operator grants; never decode these from a request."""
        from bt_api_py.gateway_parent_acceptance_service import GatewayParentAcceptanceGrant
        if (type(grants) is not tuple or not grants
                or any(type(g) is not GatewayParentAcceptanceGrant for g in grants)):
            raise ValueError('invalid acceptance policy')
        entries = {(g.instance_id, g.scope_ref, g.identity.principal_ref, g.account_scope): g for g in grants}
        if len(entries) != len(grants):
            raise ValueError('duplicate acceptance policy')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if any(key in self._gateway_acceptance_grants for key in entries):
                raise ValueError('duplicate acceptance policy')
            for grant in grants:
                expected_scope = WireScope(grant.execution_domain_ref, account_ref=grant.account_scope)
                capability = self._capabilities.get((grant.instance_id, 'service.direct_write'))
                if (capability is None or _GATEWAY_ACCEPTANCE_OPERATION not in capability.operations
                        or _GATEWAY_ACCEPTANCE_OPERATION in capability.write_operations
                        or not any(entry.identity == grant.identity and any(
                            resource.instance_id == grant.instance_id and resource.port == 'service.direct_write'
                            and resource.scope_ref == grant.scope_ref and resource.scope == expected_scope
                            and resource.operations == frozenset({_GATEWAY_ACCEPTANCE_OPERATION})
                            for resource in entry.resources)
                            for entry in self._grants.get(grant.identity.principal_ref, ()))):
                    raise ValueError('acceptance ACL unavailable')
            self._gateway_acceptance_grants.update(entries)
            for key in entries:
                self._gateway_acceptance_generations[key] = self._gateway_acceptance_generations.get(key, 0) + 1

    def revoke_gateway_parent_acceptance(self, grant) -> bool:
        from bt_api_py.gateway_parent_acceptance_service import GatewayParentAcceptanceGrant
        if type(grant) is not GatewayParentAcceptanceGrant:
            raise ValueError('invalid acceptance policy')
        key = (grant.instance_id, grant.scope_ref, grant.identity.principal_ref, grant.account_scope)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._gateway_acceptance_grants.get(key) is not grant:
                return False
            del self._gateway_acceptance_grants[key]
            self._gateway_acceptance_generations[key] = self._gateway_acceptance_generations.get(key, 0) + 1
            return True

    def _gateway_acceptance_generation_for(self, *, instance_id, scope_ref, identity, scope):
        return self._gateway_acceptance_generations.get(
            (instance_id, scope_ref, identity.principal_ref, scope.account_ref), 0)

    def gateway_parent_acceptance_grant(self, *, instance_id, scope_ref, identity, scope):
        if (type(identity) is not WireIdentity or identity.role is not WireRole.SERVICE
                or identity.delegate_ref is not None or type(scope) is not WireScope
                or scope.account_ref is None or scope.strategy_ref is not None or scope.run_id is not None
                or scope.account_refs or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                or scope.group_id is not None or scope.leg_id is not None):
            return None
        grant = self._gateway_acceptance_grants.get((instance_id, scope_ref, identity.principal_ref, scope.account_ref))
        if grant is None or grant.identity != identity or grant.execution_domain_ref != scope.execution_domain_ref:
            return None
        return grant

    def configure_execution_actions(self, *, grants: tuple) -> None:
        """Atomically install local operator policy; wire requests cannot call this."""
        if (type(grants) is not tuple or not grants
                or any(type(grant) is not ExecutionActionGrant for grant in grants)):
            raise ValueError('invalid execution action policy')
        entries = {_execution_action_key(grant): grant for grant in grants}
        if len(entries) != len(grants):
            raise ValueError('duplicate execution action policy')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if any(key in self._execution_action_grants for key in entries):
                raise ValueError('duplicate execution action policy')
            self._execution_action_grants.update(entries)
            for key in entries:
                self._execution_action_generations[key] = self._execution_action_generations.get(key, 0) + 1

    def revoke_execution_action(self, grant: ExecutionActionGrant) -> bool:
        if type(grant) is not ExecutionActionGrant:
            raise ValueError('invalid execution action policy')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        key = _execution_action_key(grant)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._execution_action_grants.get(key) is not grant:
                return False
            del self._execution_action_grants[key]
            self._execution_action_generations[key] += 1
            return True

    def replace_execution_action_grant(self, expected: ExecutionActionGrant,
                                       replacement: ExecutionActionGrant) -> bool:
        if type(expected) is not ExecutionActionGrant or type(replacement) is not ExecutionActionGrant:
            raise ValueError('invalid execution action policy')
        if (_execution_action_key(expected) != _execution_action_key(replacement)
                or expected.identity != replacement.identity):
            raise ValueError('execution action policy key changed')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        key = _execution_action_key(expected)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._execution_action_grants.get(key) is not expected:
                return False
            self._execution_action_grants[key] = replacement
            self._execution_action_generations[key] += 1
            return True

    def bind_execution_action(self, request: ExecutionActionRequest):
        """Bind a local action to current operator policy; no wire authority is created."""
        if type(request) is not ExecutionActionRequest:
            raise ValueError('invalid execution action request')
        if _INTAKE_GETPID() != self._execution_action_pid:
            raise ServiceDispatchAdmissionError('WRONG_PROCESS')
        with self._lock:
            if self._intake_reentrant():
                raise ServiceDispatchAdmissionError('INTAKE_REENTRANCY')
            self._ensure_open()
            key = _execution_action_key(request)
            grant = self._execution_action_grants.get(key)
            if not _execution_action_allows(grant, request):
                raise ServiceDispatchAdmissionError('SCOPE_DENIED')
            binding = _ExecutionActionBinding(self, request, grant, self._execution_action_generations[key])
            self._execution_action_bindings[binding] = (
                request, grant, self._execution_action_generations[key], key,
                _execution_action_facts(request), binding._pid, binding._thread, binding._ident)
            return binding

    def require_no_execution_subject_scopes(self) -> None:
        """Refuse omission of a previously installed Gateway declaration."""
        with self._read_snapshot():
            if (self._db.execute('SELECT 1 FROM execution_subject_protected_accounts LIMIT 1').fetchone()
                    or self._db.execute('SELECT 1 FROM execution_subject_aliases LIMIT 1').fetchone()
                    or self._db.execute('SELECT 1 FROM execution_subject_pins LIMIT 1').fetchone()
                    or self._db.execute('SELECT 1 FROM execution_subject_unknown_history LIMIT 1').fetchone()):
                raise ValueError('execution subject scopes omitted')

    def configure_execution_subject_scopes(self, *, protected_accounts: frozenset, aliases: Mapping) -> None:
        """Install an exact trusted classification; this is not wire input.

        Every writable alias in a protected account must be classified as
        either an ExecutionScope (selected) or None (ordinary direct). A
        missing alias fails closed. Existing configurations are immutable.
        """
        from bt_api_execution.contracts import ExecutionScope

        if (type(protected_accounts) is not frozenset or not protected_accounts
                or any(not _text(account) for account in protected_accounts)
                or not isinstance(aliases, Mapping) or not aliases):
            raise ValueError('invalid execution subject scopes')
        bindings = {}
        for wire, target in aliases.items():
            if (type(wire) is not WireScope or wire.account_ref not in protected_accounts
                    or not _text(wire.execution_domain_ref) or not _text(wire.strategy_ref)
                    or wire.run_id is not None or wire.account_refs or wire.manual_book_ref is not None
                    or wire.portfolio_ref is not None or wire.group_id is not None or wire.leg_id is not None
                    or (target is not None and type(target) is not ExecutionScope)):
                raise ValueError('invalid execution subject alias')
            key = (wire.execution_domain_ref, wire.strategy_ref, wire.account_ref)
            value = (None, None) if target is None else (target.account_key, target.strategy_id)
            if key in bindings and bindings[key] != value:
                raise ValueError('conflicting execution subject alias')
            bindings[key] = value
        if {key[2] for key in bindings} != set(protected_accounts):
            raise ValueError('unclassified protected account')
        by_strategy = {}
        for (_domain, strategy, account), canonical in bindings.items():
            key = (account, strategy)
            if key in by_strategy and by_strategy[key] != canonical:
                raise ValueError('conflicting cross-domain subject classification')
            by_strategy[key] = canonical
        with self._transaction():
            self._ensure_open()
            if self._db.execute('SELECT 1 FROM sessions LIMIT 1').fetchone() is not None:
                raise ValueError('execution subject scopes require inactive registry')
            old_accounts = {row['account_ref'] for row in self._db.execute(
                'SELECT account_ref FROM execution_subject_protected_accounts')}
            old_bindings = {(row['execution_domain_ref'], row['strategy_ref'], row['account_ref']):
                            (row['canonical_account_key'], row['canonical_strategy_id'])
                            for row in self._db.execute('SELECT * FROM execution_subject_aliases')}
            if old_accounts or old_bindings:
                if old_accounts != set(protected_accounts) or old_bindings != bindings:
                    raise ValueError('execution subject scopes changed')
                return
            for alias, canonical in bindings.items():
                existing = self._db.execute(
                    'SELECT canonical_account_key, canonical_strategy_id FROM strategy_canonical_bindings '
                    'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?', alias).fetchone()
                if existing is not None and (existing[0], existing[1]) != canonical:
                    raise ValueError('conflicting canonical subject scope')
                if canonical[0] is not None:
                    for other in self._db.execute(
                            'SELECT execution_domain_ref,strategy_ref,account_ref FROM strategy_canonical_bindings '
                            'WHERE canonical_account_key=? AND canonical_strategy_id=?', canonical):
                        if bindings.get(tuple(other)) != canonical:
                            raise ValueError('unclassified canonical subject alias')
            self._record_migration_write_attempt(fact_kind='ownership')
            self._db.executemany('INSERT INTO execution_subject_protected_accounts VALUES(?)',
                                 ((account,) for account in protected_accounts))
            self._db.executemany('INSERT INTO execution_subject_aliases VALUES(?,?,?,?,?)',
                                 ((*key, *value) for key, value in bindings.items()))
            # Classification cannot authenticate any prior claim. Keep a
            # durable quarantine even if that legacy row later disappears.
            for (account, strategy), canonical in by_strategy.items():
                if canonical[0] is None:
                    continue
                historical = self._db.execute(
                    'SELECT 1 FROM strategy_write_claims WHERE account_ref=? AND strategy_ref=? LIMIT 1',
                    (account, strategy)).fetchone()
                orphan_pin = self._db.execute(
                    'SELECT 1 FROM execution_subject_pins WHERE canonical_account_key=? '
                    'AND canonical_strategy_id=?', canonical).fetchone()
                if historical is not None or orphan_pin is not None:
                    self._db.execute('INSERT OR IGNORE INTO execution_subject_unknown_history VALUES(?,?)', canonical)

    def configure_strategy_canonical_targets(self, *, targets: Mapping) -> None:
        """Pin operator-composed wire aliases to Execution account/strategy identities.

        These bindings are durable and cannot be reinterpreted on restart. Old
        claims with no recorded binding are quarantined for explicit recovery;
        neither an expired registration nor a new mapping resolves them.
        """
        from bt_api_execution.contracts import ExecutionScope

        if not isinstance(targets, Mapping) or not targets:
            raise ValueError('invalid canonical strategy targets')
        bindings = {}
        for wire, target in targets.items():
            if (type(wire) is not WireScope or type(target) is not ExecutionScope
                    or wire.account_ref is None or wire.strategy_ref is None or wire.account_refs
                    or wire.manual_book_ref is not None or wire.portfolio_ref is not None
                    or wire.group_id is not None or wire.leg_id is not None):
                raise ValueError('invalid canonical strategy target')
            alias = (wire.execution_domain_ref, wire.strategy_ref, wire.account_ref)
            canonical = (target.account_key, target.strategy_id)
            previous = bindings.setdefault(alias, canonical)
            if previous != canonical:
                raise ValueError('conflicting canonical strategy target')
        with self._transaction():
            self._ensure_open()
            if self._db.execute('SELECT 1 FROM sessions LIMIT 1').fetchone() is not None:
                raise ValueError('canonical strategy targets require inactive registry')
            for alias, canonical in bindings.items():
                if self._db.execute('SELECT 1 FROM execution_subject_protected_accounts WHERE account_ref=?',
                                    (alias[2],)).fetchone() is not None:
                    classified = self._db.execute(
                        'SELECT canonical_account_key,canonical_strategy_id FROM execution_subject_aliases '
                        'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?', alias).fetchone()
                    if classified is None or tuple(classified) != canonical:
                        raise ValueError('conflicting canonical subject scope')
                row = self._db.execute(
                    'SELECT canonical_account_key, canonical_strategy_id FROM strategy_canonical_bindings '
                    'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?', alias).fetchone()
                if row is not None:
                    if (row['canonical_account_key'], row['canonical_strategy_id']) != canonical:
                        raise ValueError('canonical strategy target changed')
                    continue
                legacy = self._db.execute(
                    'SELECT 1 FROM strategy_write_claims WHERE execution_domain_ref=? '
                    'AND strategy_ref=? AND account_ref=?', alias).fetchone()
                # A new alias or quarantine is durable ownership state, not
                # ephemeral Registry setup. Seal the migration write boundary
                # before creating it; reading an existing binding is harmless.
                self._record_migration_write_attempt(fact_kind='ownership')
                self._db.execute(
                    'INSERT INTO strategy_canonical_bindings VALUES(?,?,?,?,?,?)',
                    (*alias, *canonical, int(legacy is not None)))

    def configure_execution_parents(self, *, grants: tuple) -> None:
        """Install exact operator grants atomically; never called from wire input."""
        if (type(grants) is not tuple or not grants
                or any(type(grant) is not ExecutionParentGrant for grant in grants)):
            raise ValueError('invalid execution parent policy')
        entries = {(g.instance_id, g.scope_ref, g.identity.principal_ref, g.account_ref, g.strategy_ref): g
                   for g in grants}
        if len(entries) != len(grants):
            raise ValueError('duplicate execution parent policy')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if any(key in self._execution_parent_grants for key in entries):
                raise ValueError('duplicate execution parent policy')
            self._execution_parent_grants.update(entries)
            for key in entries:
                self._execution_parent_generations[key] = self._execution_parent_generations.get(key, 0) + 1

    def install_execution_parent_replay_prover(self, prover) -> None:
        """Install the exact composition-owned fresh-intake replay prover.

        Composition-only: this is never reachable from a wire request, and no
        request field, flag, name or prefix can select or configure it. The
        prover must be an instance of the exact
        ``bt_api_execution.submission_intake.FreshSubmissionReplayProver``
        class; a look-alike object is rejected. Without an installed prover a
        stale parent write lease keeps returning ``SCOPE_DENIED`` unchanged.
        """
        from bt_api_execution.submission_intake import FreshSubmissionReplayProver

        if type(prover) is not FreshSubmissionReplayProver:
            raise ValueError('invalid execution parent replay prover')
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._execution_parent_replay_prover is not None:
                raise ValueError('duplicate execution parent replay prover')
            self._execution_parent_replay_prover = prover

    def revoke_execution_parent(self, grant: ExecutionParentGrant) -> bool:
        """Revoke only the exact locally installed grant, invalidating permits."""
        if type(grant) is not ExecutionParentGrant:
            raise ValueError('invalid execution parent policy')
        key = (grant.instance_id, grant.scope_ref, grant.identity.principal_ref,
               grant.account_ref, grant.strategy_ref)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._execution_parent_grants.get(key) is not grant:
                return False
            del self._execution_parent_grants[key]
            self._execution_parent_generations[key] = self._execution_parent_generations.get(key, 0) + 1
            return True

    def replace_execution_parent_grant(self, expected: ExecutionParentGrant,
                                       replacement: ExecutionParentGrant) -> bool:
        """Replace one operator grant using object-identity CAS under the lock."""
        if type(expected) is not ExecutionParentGrant or type(replacement) is not ExecutionParentGrant:
            raise ValueError('invalid execution parent policy')
        full_expected = (expected.instance_id, expected.scope_ref, expected.identity,
                         expected.account_ref, expected.strategy_ref, expected.execution_domain_ref)
        full_replacement = (replacement.instance_id, replacement.scope_ref, replacement.identity,
                            replacement.account_ref, replacement.strategy_ref, replacement.execution_domain_ref)
        if full_expected != full_replacement:
            raise ValueError('execution parent policy key changed')
        key = (expected.instance_id, expected.scope_ref, expected.identity.principal_ref,
               expected.account_ref, expected.strategy_ref)
        with self._lock, self._registry_claim_changes_locked():
            self._ensure_open()
            if self._execution_parent_grants.get(key) is not expected:
                return False
            self._execution_parent_grants[key] = replacement
            self._execution_parent_generations[key] = self._execution_parent_generations.get(key, 0) + 1
            return True

    def _execution_parent_generation_for(self, *, instance_id, scope_ref, identity, scope):
        key = (instance_id, scope_ref, identity.principal_ref, scope.account_ref, scope.strategy_ref)
        return self._execution_parent_generations.get(key, 0)

    def execution_parent_grant(self, *, instance_id, scope_ref, identity, scope, operation):
        if (operation not in _EXECUTION_PARENT_OPERATIONS or type(identity) is not WireIdentity
                or identity.role is not WireRole.STRATEGY_CLIENT or identity.delegate_ref is not None
                or type(scope) is not WireScope or scope.account_ref is None or scope.strategy_ref is None
                or scope.run_id is None or scope.account_refs or scope.manual_book_ref is not None
                or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None):
            return None
        grant = self._execution_parent_grants.get((instance_id, scope_ref, identity.principal_ref,
                                                    scope.account_ref, scope.strategy_ref))
        if (grant is None or grant.identity != identity or grant.execution_domain_ref != scope.execution_domain_ref
                or operation not in grant.operations):
            return None
        return grant

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
                   _GATEWAY_RISK_FIRST_RESERVE_V2:'direct.reserve',
                   'direct.delegated_dispatch':'direct.dispatch',
                   'direct.delegate_actor_status':'direct.command_status',
                   'direct.delegate_actor_cancel':'direct.cancel',
                   'direct.delegate_actor_cancel_status':'direct.cancel_status',
                   'direct.delegate_actor_release':'direct.release',
                   'direct.delegate_actor_release_status':'direct.command_status',
                   'direct.delegate_actor_status_v2':'direct.command_status',
                   'direct.delegate_actor_release_status_v2':'direct.command_status',
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
                or logical not in grant.operations
                or (operation == _GATEWAY_RISK_FIRST_RESERVE_V2 and grant.actor_kind != 'risk')):
            return None
        return grant

    def _resource_scope_matches(self, resource, requested, identity):
        if self._scope_matches(resource.scope, requested, identity, port=resource.port):
            return True
        if (
            self._risk_parent_execution_writes_enabled
            and identity.role is WireRole.SERVICE
            and identity.delegate_ref is None
            and resource.port == "service.risk_order"
            and resource.scope.run_id is None
            and requested.run_id is not None
            and requested.account_ref is not None
            and not requested.account_refs
            and requested.strategy_ref is not None
            and requested.manual_book_ref is None
            and requested.portfolio_ref is None
            and requested.group_id is None
            and requested.leg_id is None
        ):
            return requested == replace(resource.scope, run_id=requested.run_id)
        if (identity.role is not WireRole.SERVICE or resource.port != 'service.direct_write'
                or not resource.operations or not resource.operations <= (_GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_PARENT_OPERATIONS)
                or resource.scope.run_id is not None or requested.run_id is None):
            return False
        if not all((self.gateway_parent_grant if op in _GATEWAY_PARENT_OPERATIONS else self.gateway_delegate_grant)(
                   instance_id=resource.instance_id, scope_ref=resource.scope_ref,
                   identity=identity, scope=requested, operation=op) is not None for op in resource.operations):
            return False
        return requested == replace(resource.scope, run_id=requested.run_id)

    def _gateway_delegate_strategy_run_current(self, scope, *, instance_id):
        if not self._strategy_owner_run_claim_exists(scope):
            return False
        claim = self._db.execute(
            'SELECT active_registration_id FROM strategy_write_claims WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=? AND run_id=?',
            (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref, scope.run_id)).fetchone()
        row = None if claim is None else self._db.execute(
            'SELECT * FROM sessions WHERE registration_id=? AND service_epoch=? AND expires_at>?',
            (claim['active_registration_id'], self._epoch, self._now())).fetchone()
        if (row is None or row['instance_id'] != instance_id or row['port'] != 'service.direct_write'
                or row['policy_version'] != self.policy_version
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
        family = ('account.order_cut.' if operation.startswith('account.order_cut.') else
                  'account.facts.' if operation.startswith('account.facts.') else
                  'account.cancel_attempts.' if operation.startswith('account.cancel_attempts.') else 'account.events.')
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
            and self._canonical_group_run_matches(
                scope.execution_domain_ref, scope.strategy_ref, account_ref, policy.run_id, operation
            )
        )

    def _canonical_group_run_matches(self, domain, strategy, account, run_id, operation):
        """Check delegated group work against configured strategy aliases.

        Group service registrations are delegates, not competing strategy
        clients. An active claim for the same run is therefore permissible.
        """
        rows = self._db.execute(
            'SELECT other.quarantined, claim.run_id, claim.freeze_command_id '
            'FROM strategy_canonical_bindings current JOIN strategy_canonical_bindings other '
            'ON other.canonical_account_key=current.canonical_account_key '
            'AND other.canonical_strategy_id=current.canonical_strategy_id '
            'LEFT JOIN strategy_write_claims claim '
            'ON claim.execution_domain_ref=other.execution_domain_ref '
            'AND claim.strategy_ref=other.strategy_ref AND claim.account_ref=other.account_ref '
            'WHERE current.execution_domain_ref=? AND current.strategy_ref=? AND current.account_ref=?',
            (domain, strategy, account)).fetchall()
        return not any(
            row['quarantined'] or (row['run_id'] is not None and row['run_id'] != run_id)
            or (operation in _FREEZE_OPERATIONS and row['run_id'] == run_id
                and row['freeze_command_id'] is not None)
            for row in rows
        )

    def _execution_release_policy(self, instance_id, identity, scope):
        if not _is_execution_release_scope(identity, _EXECUTION_RELEASE_PORT, scope):
            return None
        key = (instance_id, identity.principal_ref, scope.account_ref,
               scope.strategy_ref, scope.execution_domain_ref)
        grant = self._execution_action_grants.get(key)
        return (grant if type(grant) is ExecutionActionGrant and grant.identity == identity
                and 'release_child' in grant.actions else None)

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
        if (_GATEWAY_RISK_FIRST_RESERVE_V2 in requested_operations
                and not self._gateway_risk_first_reserve_admission_enabled):
            return "CAPABILITY_UNAVAILABLE", None, frozenset()
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
        if (requested_operations & _GATEWAY_ORDER_CUT_OPERATIONS and
                (port != 'service.account_read' or identity.role is not WireRole.SERVICE
                 or identity.delegate_ref is not None or scope.account_ref is None
                 or scope != WireScope(scope.execution_domain_ref, account_ref=scope.account_ref))):
            return "SCOPE_DENIED", capability, frozenset()
        if port == _PRODUCT_PORT and (requested_operations != _PRODUCT_OPERATIONS
                                     or identity.role is not WireRole.SERVICE):
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
        if requested_operations & _EXECUTION_RELEASE_OPERATIONS or port == _EXECUTION_RELEASE_PORT:
            if (not requested_operations <= _EXECUTION_RELEASE_OPERATIONS
                    or not _is_execution_release_scope(identity, port, scope)
                    or self._execution_release_policy(instance_id, identity, scope) is None):
                return 'SCOPE_DENIED', capability, frozenset()
        if requested_operations & _EXECUTION_PARENT_OPERATIONS:
            if (port != _EXECUTION_PARENT_PORT or not requested_operations <= _EXECUTION_PARENT_OPERATIONS
                    or not capability.operations <= _EXECUTION_PARENT_OPERATIONS
                    or (_EXECUTION_PARENT_SUBMIT in capability.operations
                        and _EXECUTION_PARENT_SUBMIT not in capability.write_operations)
                    or _EXECUTION_PARENT_QUERY in capability.write_operations
                    or any(self.execution_parent_grant(instance_id=instance_id, scope_ref=scope_ref,
                           identity=identity, scope=scope, operation=op) is None
                           for op in requested_operations & _EXECUTION_PARENT_OPERATIONS)):
                return 'SCOPE_DENIED', capability, frozenset()
        elif port == _EXECUTION_PARENT_PORT:
            return 'SCOPE_DENIED', capability, frozenset()
        if requested_operations & _GATEWAY_ALL_DELEGATE_OPERATIONS:
            if (port != 'service.direct_write' or any(self.gateway_delegate_grant(
                    instance_id=instance_id, scope_ref=scope_ref, identity=identity, scope=scope, operation=op
                    ) is None for op in requested_operations & _GATEWAY_ALL_DELEGATE_OPERATIONS)
                    or not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id)):
                return 'SCOPE_DENIED', capability, frozenset()
        if requested_operations & _GATEWAY_PARENT_OPERATIONS:
            if (port != 'service.direct_write' or any(self.gateway_parent_grant(
                    instance_id=instance_id, scope_ref=scope_ref, identity=identity, scope=scope, operation=op
                    ) is None for op in requested_operations & _GATEWAY_PARENT_OPERATIONS)
                    or not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id)):
                return 'SCOPE_DENIED', capability, frozenset()
        if _GATEWAY_ACCEPTANCE_OPERATION in requested_operations:
            if (port != 'service.direct_write' or requested_operations != frozenset({_GATEWAY_ACCEPTANCE_OPERATION})
                    or _GATEWAY_ACCEPTANCE_OPERATION in capability.write_operations
                    or self.gateway_parent_acceptance_grant(instance_id=instance_id, scope_ref=scope_ref,
                        identity=identity, scope=scope) is None):
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
        # The built-in Risk admin manifest declares these operations as
        # writes. Also classify them here when an older/custom capability
        # omitted that declaration: a missing label cannot relax authorization.
        granted_write_operations = granted & (
            capability.write_operations
            | (_RISK_ADMIN_WRITE_OPERATIONS if port == "service.risk_admin" else frozenset())
        )
        if granted_write_operations:
            if (port == "service.risk_admin"
                    and granted_write_operations <= _RISK_ADMIN_WRITE_OPERATIONS):
                if not _risk_admin_write_scope(identity, scope):
                    return "SCOPE_DENIED", capability, frozenset()
            elif (identity.role is WireRole.APPLICATION_CLIENT and port == "service.direct_write"
                    and granted_write_operations <= _GATEWAY_AUTHENTICATED_CONTROL_WRITE_OPERATIONS):
                if (scope.account_ref is None or scope.account_refs or scope.manual_book_ref is not None
                        or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None
                        or identity.delegate_ref is not None):
                    return "SCOPE_DENIED", capability, frozenset()
            elif (identity.role is WireRole.SERVICE and port == 'service.direct_write'
                  and granted_write_operations <= (_GATEWAY_PARENT_WRITE_OPERATIONS | _GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS)
                  and all((self.gateway_parent_grant if op in _GATEWAY_PARENT_WRITE_OPERATIONS else self.gateway_delegate_grant)(
                          instance_id=instance_id,scope_ref=scope_ref,identity=identity,scope=scope,operation=op)
                          is not None for op in granted_write_operations)):
                if not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id):
                    return "DUPLICATE_ACTIVE_RUN", capability, frozenset()
            elif (
                self._risk_parent_execution_writes_enabled
                and identity.role is WireRole.SERVICE
                and port == "service.risk_order"
                and granted_write_operations <= _RISK_EXECUTION_PARENT_WRITE_OPERATIONS
            ):
                if (
                    scope.account_ref is None
                    or scope.account_refs
                    or scope.strategy_ref is None
                    or scope.run_id is None
                    or scope.manual_book_ref is not None
                    or scope.portfolio_ref is not None
                    or scope.group_id is not None
                    or scope.leg_id is not None
                    or identity.delegate_ref is not None
                ):
                    return "SCOPE_DENIED", capability, frozenset()
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

    @staticmethod
    def _execution_release_session_identity(row):
        """Freeze all registration fields except the renewable wall expiry."""
        return tuple((key, row[key]) for key in row.keys() if key != 'expires_at')

    def _execution_release_mono_now(self):
        if self._execution_action_pid != _INTAKE_GETPID():
            raise ServiceDispatchAdmissionError('UNAVAILABLE')
        if os.name == 'nt':
            from bt_api_py._win32_security import (
                Win32SecurityUnavailable,
                sleep_inclusive_clock_ns,
            )

            try:
                value = sleep_inclusive_clock_ns() / 1_000_000_000.0
            except (Win32SecurityUnavailable, OSError, OverflowError, TypeError, ValueError):
                raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
        else:
            if (type(_RELEASE_SLEEP_CLOCK) is not BuiltinFunctionType
                    or _RELEASE_SLEEP_CLOCK.__module__ != 'time'
                    or _RELEASE_SLEEP_CLOCK.__name__ != 'clock_gettime'
                    or type(_RELEASE_SLEEP_CLOCK_ID) is not int):
                raise ServiceDispatchAdmissionError('UNAVAILABLE')
            try:
                value = _RELEASE_SLEEP_CLOCK(_RELEASE_SLEEP_CLOCK_ID)
            except (OSError, OverflowError, ValueError):
                raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ServiceDispatchAdmissionError('UNAVAILABLE')
        return value

    def _execution_release_live_lease(self, row):
        """Return only a same-process/epoch, unexpired registration deadline."""
        lease = self._execution_release_monotonic_leases.get(row['registration_id'])
        if (type(lease) is not tuple or len(lease) != 5
                or lease[0] != self._epoch or lease[1] != self._execution_action_pid
                or lease[2] != row['expires_at']
                or lease[4] != self._execution_release_session_identity(row)
                or self._execution_release_mono_now() >= lease[3]):
            return None
        return lease

    def _execution_parent_mono_now(self, getpid=_INTAKE_GETPID):
        """Sample a suspend-inclusive clock; any fault poisons this Registry."""
        if self._execution_action_pid != getpid():
            raise ServiceDispatchAdmissionError('UNAVAILABLE')
        with self._execution_parent_clock_lock:
            if self._execution_parent_clock_failed:
                raise ServiceDispatchAdmissionError('UNAVAILABLE')
            try:
                if os.name == 'nt':
                    from bt_api_py._win32_security import (
                        Win32SecurityUnavailable,
                        sleep_inclusive_clock_ns,
                    )

                    try:
                        value = sleep_inclusive_clock_ns() / 1_000_000_000.0
                    except Win32SecurityUnavailable:
                        raise ValueError from None
                else:
                    if (type(_RELEASE_SLEEP_CLOCK) is not BuiltinFunctionType
                            or _RELEASE_SLEEP_CLOCK.__module__ != 'time'
                            or _RELEASE_SLEEP_CLOCK.__name__ != 'clock_gettime'
                            or type(_PARENT_SLEEP_CLOCK_ID) is not int):
                        raise ValueError
                    value = _RELEASE_SLEEP_CLOCK(_PARENT_SLEEP_CLOCK_ID)
                if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                        or (self._execution_parent_clock_last is not None
                            and value < self._execution_parent_clock_last)):
                    raise ValueError
            except (OSError, OverflowError, TypeError, ValueError):
                self._execution_parent_clock_failed = True
                raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
            self._execution_parent_clock_last = value
            return value

    def _execution_parent_lease_policy(self, row):
        identity, scope = self._stored_identity(row['identity_json']), self._stored_scope(row['scope_json'])
        grant = self.execution_parent_grant(instance_id=row['instance_id'], scope_ref=row['scope_ref'],
                                             identity=identity, scope=scope, operation=_EXECUTION_PARENT_SUBMIT)
        generation = self._execution_parent_generation_for(instance_id=row['instance_id'],
            scope_ref=row['scope_ref'], identity=identity, scope=scope)
        return grant, generation

    def _execution_parent_read_policy(self, row):
        identity, scope = self._stored_identity(row['identity_json']), self._stored_scope(row['scope_json'])
        grant = self.execution_parent_grant(instance_id=row['instance_id'], scope_ref=row['scope_ref'],
                                             identity=identity, scope=scope, operation=_EXECUTION_PARENT_QUERY)
        generation = self._execution_parent_generation_for(instance_id=row['instance_id'],
            scope_ref=row['scope_ref'], identity=identity, scope=scope)
        return grant, generation

    def _execution_parent_read_wall_now(self, getpid=_INTAKE_GETPID):
        if self._execution_action_pid != getpid():
            raise ServiceDispatchAdmissionError('UNAVAILABLE')
        with self._execution_parent_read_wall_lock:
            if self._execution_parent_read_wall_failed:
                raise ServiceDispatchAdmissionError('UNAVAILABLE')
            try:
                value = self._clock()
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or (self._execution_parent_read_wall_last is not None
                            and value < self._execution_parent_read_wall_last)):
                    raise ValueError
            except Exception:
                self._execution_parent_read_wall_failed = True
                raise ServiceDispatchAdmissionError('UNAVAILABLE') from None
            self._execution_parent_read_wall_last = value
            return value

    def _execution_parent_live_read_lease(self, row):
        lease = self._execution_parent_read_leases.get(row['registration_id'])
        if (type(lease) is not tuple or len(lease) != 10 or lease[0] is not self
                or lease[1] != self._execution_action_pid or self._execution_action_pid != _INTAKE_GETPID()
                or lease[2] != self._epoch or lease[3] != self._execution_release_session_identity(row)
                or lease[4] != row['expires_at'] or self._execution_parent_mono_now() >= lease[5]
                or lease[8] is not self._grants.get(row['principal']) or lease[9] is not self._capabilities):
            return None
        self._execution_parent_read_wall_now()
        grant, generation = self._execution_parent_read_policy(row)
        identity, scope = self._stored_identity(row['identity_json']), self._stored_scope(row['scope_json'])
        if (grant is None or grant is not lease[6] or generation != lease[7]
                or not self._identity_is_trusted(row['principal'], identity)
                or not self._current_resource_allows(row['principal'], identity, instance_id=row['instance_id'],
                    port=row['port'], scope_ref=row['scope_ref'], scope=scope, operation=_EXECUTION_PARENT_QUERY)
                or row['policy_version'] != self.policy_version
                or row['requirements_version'] != self.requirements_version
                or not self._registration_policy_matches(row)):
            return None
        return lease

    def _execution_parent_live_lease(self, row):
        lease = self._execution_parent_monotonic_leases.get(row['registration_id'])
        if (type(lease) is not tuple or len(lease) != 8 or lease[0] is not self
                or lease[1] != self._execution_action_pid or self._execution_action_pid != _INTAKE_GETPID()
                or lease[2] != self._epoch or lease[3] != self._execution_release_session_identity(row)
                or lease[4] != row['expires_at'] or self._execution_parent_mono_now() >= lease[5]):
            return None
        grant, generation = self._execution_parent_lease_policy(row)
        if grant is None or grant is not lease[6] or generation != lease[7]:
            return None
        return lease

    def _execution_parent_replay_lease(self, row):
        """The still-live registration lease that a replay proof may rely on.

        This is the same structural lease as ``_execution_parent_live_lease``
        except that the grant/generation binding, which the new authorization
        necessarily superseded, is replaced by the exact prover proof. It is
        private and used only together with such a proof.
        """
        lease = self._execution_parent_monotonic_leases.get(row['registration_id'])
        if (type(lease) is not tuple or len(lease) != 8 or lease[0] is not self
                or lease[1] != self._execution_action_pid or self._execution_action_pid != _INTAKE_GETPID()
                or lease[2] != self._epoch or lease[3] != self._execution_release_session_identity(row)
                or lease[4] != row['expires_at'] or self._execution_parent_mono_now() >= lease[5]):
            return None
        return lease

    def _execution_parent_replay_matches(self, proof, *, registration_id, service_epoch,
                                         instance_id, principal, identity, scope, row, grant,
                                         generation):
        """Require the exact proof for this registration and the current grant."""
        if type(proof) is not ExecutionParentReplayProof:
            return False
        if (type(grant) is not ExecutionParentGrant or grant is not proof.grant
                or generation != proof.generation
                or proof.registration_id != registration_id
                or proof.service_epoch != service_epoch
                or proof.instance_id != instance_id
                or proof.instance_id != row['instance_id']
                or proof.principal != principal
                or proof.identity != identity
                or proof.scope != scope):
            return False
        namespace = execution_parent_replay_namespace(grant)
        return namespace is not None and proof.namespace == namespace

    def _execution_parent_replay_for_dispatch(self, row, principal, identity, scope, arguments):
        """Query the installed prover only for a stale write lease.

        Returns ``None`` without a prover, with a still-current lease, with a
        dead registration lease, or when the exact receipt is not proven. The
        proof is revalidated against the current grant before it is returned.
        """
        prover = self._execution_parent_replay_prover
        if prover is None or type(arguments) is not MappingProxyType:
            return None
        lease = self._execution_parent_monotonic_leases.get(row['registration_id'])
        if type(lease) is not tuple or len(lease) != 8:
            return None
        grant, generation = self._execution_parent_lease_policy(row)
        if grant is lease[6] and generation == lease[7]:
            return None
        if self._execution_parent_replay_lease(row) is None:
            return None
        try:
            proof = prover.prove(
                registration_id=row['registration_id'], service_epoch=self._epoch,
                instance_id=row['instance_id'], principal=principal, identity=identity,
                scope=scope, grant=grant, generation=generation, arguments=arguments)
        except Exception:
            return None
        if not self._execution_parent_replay_matches(
                proof, registration_id=row['registration_id'], service_epoch=self._epoch,
                instance_id=row['instance_id'], principal=principal, identity=identity,
                scope=scope, row=row, grant=grant, generation=generation):
            return None
        try:
            request_bytes = encode_wire_value(arguments)
        except Exception:
            return None
        if (proof.request_bytes != request_bytes
                or proof.request_id != arguments.get('request_id')):
            return None
        return proof

    def _register(self, principal: str, request: WireEnvelope, *, parent_started=None) -> Mapping[str, Any]:
        operations = request.payload.get('operations')
        if (request.payload.get('port') == _EXECUTION_PARENT_PORT and type(operations) is tuple
                and (_EXECUTION_PARENT_SUBMIT in operations
                     or (_EXECUTION_PARENT_QUERY in operations
                         and request.payload.get('instance_id') in self._execution_parent_read_instances))):
            return self._register_execution_parent(principal, request, parent_started)
        if request.payload.get('port') != _EXECUTION_RELEASE_PORT:
            return self._register_impl(principal, request)
        # Capture before any authorization, journal lock or commit: time spent
        # establishing a lease cannot extend its monotonic lifetime.
        try:
            started = self._execution_release_mono_now()
            deadline = started + self.lease_seconds
            if not math.isfinite(deadline):
                return self._failure('UNAVAILABLE')
            registration_key = request.payload.get('registration_key')
            with self._lock:
                self._ensure_open()
                prior = self._db.execute(
                    'SELECT registration_id FROM sessions WHERE principal=? AND registration_key=? AND service_epoch=?',
                    (principal, registration_key, self._epoch)).fetchone()
                result = self._register_impl(principal, request)
                if not result.get('ok'):
                    return result
                registration_id = result['result']['registration_id']
                row = self._db.execute('SELECT * FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()
                if row is None or row['port'] != _EXECUTION_RELEASE_PORT:
                    return self._failure('UNAVAILABLE')
                if prior is not None and prior['registration_id'] == registration_id:
                    # A retry cannot acquire a fresh deadline for an older or
                    # uncertain registration, even when its wall clock moved.
                    if self._execution_release_live_lease(row) is None:
                        return self._failure('NOT_REGISTERED')
                else:
                    if self._execution_release_mono_now() >= deadline:
                        return self._failure('NOT_REGISTERED')
                    self._execution_release_monotonic_leases = {
                        key: value for key, value in self._execution_release_monotonic_leases.items()
                        if value[3] > started
                    }
                    self._execution_release_monotonic_leases[registration_id] = (
                        self._epoch, self._execution_action_pid, row['expires_at'], deadline,
                        self._execution_release_session_identity(row))
                return result
        except (ServiceDispatchAdmissionError, sqlite3.Error, KeyError, TypeError, ValueError):
            return self._failure('UNAVAILABLE')

    def _register_execution_parent(self, principal, request, started):
        if started is None:
            return self._failure('UNAVAILABLE')
        try:
            operations = frozenset(request.payload.get('operations', ()))
            has_write = _EXECUTION_PARENT_SUBMIT in operations
            has_read = (_EXECUTION_PARENT_QUERY in operations
                        and request.payload.get('instance_id') in self._execution_parent_read_instances)
            if has_read:
                self._execution_parent_read_wall_now()
            deadline = started + self.lease_seconds
            if not math.isfinite(deadline):
                return self._failure('UNAVAILABLE')
            with self._lock:
                self._ensure_open()
                prior = self._db.execute(
                    'SELECT registration_id FROM sessions WHERE principal=? AND registration_key=? AND service_epoch=?',
                    (principal, request.payload.get('registration_key'), self._epoch)).fetchone()
                known = self._db.execute('SELECT 1 FROM registration_keys WHERE principal=? AND registration_key=?',
                    (principal, request.payload.get('registration_key'))).fetchone()
                if known is not None and prior is None:
                    return self._failure('NOT_REGISTERED')
                if prior is not None:
                    prior_row = self._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                                 (prior['registration_id'],)).fetchone()
                    if (prior_row is None
                            or (has_write and self._execution_parent_live_lease(prior_row) is None)
                            or (has_read and self._execution_parent_live_read_lease(prior_row) is None)):
                        return self._failure('NOT_REGISTERED')
                try:
                    result = self._register_impl(principal, request)
                except BaseException:
                    if prior is not None:
                        self._execution_parent_monotonic_leases.pop(prior['registration_id'], None)
                        self._execution_parent_read_leases.pop(prior['registration_id'], None)
                    raise
                if not result.get('ok'):
                    return result
                if has_read:
                    self._execution_parent_read_wall_now()
                registration_id = result['result']['registration_id']
                row = self._db.execute('SELECT * FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()
                if row is None or row['port'] != _EXECUTION_PARENT_PORT:
                    self._execution_parent_monotonic_leases.pop(registration_id, None)
                    self._execution_parent_read_leases.pop(registration_id, None)
                    return self._failure('UNAVAILABLE')
                if prior is not None and prior['registration_id'] == registration_id:
                    if ((has_write and self._execution_parent_live_lease(row) is None)
                            or (has_read and self._execution_parent_live_read_lease(row) is None)):
                        return self._failure('NOT_REGISTERED')
                else:
                    write_policy = self._execution_parent_lease_policy(row) if has_write else (None, None)
                    read_policy = self._execution_parent_read_policy(row) if has_read else (None, None)
                    if ((has_write and write_policy[0] is None) or (has_read and read_policy[0] is None)
                            or self._execution_parent_mono_now() >= deadline):
                        return self._failure('NOT_REGISTERED')
                    facts = (self, self._execution_action_pid, self._epoch,
                             self._execution_release_session_identity(row), row['expires_at'], deadline)
                    if has_write:
                        self._execution_parent_monotonic_leases[registration_id] = facts + write_policy
                    if has_read:
                        self._execution_parent_read_leases[registration_id] = facts + read_policy + (
                            self._grants.get(row['principal']), self._capabilities)
                return result
        except (ServiceDispatchAdmissionError, sqlite3.Error, KeyError, TypeError, ValueError, RuntimeError):
            return self._failure('UNAVAILABLE')

    def _register_impl(self, principal: str, request: WireEnvelope) -> Mapping[str, Any]:
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
        transient_read_registration = (
            not (granted & capability.write_operations)
            and all((port, operation) in _TRANSIENT_READ_REGISTRATIONS for operation in granted)
        )
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
            scope_accounts = ((request.header.scope.account_ref,) if request.header.scope.account_ref is not None
                              else request.header.scope.account_refs)
            protected_strategy_write = (request.header.identity.role is WireRole.STRATEGY_CLIENT
                and bool(granted & capability.write_operations)
                and any(self._db.execute(
                    'SELECT 1 FROM execution_subject_protected_accounts WHERE account_ref=?', (account,)
                ).fetchone() is not None for account in scope_accounts))
            if (port == _EXECUTION_PARENT_PORT
                    or requested & (_GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_PARENT_OPERATIONS)
                    or protected_strategy_write):
                status, current_capability, current_granted = self._authorized_resource(
                    principal, request.header.identity, instance_id=instance_id, port=port,
                    scope_ref=scope_ref, scope=request.header.scope, requested_operations=requested)
                if status != 'OK' or current_capability != capability or current_granted != granted:
                    return self._failure('SCOPE_DENIED')
            if protected_strategy_write:
                selected_subject_scope = any(self._db.execute(
                    'SELECT 1 FROM execution_subject_aliases WHERE execution_domain_ref=? AND strategy_ref=? '
                    'AND account_ref=? AND canonical_account_key IS NOT NULL',
                    (request.header.scope.execution_domain_ref, request.header.scope.strategy_ref, account)
                ).fetchone() is not None for account in scope_accounts)
                if selected_subject_scope and any(
                        not callable(self._handlers.get((instance_id, operation)))
                        for operation in granted & capability.write_operations):
                    return self._failure('CAPABILITY_UNAVAILABLE')
            previous = self._db.execute(
                "SELECT request_digest, registration_id FROM registration_keys "
                "WHERE principal=? AND registration_key=?",
                (principal, registration_key),
            ).fetchone()
            if previous is not None and previous["request_digest"] != digest:
                return self._failure("CONFLICT")
            active = self._db.execute(
                "SELECT * FROM sessions WHERE principal=? AND registration_key=? AND service_epoch=?",
                (principal, registration_key, self._epoch),
            ).fetchone()
            if active is not None and active["expires_at"] > now:
                if active["request_digest"] != digest:
                    return self._failure("CONFLICT")
                if (request.header.identity.role is WireRole.STRATEGY_CLIENT
                        and granted & capability.write_operations
                        and not self._strategy_claim_exists(request.header.scope, active['registration_id'])):
                    return self._failure('POLICY_REQUIREMENT_UNMET')
                if (
                    active["policy_version"] != self.policy_version
                    or active["requirements_version"] != self.requirements_version
                    or not self._registration_policy_matches(active)
                ):
                    return self._failure("POLICY_REQUIREMENT_UNMET")
                return self._success(self._registration_result(active, now=now))
            if previous is None and not transient_read_registration:
                key_count = self._db.execute("SELECT COUNT(*) FROM registration_keys").fetchone()[0]
                if key_count >= self.max_registration_keys:
                    return self._failure("CAPACITY")
            active_count = self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            if active_count >= self.max_sessions:
                return self._failure("CAPACITY")
            registration_id = uuid.uuid4().hex
            write_ops = granted & capability.write_operations
            if request.header.identity.role is WireRole.STRATEGY_CLIENT:
                if write_ops:
                    claim_error = self._claim_strategy_write(
                        request.header.scope,
                        now,
                        registration_id,
                        write_ops,
                        principal=principal,
                        identity=request.header.identity,
                        probe_only=True,
                    )
                    if claim_error is not None:
                        if isinstance(claim_error, tuple):
                            return self._failure_with_result(claim_error[0], claim_error[1])
                        return self._failure(claim_error)

            if not transient_read_registration:
                try:
                    self._record_migration_write_attempt(fact_kind="ownership")
                except MigrationGateError as error:
                    return self._failure(_MIGRATION_WRITE_DISABLED if error.code == _MIGRATION_WRITE_DISABLED else "UNAVAILABLE")
                except OSError:
                    return self._failure("UNAVAILABLE")
            if request.header.identity.role is WireRole.STRATEGY_CLIENT and write_ops:
                claim_error = self._claim_strategy_write(request.header.scope, now, registration_id, write_ops,
                                                         principal=principal, identity=request.header.identity)
                if claim_error is not None:
                    raise RuntimeError('strategy claim changed within registration transaction')

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
            if not transient_read_registration:
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

    def _execution_subject_claim(self, scope, principal, identity, accounts, write_operations, *, probe_only,
                                 active_registration_id=None):
        """Check configured ownership before revealing any durable run."""
        selected = set()
        for account in accounts:
            protected = self._db.execute('SELECT 1 FROM execution_subject_protected_accounts WHERE account_ref=?',
                                         (account,)).fetchone()
            alias = (scope.execution_domain_ref, scope.strategy_ref, account)
            classified = self._db.execute(
                'SELECT canonical_account_key,canonical_strategy_id FROM execution_subject_aliases '
                'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?', alias).fetchone()
            existing = self._db.execute(
                'SELECT canonical_account_key,canonical_strategy_id,quarantined FROM strategy_canonical_bindings '
                'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?', alias).fetchone()
            if protected:
                all_classes = self._db.execute(
                    'SELECT canonical_account_key,canonical_strategy_id FROM execution_subject_aliases '
                    'WHERE account_ref=? AND strategy_ref=?', (account, scope.strategy_ref)).fetchall()
                if len({tuple(row) for row in all_classes}) > 1:
                    return 'POLICY_REQUIREMENT_UNMET'
            if protected and classified is None:
                return 'POLICY_REQUIREMENT_UNMET'
            if classified is None or classified[0] is None:
                if existing is not None and self._db.execute(
                        'SELECT 1 FROM execution_subject_aliases WHERE canonical_account_key=? '
                        'AND canonical_strategy_id=? LIMIT 1', (existing[0], existing[1])).fetchone():
                    return 'POLICY_REQUIREMENT_UNMET'
                continue
            canonical = (classified[0], classified[1])
            if existing is not None and (existing['quarantined'] or tuple(existing[:2]) != canonical):
                return 'POLICY_REQUIREMENT_UNMET'
            selected.add(canonical)
        if not selected:
            return None
        if (len(selected) != 1 or len(accounts) != 1 or type(identity) is not WireIdentity
                or identity.role is not WireRole.STRATEGY_CLIENT or identity.delegate_ref is not None
                or identity.actor_ref != scope.strategy_ref or not self._identity_is_trusted(principal, identity)):
            return 'SCOPE_DENIED'
        canonical = next(iter(selected))
        if self._db.execute('SELECT 1 FROM execution_subject_unknown_history '
                            'WHERE canonical_account_key=? AND canonical_strategy_id=?',
                            canonical).fetchone() is not None:
            return 'POLICY_REQUIREMENT_UNMET'
        # Inspect all historical domains, including those absent from the
        # trusted classification, before considering a first pin or a hint.
        if self._db.execute(
                'SELECT 1 FROM strategy_write_claims c JOIN execution_subject_aliases target '
                'ON target.account_ref=c.account_ref AND target.strategy_ref=c.strategy_ref '
                'AND target.canonical_account_key=? AND target.canonical_strategy_id=? '
                'LEFT JOIN execution_subject_aliases a '
                'ON a.execution_domain_ref=c.execution_domain_ref AND a.strategy_ref=c.strategy_ref '
                'AND a.account_ref=c.account_ref WHERE '
                '(a.canonical_account_key IS NULL OR a.canonical_account_key!=? '
                'OR a.canonical_strategy_id!=?) LIMIT 1',
                (*canonical, *canonical)).fetchone() is not None:
            return 'POLICY_REQUIREMENT_UNMET'
        # No canonical alias may be omitted from trusted classification.
        unknown = self._db.execute(
            'SELECT 1 FROM strategy_canonical_bindings b LEFT JOIN execution_subject_aliases a '
            'ON a.execution_domain_ref=b.execution_domain_ref AND a.strategy_ref=b.strategy_ref '
            'AND a.account_ref=b.account_ref WHERE b.canonical_account_key=? AND b.canonical_strategy_id=? '
            'AND (b.quarantined!=0 OR a.canonical_account_key IS NULL OR '
            'a.canonical_account_key!=b.canonical_account_key OR a.canonical_strategy_id!=b.canonical_strategy_id) '
            'LIMIT 1', canonical).fetchone()
        if unknown:
            return 'POLICY_REQUIREMENT_UNMET'
        claims = self._db.execute(
            'SELECT c.run_id,c.active_registration_id,c.freeze_command_id FROM execution_subject_aliases a '
            'JOIN strategy_write_claims c ON c.execution_domain_ref=a.execution_domain_ref '
            'AND c.strategy_ref=a.strategy_ref AND c.account_ref=a.account_ref '
            'WHERE a.canonical_account_key=? AND a.canonical_strategy_id=?', canonical).fetchall()
        pin = self._db.execute('SELECT principal,identity_json,run_id FROM execution_subject_pins '
                               'WHERE canonical_account_key=? AND canonical_strategy_id=?', canonical).fetchone()
        identity_json = json.dumps(self._identity_dict(identity), sort_keys=True, separators=(',', ':'))
        if pin is None:
            if claims:
                return 'POLICY_REQUIREMENT_UNMET'
            if not probe_only:
                self._db.execute('INSERT INTO execution_subject_pins VALUES(?,?,?,?,?)',
                                 (*canonical, principal, identity_json, scope.run_id))
            return None
        if pin['principal'] != principal or pin['identity_json'] != identity_json:
            return 'SCOPE_DENIED'
        if not claims or not _text(pin['run_id']) or any(row['run_id'] != pin['run_id'] for row in claims):
            return 'POLICY_REQUIREMENT_UNMET'
        if (scope.run_id == pin['run_id'] and not write_operations.isdisjoint(_FREEZE_OPERATIONS)
                and any(row['freeze_command_id'] is not None for row in claims)):
            return 'STRATEGY_STOPPED'
        if any(row['active_registration_id'] is not None
               and row['active_registration_id'] != active_registration_id for row in claims):
            return 'DUPLICATE_ACTIVE_RUN'
        if scope.run_id != pin['run_id']:
            return ('DUPLICATE_ACTIVE_RUN', {'strategy_write_run_id': pin['run_id']})
        return None

    def _claim_strategy_write(
        self,
        scope: WireScope,
        now: float,
        registration_id: str,
        write_operations: frozenset[str],
        *,
        principal: str,
        identity: WireIdentity,
        probe_only: bool = False,
    ) -> str | tuple[str, Mapping[str, Any]] | None:
        if scope.strategy_ref is None or scope.run_id is None:
            return "SCOPE_DENIED"
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return "SCOPE_DENIED"
        subject_error = self._execution_subject_claim(scope, principal, identity, accounts, write_operations,
                                                       probe_only=probe_only)
        if subject_error is not None:
            return subject_error
        canonical_rows = tuple(self._db.execute(
            'SELECT canonical_account_key, canonical_strategy_id FROM strategy_canonical_bindings '
            'WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?',
            (scope.execution_domain_ref, scope.strategy_ref, account_ref)).fetchone()
            for account_ref in accounts)
        if any(row is not None for row in canonical_rows):
            if any(row is None for row in canonical_rows):
                return 'POLICY_REQUIREMENT_UNMET'
            for canonical in canonical_rows:
                aliases = self._db.execute(
                    'SELECT b.quarantined, c.run_id, c.active_registration_id, c.freeze_command_id '
                    'FROM strategy_canonical_bindings b LEFT JOIN strategy_write_claims c '
                    'ON c.execution_domain_ref=b.execution_domain_ref AND c.strategy_ref=b.strategy_ref '
                    'AND c.account_ref=b.account_ref WHERE b.canonical_account_key=? '
                    'AND b.canonical_strategy_id=?',
                    (canonical['canonical_account_key'], canonical['canonical_strategy_id'])).fetchall()
                if any(row['quarantined'] for row in aliases):
                    return 'POLICY_REQUIREMENT_UNMET'
                if (not write_operations.isdisjoint(_FREEZE_OPERATIONS)
                        and any(row['run_id'] == scope.run_id and row['freeze_command_id'] is not None
                                for row in aliases)):
                    return 'STRATEGY_STOPPED'
                if any(row['active_registration_id'] is not None for row in aliases):
                    return 'DUPLICATE_ACTIVE_RUN'
                runs = {row['run_id'] for row in aliases if row['run_id'] is not None}
                if len(runs) > 1:
                    return 'DUPLICATE_ACTIVE_RUN'
                if runs and scope.run_id not in runs:
                    return ('DUPLICATE_ACTIVE_RUN', {'strategy_write_run_id': next(iter(runs))})
        rows = tuple(
            self._db.execute(
                "SELECT run_id, active_registration_id, freeze_command_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, account_ref),
            ).fetchone()
            for account_ref in accounts
        )
        if any(
            row is not None
            and row["run_id"] == scope.run_id
            and row["freeze_command_id"] is not None
            and not write_operations.isdisjoint(_FREEZE_OPERATIONS)
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
        if probe_only:
            return None
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

    def freeze_strategy(self, scope: WireScope, command_id: str) -> Mapping[str, Any]:
        """Persist one exact run FREEZE fence and return its durable owner receipt.

        The fence refuses every submit-type operation of the run, closes
        included (``_FREEZE_OPERATIONS``); cancels stay available. This method
        is an in-process owner hook. It cannot select an account, strategy, or
        run by lookup: callers must supply one fully bound strategy scope, and
        the registry only fences a matching durable write claim.

        Persisted receipt values keep their historical form: ``effect_ref``
        starts with ``strategy-stop-new-v1:`` (Monitor parses it) and
        ``effect_result.action`` is ``"stop_new"``.
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
            raise ServiceRegistryFreezeError("INVALID_SCOPE_OR_COMMAND")
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
                "SELECT * FROM strategy_freeze_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["scope_digest"] != scope_digest:
                    raise ServiceRegistryFreezeError("COMMAND_SCOPE_CONFLICT")
                return self._freeze_receipt_from_row(previous)
            claim = self._db.execute(
                "SELECT run_id, freeze_at, freeze_command_id "
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
            if (claim["freeze_at"] is None) != (claim["freeze_command_id"] is None):
                raise ServiceRegistryFreezeError("FENCE_STATE_INVALID")
            count = int(
                self._db.execute(
                    "SELECT COUNT(*) FROM strategy_freeze_receipts"
                ).fetchone()[0]
            )
            if count >= _MAX_FREEZE_RECEIPTS:
                raise ServiceRegistryFreezeError("RECEIPT_CAPACITY")

            # This request has passed the exact active-run check. Seal MIG03
            # immediately before the first durable owner mutation, within the
            # same Registry transaction that will publish the fence/receipt.
            try:
                self._record_migration_write_attempt(fact_kind="ownership")
            except MigrationGateError as error:
                if error.code == _MIGRATION_WRITE_DISABLED:
                    raise ServiceRegistryFreezeError(_MIGRATION_WRITE_DISABLED) from None
                raise ServiceRegistryFreezeError("MIGRATION_BOUNDARY_UNAVAILABLE") from None
            except OSError:
                raise ServiceRegistryFreezeError("MIGRATION_BOUNDARY_UNAVAILABLE") from None

            if claim["freeze_command_id"] is None:
                updated = self._db.execute(
                    "UPDATE strategy_write_claims SET freeze_at=?, freeze_command_id=? "
                    "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=? "
                    "AND run_id=? AND freeze_command_id IS NULL",
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
                    raise ServiceRegistryFreezeError("FENCE_STATE_CHANGED")
                effect_result = {"action": "stop_new", "result": "fenced"}
            else:
                effect_result = {"action": "stop_new", "result": "already_fenced"}

            self._db.execute(
                "INSERT INTO strategy_freeze_receipts "
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
                "SELECT * FROM strategy_freeze_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                raise ServiceRegistryFreezeError("RECEIPT_UNAVAILABLE")
            return self._freeze_receipt_from_row(row)

    @staticmethod
    def _freeze_receipt_from_row(row: sqlite3.Row) -> Mapping[str, Any]:
        try:
            result = json.loads(row["effect_result_json"])
        except (TypeError, json.JSONDecodeError):
            raise ServiceRegistryFreezeError("RECEIPT_INVALID") from None
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
            raise ServiceRegistryFreezeError("RECEIPT_INVALID")
        return MappingProxyType(
            {
                "command_id": str(row["command_id"]),
                "status": str(row["status"]),
                "effect_ref": row["effect_ref"],
                "effect_result": MappingProxyType(result),
            }
        )

    def _strategy_write_is_frozen(self, scope: WireScope) -> bool:
        if (
            scope.strategy_ref is None
            or scope.run_id is None
            or (scope.account_ref is None and not scope.account_refs)
        ):
            return False
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        for account_ref in accounts:
            if self._db.execute(
                'SELECT 1 FROM strategy_canonical_bindings current '
                'JOIN strategy_canonical_bindings other ON '
                'other.canonical_account_key=current.canonical_account_key AND '
                'other.canonical_strategy_id=current.canonical_strategy_id '
                'JOIN strategy_write_claims claim ON '
                'claim.execution_domain_ref=other.execution_domain_ref AND '
                'claim.strategy_ref=other.strategy_ref AND claim.account_ref=other.account_ref '
                'WHERE current.execution_domain_ref=? AND current.strategy_ref=? AND current.account_ref=? '
                'AND claim.run_id=? AND claim.freeze_command_id IS NOT NULL LIMIT 1',
                (scope.execution_domain_ref, scope.strategy_ref, account_ref, scope.run_id)).fetchone():
                return True
        return any(
            row is not None
            and row["run_id"] == scope.run_id
            and row["freeze_command_id"] is not None
            for row in (
                self._db.execute(
                    "SELECT run_id, freeze_command_id FROM strategy_write_claims "
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

    def _acknowledge_retired_gateway_session(
        self, principal: str, request: WireEnvelope, registration_id: str, service_epoch: str
    ) -> Mapping[str, Any]:
        """Acknowledge only an exact session retired by this Registry's restart."""
        with self._read_snapshot():
            row = self._db.execute(
                """
                SELECT identity_json, scope_json FROM gateway_retired_sessions
                WHERE registration_id=? AND service_epoch=? AND successor_epoch=? AND principal=?
                """,
                (registration_id, service_epoch, self._epoch, principal),
            ).fetchone()
            if row is None:
                return self._failure("STALE_EPOCH")
            identity = request.header.identity
            if identity != self._stored_identity(row["identity_json"]) or not self._identity_is_trusted(
                principal, identity
            ):
                return self._failure("DENIED")
            if request.header.scope != self._stored_scope(row["scope_json"]):
                return self._failure("SCOPE_DENIED")
            return self._success({"unregistered": True, "registration_id": registration_id})

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
            if not renew and self._gateway_retired_session_ack_enabled:
                return self._acknowledge_retired_gateway_session(
                    principal, request, payload["registration_id"], payload["service_epoch"]
                )
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

    def _renew(self, principal: str, request: WireEnvelope, *, parent_started=None) -> Mapping[str, Any]:
        payload = self._payload_fields(request, frozenset({'registration_id', 'service_epoch'}))
        if payload is None or not _text(payload.get('registration_id')):
            return self._renew_or_unregister(principal, request, renew=True)
        try:
            with self._lock:
                self._ensure_open()
                row = self._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                       (payload['registration_id'],)).fetchone()
                if row is not None and row['port'] == _EXECUTION_PARENT_PORT:
                    try:
                        parent_operations = frozenset(json.loads(row['operations_json']))
                    except (TypeError, ValueError):
                        return self._failure('NOT_REGISTERED')
                    if (_EXECUTION_PARENT_SUBMIT in parent_operations
                            or (_EXECUTION_PARENT_QUERY in parent_operations
                                and row['instance_id'] in self._execution_parent_read_instances)):
                        return self._renew_execution_parent(principal, request, row, parent_started)
                if row is None or row['port'] != _EXECUTION_RELEASE_PORT:
                    return self._renew_or_unregister(principal, request, renew=True)
                started = self._execution_release_mono_now()
                old = self._execution_release_live_lease(row)
                deadline = started + self.lease_seconds
                if old is None or not math.isfinite(deadline):
                    return self._failure('NOT_REGISTERED')
                result = self._renew_or_unregister(principal, request, renew=True)
                if not result.get('ok'):
                    return result
                updated = self._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                           (payload['registration_id'],)).fetchone()
                # The existing deadline must survive through commit: a slow
                # renewal cannot revive an already expired registration.
                now = self._execution_release_mono_now()
                if (updated is None or self._execution_release_monotonic_leases.get(row['registration_id']) is not old
                        or self._execution_release_session_identity(updated) != old[4]
                        or now >= old[3] or now >= deadline):
                    return self._failure('NOT_REGISTERED')
                self._execution_release_monotonic_leases[row['registration_id']] = (
                    self._epoch, self._execution_action_pid, updated['expires_at'], deadline, old[4])
                return result
        except (ServiceDispatchAdmissionError, sqlite3.Error, KeyError, TypeError, ValueError):
            return self._failure('UNAVAILABLE')

    def _renew_execution_parent(self, principal, request, row, started):
        registration_id = row['registration_id']
        # A forged renewal must not revoke another principal's live token.
        if (row['principal'] != principal or request.header.identity != self._stored_identity(row['identity_json'])
                or request.header.scope != self._stored_scope(row['scope_json'])):
            return self._failure('DENIED')
        try:
            operations = frozenset(json.loads(row['operations_json']))
            has_write = _EXECUTION_PARENT_SUBMIT in operations
            has_read = (_EXECUTION_PARENT_QUERY in operations
                        and row['instance_id'] in self._execution_parent_read_instances)
            old = self._execution_parent_live_lease(row) if has_write else None
            old_read = self._execution_parent_live_read_lease(row) if has_read else None
            if started is None or (has_write and old is None) or (has_read and old_read is None):
                return self._failure('NOT_REGISTERED')
            deadline = started + self.lease_seconds
            if not math.isfinite(deadline):
                return self._failure('UNAVAILABLE')
            try:
                result = self._renew_or_unregister(principal, request, renew=True)
            except BaseException:
                self._execution_parent_monotonic_leases.pop(registration_id, None)
                self._execution_parent_read_leases.pop(registration_id, None)
                raise
            if not result.get('ok'):
                return result
            updated = self._db.execute('SELECT * FROM sessions WHERE registration_id=?', (registration_id,)).fetchone()
            now = self._execution_parent_mono_now()
            if has_read:
                self._execution_parent_read_wall_now()
            write_policy = (None, None) if updated is None or not has_write else self._execution_parent_lease_policy(updated)
            read_policy = (None, None) if updated is None or not has_read else self._execution_parent_read_policy(updated)
            if (updated is None or now >= deadline
                    or (has_write and (self._execution_parent_monotonic_leases.get(registration_id) is not old
                        or self._execution_release_session_identity(updated) != old[3] or now >= old[5]
                        or write_policy[0] is not old[6] or write_policy[1] != old[7]))
                    or (has_read and (self._execution_parent_read_leases.get(registration_id) is not old_read
                        or self._execution_release_session_identity(updated) != old_read[3] or now >= old_read[5]
                        or read_policy[0] is not old_read[6] or read_policy[1] != old_read[7]
                        or self._grants.get(principal) is not old_read[8] or self._capabilities is not old_read[9]
                        or not self._identity_is_trusted(principal, self._stored_identity(updated['identity_json']))
                        or not self._current_resource_allows(principal, self._stored_identity(updated['identity_json']),
                            instance_id=updated['instance_id'], port=updated['port'], scope_ref=updated['scope_ref'],
                            scope=self._stored_scope(updated['scope_json']), operation=_EXECUTION_PARENT_QUERY)))):
                self._execution_parent_monotonic_leases.pop(registration_id, None)
                self._execution_parent_read_leases.pop(registration_id, None)
                return self._failure('NOT_REGISTERED')
            facts = (self, self._execution_action_pid, self._epoch,
                     self._execution_release_session_identity(updated), updated['expires_at'], deadline)
            if has_write:
                self._execution_parent_monotonic_leases[registration_id] = facts + (old[6], old[7])
            if has_read:
                self._execution_parent_read_leases[registration_id] = facts + (old_read[6], old_read[7], old_read[8], old_read[9])
            return result
        except (ServiceDispatchAdmissionError, sqlite3.Error, KeyError, TypeError, ValueError):
            self._execution_parent_monotonic_leases.pop(registration_id, None)
            self._execution_parent_read_leases.pop(registration_id, None)
            return self._failure('UNAVAILABLE')

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
        if self._parent_retry_unready:
            return False

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
        with self._lock:
            result = self._renew_or_unregister(principal, request, renew=False)
            if result.get('ok'):
                self._execution_release_monotonic_leases.pop(request.payload.get('registration_id'), None)
                self._execution_parent_monotonic_leases.pop(request.payload.get('registration_id'), None)
                self._execution_parent_read_leases.pop(request.payload.get('registration_id'), None)
            return result

    def _risk_admin_cancel_admission_error(self, binding: _RiskAdminCancelBinding) -> str | None:
        """Recheck an exact admin registration under the caller's Registry snapshot.

        Registry grants cover the registered account and operation. They do
        not establish Risk's separate operator-to-target scope authorization.
        """
        if not self._risk_admin_cancel_admission_enabled:
            return "CAPABILITY_UNAVAILABLE"
        if self._closed or self._db is None:
            return "CLOSED"
        if binding.service_epoch != self._epoch:
            return "STALE_EPOCH"
        row = self._db.execute(
            "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
            (binding.registration_id, binding.principal, binding.service_epoch),
        ).fetchone()
        if (row is None or type(row["expires_at"]) not in (int, float)
                or row["expires_at"] <= self._now()):
            return "NOT_REGISTERED"
        identity, scope = binding.identity, binding.scope
        if (row["instance_id"] != binding.instance_id or row["port"] != "service.risk_admin"
                or row["scope_ref"] != binding.scope_ref
                or row["registration_key"] != binding.registration_key
                or row["request_digest"] != binding.registration_digest
                or self._stored_identity(row["identity_json"]) != identity
                or self._stored_scope(row["scope_json"]) != scope):
            return "SCOPE_DENIED"
        if (not _risk_admin_write_scope(identity, scope)
                or not self._identity_is_trusted(binding.principal, identity)):
            return "DENIED"
        if (not self._ready or row["policy_version"] != self.policy_version
                or row["requirements_version"] != self.requirements_version
                or not self._registration_policy_matches(row)):
            return "POLICY_REQUIREMENT_UNMET"
        capability = self._capabilities.get((binding.instance_id, "service.risk_admin"))
        if (capability is None or _RISK_ADMIN_CANCEL not in capability.operations
                or _RISK_ADMIN_CANCEL not in frozenset(json.loads(row["operations_json"]))
                or (binding.instance_id, _RISK_ADMIN_CANCEL) not in self._handlers):
            return "CAPABILITY_UNAVAILABLE"
        if not self._current_resource_allows(
            binding.principal, identity, instance_id=binding.instance_id,
            port="service.risk_admin", scope_ref=binding.scope_ref,
            scope=scope, operation=_RISK_ADMIN_CANCEL,
        ):
            return "SCOPE_DENIED"
        return None

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
        """Recheck the exact registered operation under a Registry snapshot."""
        if type(operation) is not str or operation not in ({"direct.reserve", "direct.dispatch", "direct.cancel", _GATEWAY_ACCEPTANCE_OPERATION} | _RISK_V2_ADMISSION_OPERATIONS | _GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS):
            return "CAPABILITY_UNAVAILABLE"
        if operation in _RISK_V2_ADMISSION_OPERATIONS and not self._risk_v2_dispatch_admission_enabled:
            return "CAPABILITY_UNAVAILABLE"
        if (operation == _GATEWAY_RISK_FIRST_RESERVE_V2
                and not self._gateway_risk_first_reserve_admission_enabled):
            return "CAPABILITY_UNAVAILABLE"
        if self._closed or self._db is None:
            return "CLOSED"
        if service_epoch != self._epoch:
            return "STALE_EPOCH"
        row = self._db.execute(
            "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
            (registration_id, principal, service_epoch),
        ).fetchone()
        margin = self._dispatch_lease_margin_seconds if operation in {"direct.dispatch", _GATEWAY_RISK_FIRST_RESERVE_V2, *_RISK_V2_ADMISSION_OPERATIONS} else 0.0
        if (row is None or type(row["expires_at"]) not in (int, float)
                or row["expires_at"] <= self._now() + margin):
            return "NOT_REGISTERED"
        observer = operation in _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS
        expected_port = ("service.risk_order" if operation in _RISK_V2_ADMISSION_OPERATIONS else
                         "service.account_read" if observer or operation in _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS
                         else "service.direct_write")
        if row["instance_id"] != instance_id or row["port"] != expected_port:
            return "DENIED"
        if self._stored_identity(row["identity_json"]) != identity:
            return "DENIED"
        if self._stored_scope(row["scope_json"]) != scope:
            return "SCOPE_DENIED"
        if operation in _RISK_V2_ADMISSION_OPERATIONS:
            strategy_v2_ok = (
                identity.role is WireRole.STRATEGY_CLIENT
                and identity.actor_ref == scope.strategy_ref
            )
            execution_v2_ok = (
                self._risk_parent_execution_writes_enabled
                and identity.role is WireRole.SERVICE
            )
            if (
                not (strategy_v2_ok or execution_v2_ok)
                or identity.delegate_ref is not None
                or scope.account_ref is None
                or scope.account_refs
                or scope.strategy_ref is None
                or scope.run_id is None
                or scope.manual_book_ref is not None
                or scope.portfolio_ref is not None
                or scope.group_id is not None
                or scope.leg_id is not None
            ):
                return "SCOPE_DENIED"
        if observer and not self._account_observer_registration_unique(instance_id=instance_id,
                principal=principal, identity=identity, scope=scope, registration_id=registration_id, operation=operation):
            return 'SCOPE_DENIED'
        expected_role = (WireRole.APPLICATION_CLIENT if operation in (_GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS) else
                         WireRole.SERVICE if operation in _GATEWAY_ALL_DELEGATE_OPERATIONS else WireRole.STRATEGY_CLIENT)
        allowed_roles = ({WireRole.SERVICE} if operation == _GATEWAY_ACCEPTANCE_OPERATION or operation in _GATEWAY_ORDER_CUT_OPERATIONS else
                         {WireRole.STRATEGY_CLIENT, WireRole.SERVICE, WireRole.APPLICATION_CLIENT} if observer else
                         {WireRole.STRATEGY_CLIENT, WireRole.SERVICE} if (operation in _GATEWAY_PARENT_OPERATIONS or (operation in _RISK_V2_ADMISSION_OPERATIONS and self._risk_parent_execution_writes_enabled)) else {expected_role})
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
            or (operation not in ({_GATEWAY_ACCEPTANCE_OPERATION} | _GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS) and operation not in capability.write_operations)
            or operation not in frozenset(json.loads(row["operations_json"]))
            or (operation not in ({_GATEWAY_ACCEPTANCE_OPERATION} | _GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_PARENT_READ_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS) and operation not in frozenset(json.loads(row["write_operations_json"])))
            or (operation == _GATEWAY_ACCEPTANCE_OPERATION and operation in capability.write_operations)
            or (instance_id, operation) not in self._handlers
        ):
            return "CAPABILITY_UNAVAILABLE"
        if not self._current_resource_allows(
            principal, identity, instance_id=instance_id, port=row["port"],
            scope_ref=row["scope_ref"], scope=scope, operation=operation,
        ):
            return "SCOPE_DENIED"
        if operation == _GATEWAY_ACCEPTANCE_OPERATION:
            if self.gateway_parent_acceptance_grant(instance_id=instance_id, scope_ref=row['scope_ref'],
                    identity=identity, scope=scope) is None:
                return 'SCOPE_DENIED'
        elif operation in _GATEWAY_PARENT_OPERATIONS:
            if (self.gateway_parent_grant(instance_id=instance_id,scope_ref=row['scope_ref'],identity=identity,
                                          scope=scope,operation=operation) is None
                    or not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id)):
                return 'SCOPE_DENIED'
        elif operation in _GATEWAY_ALL_DELEGATE_OPERATIONS:
            if self.gateway_delegate_grant(instance_id=instance_id,scope_ref=row['scope_ref'],identity=identity,
                                           scope=scope,operation=operation) is None:
                return 'SCOPE_DENIED'
            if not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id):
                return "DUPLICATE_ACTIVE_RUN"
        elif (
            self._risk_parent_execution_writes_enabled
            and identity.role is WireRole.SERVICE
            and operation in _RISK_V2_ADMISSION_OPERATIONS
        ):
            pass
        elif operation not in (_GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS) and not self._strategy_claim_exists(scope, registration_id):
            return "DUPLICATE_ACTIVE_RUN"
        if (operation == _GATEWAY_RISK_FIRST_RESERVE_V2
                and self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gateway_lease_expirations'").fetchone()
                and self._db.execute("SELECT 1 FROM gateway_lease_expirations WHERE instance_id=? AND account_ref=? AND strategy_ref=? AND state='PENDING' LIMIT 1",
                                     (instance_id, scope.account_ref, scope.strategy_ref)).fetchone()):
            return "STRATEGY_STOPPED"
        if operation in _FREEZE_OPERATIONS and self._strategy_write_is_frozen(scope):
            return "STRATEGY_STOPPED"
        return None

    def _execution_release_admission_error(self, registration_id, service_epoch, instance_id,
                                           principal, identity, scope, operation):
        """Check only the dedicated SERVICE registration; never infer strategy liveness."""
        if operation not in _EXECUTION_RELEASE_OPERATIONS:
            return 'CAPABILITY_UNAVAILABLE'
        if self._closed or self._db is None:
            return 'CLOSED'
        if service_epoch != self._epoch:
            return 'STALE_EPOCH'
        row = self._db.execute(
            'SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?',
            (registration_id, principal, service_epoch)).fetchone()
        if row is None or type(row['expires_at']) not in (int, float) or row['expires_at'] <= self._now():
            return 'NOT_REGISTERED'
        if self._execution_release_live_lease(row) is None:
            return 'NOT_REGISTERED'
        if (row['instance_id'] != instance_id or row['port'] != _EXECUTION_RELEASE_PORT
                or not _is_execution_release_scope(identity, row['port'], scope)
                or self._stored_identity(row['identity_json']) != identity
                or not self._identity_is_trusted(principal, identity)):
            return 'DENIED'
        if self._stored_scope(row['scope_json']) != scope:
            return 'SCOPE_DENIED'
        if (not self._ready or not self._registration_policy_matches(row)
                or row['policy_version'] != self.policy_version
                or row['requirements_version'] != self.requirements_version):
            return 'POLICY_REQUIREMENT_UNMET'
        capability = self._capabilities.get((instance_id, _EXECUTION_RELEASE_PORT))
        if (capability is None or not capability.operations <= _EXECUTION_RELEASE_OPERATIONS
                or capability.write_operations
                or operation not in capability.operations
                or operation not in frozenset(json.loads(row['operations_json']))
                or (instance_id, operation) not in self._handlers):
            return 'CAPABILITY_UNAVAILABLE'
        if (self._execution_release_policy(instance_id, identity, scope) is None
                or not self._current_resource_allows(principal, identity, instance_id=instance_id,
                    port=row['port'], scope_ref=row['scope_ref'], scope=scope, operation=operation)):
            return 'SCOPE_DENIED'
        return None

    def _execution_parent_admission_error(self, registration_id, service_epoch, instance_id,
                                          principal, identity, scope, operation, *,
                                          arguments=None, replay_proof=None):
        """Recheck the exact Execution parent registration under Registry lock.

        A registration write lease is pinned to the grant/generation it was
        issued under. When a re-authorization supersedes it, submit may enter
        the read-only replay path only through an exact proof from the
        composition-installed prover (``arguments`` for the first check, the
        already-issued ``replay_proof`` for later rechecks). Everything else
        still has to pass, and the current grant must still cover the request.
        """
        if operation not in _EXECUTION_PARENT_OPERATIONS:
            return 'CAPABILITY_UNAVAILABLE'
        if self._closed or self._db is None:
            return 'CLOSED'
        if service_epoch != self._epoch:
            return 'STALE_EPOCH'
        row = self._db.execute(
            'SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?',
            (registration_id, principal, service_epoch)).fetchone()
        if row is None or type(row['expires_at']) not in (int, float) or row['expires_at'] <= self._now():
            return 'NOT_REGISTERED'
        if row['instance_id'] != instance_id or row['port'] != _EXECUTION_PARENT_PORT:
            return 'DENIED'
        replay = None
        replay_stale = False
        lease_grant = lease_generation = None
        if operation == _EXECUTION_PARENT_SUBMIT:
            lease = self._execution_parent_monotonic_leases.get(registration_id)
            if type(lease) is tuple and len(lease) == 8:
                lease_grant, lease_generation = self._execution_parent_lease_policy(row)
                if lease_grant is not lease[6] or lease_generation != lease[7]:
                    replay_stale = True
                    if replay_proof is None:
                        if arguments is None:
                            return 'SCOPE_DENIED'
                        replay_proof = self._execution_parent_replay_for_dispatch(
                            row, principal, identity, scope, arguments)
                    if replay_proof is None:
                        return 'SCOPE_DENIED'
                    replay = replay_proof
                    if self._execution_parent_replay_lease(row) is None:
                        return 'NOT_REGISTERED'
            if not replay_stale:
                if replay_proof is not None:
                    return 'SCOPE_DENIED'
                if self._execution_parent_live_lease(row) is None:
                    return 'NOT_REGISTERED'
        elif (operation == _EXECUTION_PARENT_QUERY and instance_id in self._execution_parent_read_instances):
            if self._execution_parent_live_read_lease(row) is None:
                return 'NOT_REGISTERED'
        if self._stored_identity(row['identity_json']) != identity or not self._identity_is_trusted(principal, identity):
            return 'DENIED'
        if self._stored_scope(row['scope_json']) != scope:
            return 'SCOPE_DENIED'
        if not self._ready or row['policy_version'] != self.policy_version or row['requirements_version'] != self.requirements_version or not self._registration_policy_matches(row):
            return 'POLICY_REQUIREMENT_UNMET'
        capability = self._capabilities.get((instance_id, row['port']))
        if (capability is None or not capability.operations <= _EXECUTION_PARENT_OPERATIONS
                or operation not in capability.operations
                or operation not in frozenset(json.loads(row['operations_json']))
                or _EXECUTION_PARENT_QUERY in capability.write_operations
                or (_EXECUTION_PARENT_SUBMIT in capability.operations
                    and _EXECUTION_PARENT_SUBMIT not in capability.write_operations)
                or (operation == _EXECUTION_PARENT_SUBMIT
                    and operation not in frozenset(json.loads(row['write_operations_json'])))
                or (instance_id, operation) not in self._handlers):
            return 'CAPABILITY_UNAVAILABLE'
        if (not self._current_resource_allows(principal, identity, instance_id=instance_id,
                port=row['port'], scope_ref=row['scope_ref'], scope=scope, operation=operation)
                or self.execution_parent_grant(instance_id=instance_id, scope_ref=row['scope_ref'],
                    identity=identity, scope=scope, operation=operation) is None):
            return 'SCOPE_DENIED'
        if operation == _EXECUTION_PARENT_SUBMIT:
            if not self._strategy_claim_exists(scope, registration_id):
                return 'DUPLICATE_ACTIVE_RUN'
            if self._strategy_write_is_frozen(scope):
                return 'STRATEGY_STOPPED'
            if replay is not None:
                # Every other check above has passed. Bind the proof to this
                # exact registration and the CURRENT grant/generation, and to
                # the exact request bytes on the first check.
                if not self._execution_parent_replay_matches(
                        replay, registration_id=registration_id, service_epoch=service_epoch,
                        instance_id=instance_id, principal=principal, identity=identity,
                        scope=scope, row=row, grant=lease_grant,
                        generation=lease_generation):
                    return 'SCOPE_DENIED'
                if arguments is not None:
                    try:
                        request_bytes = encode_wire_value(arguments)
                    except Exception:
                        return 'SCOPE_DENIED'
                    if (replay.request_bytes != request_bytes
                            or replay.request_id != arguments.get('request_id')):
                        return 'SCOPE_DENIED'
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
        read_only_control = request.header.operation in _GATEWAY_CONTROL_PURE_READ_OPERATIONS
        read_only = read_only_market or read_only_control
        now = self._now()
        with (self._read_snapshot() if read_only else self._transaction()):
            if not read_only:
                self._expire_sessions(now)
            row = self._db.execute(
                "SELECT * FROM sessions WHERE registration_id=? AND principal=? AND service_epoch=?",
                (payload["registration_id"], principal, self._epoch),
            ).fetchone()
            if row is None:
                return self._failure("NOT_REGISTERED")
            if read_only and (
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
            if not self._ready and (self._parent_retry_unready or restricted_readiness is None):
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
            if read_only_control and (capability.port != "service.direct_write" or operation in write_ops):
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
            if capability.port == _EXECUTION_PARENT_PORT and operation == _EXECUTION_PARENT_SUBMIT:
                try:
                    from bt_api_base.market_protocol import MarketInstrumentKey
                    spec = payload['arguments']['spec']
                    instrument = MarketInstrumentKey.from_dict(spec['instrument'])
                    parent_grant = self.execution_parent_grant(instance_id=instance_id, scope_ref=row['scope_ref'],
                        identity=identity, scope=scope, operation=operation)
                    if (parent_grant is None or not parent_grant.allows_instrument(instrument)
                            or spec.get('account_scope') != parent_grant.account_ref
                            or spec.get('strategy_scope') != parent_grant.strategy_ref
                            or spec.get('execution_domain_ref') != parent_grant.execution_domain_ref):
                        return self._failure('SCOPE_DENIED')
                except Exception:
                    return self._failure('SCOPE_DENIED')
            if (capability.port == 'service.direct_write' and operation in {'direct.reserve', 'direct.dispatch', 'direct.delegated_reserve', 'direct.delegated_dispatch', _GATEWAY_RISK_FIRST_RESERVE_V2}
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
            if capability.port == _PRODUCT_PORT and operation in _PRODUCT_OPERATIONS and any(
                required not in granted or not self._current_resource_allows(
                    principal, identity, instance_id=instance_id, port=row["port"],
                    scope_ref=row["scope_ref"], scope=scope, operation=required)
                for required in _PRODUCT_OPERATIONS
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
                    if not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id):
                        return self._failure('DUPLICATE_ACTIVE_RUN')
                elif (capability.port == 'service.direct_write' and operation in _GATEWAY_ALL_DELEGATE_OPERATIONS
                        and self.gateway_delegate_grant(instance_id=instance_id,scope_ref=row['scope_ref'],
                            identity=identity,scope=scope,operation=operation) is not None):
                    if not self._gateway_delegate_strategy_run_current(scope, instance_id=instance_id):
                        return self._failure("DUPLICATE_ACTIVE_RUN")
                elif _is_exact_owner_cancel_scope(
                    identity, port=row["port"], scope=scope, operation=operation
                ) or _is_exact_owner_reduce_scope(
                    identity, port=row["port"], scope=scope, operation=operation
                ):
                    if not self._strategy_owner_run_claim_exists(scope):
                        return self._failure("DUPLICATE_ACTIVE_RUN")
                elif (
                    self._risk_parent_execution_writes_enabled
                    and capability.port == "service.risk_order"
                    and operation in _RISK_EXECUTION_PARENT_WRITE_OPERATIONS
                    and scope.account_ref is not None
                    and not scope.account_refs
                    and scope.strategy_ref is not None
                    and scope.run_id is not None
                    and identity.delegate_ref is None
                ):
                    pass
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
                    and operation in ({"direct.reserve", "direct.dispatch", "direct.cancel", _GATEWAY_ACCEPTANCE_OPERATION} | _GATEWAY_AUTHENTICATED_CONTROL_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_ALL_DELEGATE_OPERATIONS | _GATEWAY_STRATEGY_DELEGATED_CANCEL | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS))
                    or (capability.port == "service.account_read" and operation in (_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS))
                    or (capability.port == "service.risk_order" and operation in _RISK_V2_ADMISSION_OPERATIONS)
                    or (capability.port == _EXECUTION_PARENT_PORT and operation in _EXECUTION_PARENT_OPERATIONS)
                    or (capability.port == _EXECUTION_RELEASE_PORT and operation in _EXECUTION_RELEASE_OPERATIONS)):
                try:
                    checker = (self._execution_parent_admission_error if operation in _EXECUTION_PARENT_OPERATIONS
                               else self._execution_release_admission_error if operation in _EXECUTION_RELEASE_OPERATIONS
                               else self._direct_dispatch_admission_error)
                    if operation in _EXECUTION_PARENT_OPERATIONS:
                        # Only a stale write lease can obtain a proof; a
                        # still-current lease returns None so its behaviour is
                        # unchanged, and no prover means no proof at all.
                        execution_parent_replay = self._execution_parent_replay_for_dispatch(
                            row, principal, identity, scope, payload["arguments"])
                        admission_error = checker(
                            row["registration_id"], self._epoch, instance_id, principal, identity,
                            scope, operation, arguments=payload["arguments"],
                            replay_proof=execution_parent_replay,
                        )
                    else:
                        execution_parent_replay = None
                        admission_error = checker(
                            row["registration_id"], self._epoch, instance_id, principal, identity,
                            scope, operation,
                        )
                except Exception:
                    return self._failure("UNAVAILABLE")
                if (admission_error is not None and not
                        (operation == _EXECUTION_PARENT_SUBMIT and admission_error == 'STRATEGY_STOPPED')):
                    return self._failure(admission_error)
                admission = ServiceDispatchAdmission(
                    _DISPATCH_ADMISSION_ISSUER, self,
                    registration_id=row["registration_id"], service_epoch=self._epoch,
                    instance_id=instance_id, principal=principal, identity=identity, scope=scope,
                    arguments=payload["arguments"], operation=operation,
                    execution_parent_replay=execution_parent_replay,
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
                row['service_epoch'],
            )
            if admission is not None and operation in (_EXECUTION_PARENT_OPERATIONS | _EXECUTION_RELEASE_OPERATIONS):
                admission._execution_context = context
        def reject_before_handler(code):
            if capability.port in {_EXECUTION_PARENT_PORT, _EXECUTION_RELEASE_PORT} and admission is not None:
                admission.close()
                with self._lock:
                    self._dispatch_admissions.pop(admission, None)
            return self._failure(code)
        if restricted_readiness is not None:
            try:
                if restricted_readiness() is not True:
                    return reject_before_handler("POLICY_REQUIREMENT_UNMET")
            except Exception:
                return reject_before_handler("POLICY_REQUIREMENT_UNMET")
        elif not self._ready:
            # Preserve the global readiness fence if it changed after the
            # earlier authorization-phase check.
            return reject_before_handler("POLICY_REQUIREMENT_UNMET")
        risk_admin_cancel = (capability.port == "service.risk_admin" and operation == _RISK_ADMIN_CANCEL
                             and self._risk_admin_cancel_admission_configured)
        if risk_admin_cancel and (set(arguments) != {"operation_id", "scope_kind", "scope_value"}
                                  or any(type(value) is not str for value in arguments.values())):
            return self._failure("INVALID_REQUEST")
        write_fact_kind = None
        if (operation in write_ops or (capability.port == "service.risk_admin"
                                      and operation in _RISK_ADMIN_WRITE_OPERATIONS)):
            write_fact_kind = "dispatch"
        elif capability.port == "service.account_read" and (
            operation in {"account.ack", "account.events.read_after", "account.events.ack", "account.facts.read_after", "account.facts.read_after.v2", "account.facts.ack", "account.facts.snapshot_read", "account.facts.snapshot_confirm", "account.cancel_attempts.read_after", "account.cancel_attempts.ack"}
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
                    return reject_before_handler(_MIGRATION_WRITE_DISABLED)
                # Marker/epoch/lock uncertainty must prevent handler entry.
                return reject_before_handler("UNAVAILABLE")
            except OSError:
                # Marker/epoch/lock uncertainty must prevent handler entry.
                return reject_before_handler("UNAVAILABLE")
            except BaseException:
                reject_before_handler("UNAVAILABLE")
                raise
        # A stopped submit is still a write attempt and must cross the MIG03
        # read-only/seal boundary above. It is rejected here, before the
        # execution handler, so it never becomes a provider dispatch.
        try:
            stopped = operation in _FREEZE_OPERATIONS and self._strategy_write_is_frozen(scope)
        except BaseException:
            reject_before_handler("UNAVAILABLE")
            raise
        if stopped:
            return reject_before_handler("STRATEGY_STOPPED")
        admin_admission = None
        if risk_admin_cancel:
            # The operator cancellation contract contains only three immutable
            # strings. Do not issue an admission for nested mutable values or
            # for an incomplete/different request contract.
            binding = _RiskAdminCancelBinding(
                registration_id=row["registration_id"], registration_key=row["registration_key"],
                registration_digest=row["request_digest"], scope_ref=row["scope_ref"],
                service_epoch=self._epoch, instance_id=instance_id, principal=principal,
                identity=identity, scope=scope,
                arguments_sha256=hashlib.sha256(encode_wire_value(arguments)).digest(), pid=os.getpid(),
                issuer_thread=threading.current_thread(),
            )
            try:
                with self._read_snapshot():
                    admission_error = self._risk_admin_cancel_admission_error(binding)
                    if admission_error is not None:
                        return self._failure(admission_error)
                    admin_admission = RiskAdminCancelAdmission(
                        _RISK_ADMIN_CANCEL_ISSUER, self, binding=binding, arguments=arguments,
                    )
                    self._risk_admin_cancel_admissions[admin_admission] = binding
                    context = replace(context, dispatch_admission=admin_admission)
                    admin_admission._bind_context(context)
            except Exception:
                return self._failure("UNAVAILABLE")
        release_action_binding = None
        try:
            try:
                if capability.port == _EXECUTION_RELEASE_PORT:
                    # Only this code-owned, side-effect-free observer is allowed.
                    # A future durable claim needs its own explicit integration.
                    if handler is not _execution_release_local_policy_handler or admission is None:
                        return self._failure('CAPABILITY_UNAVAILABLE')
                    try:
                        from bt_api_base.market_protocol import MarketInstrumentKey as ReleaseInstrumentKey
                        instrument = ReleaseInstrumentKey.from_dict(arguments['instrument'])
                        action = ExecutionActionRequest(instance_id, identity, scope.account_ref,
                            scope.strategy_ref, scope.execution_domain_ref, arguments['grant_revision'],
                            instrument, arguments['plan_version'], 'release_child')
                        action_binding = self.bind_execution_action(action)
                        release_action_binding = action_binding
                        with admission.release_action_scope(context, arguments, action_binding) as check:
                            check()
                            result = handler(context, arguments)
                            check()
                    except (KeyError, TypeError, ValueError, ServiceDispatchAdmissionError):
                        return self._failure('SCOPE_DENIED')
                else:
                    result = handler(context, arguments)
            finally:
                if admin_admission is not None:
                    # A returned or failed handler cannot retain authority for
                    # a later call. Only its synchronous local work may use it.
                    admin_admission.close()
            if not isinstance(result, Mapping):
                return self._failure("HANDLER_FAILED")
            if (capability.port == "service.telemetry_ingress" and operation == "monitor.heartbeat") or (
                capability.port == "service.market_source" and operation == "market.clock_observation"
            ) or (
                capability.port == "service.direct_write"
                and operation in {"direct.strategy_position", "direct.strategy_close_capacity", "direct.strategy_financial", "direct.instrument_status"}
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
                            or (operation == 'direct.instrument_status' and context.service_epoch != self._epoch)
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
            if ((capability.port == "service.direct_write" and operation in ({_GATEWAY_ACCEPTANCE_OPERATION} | _GATEWAY_CONTROL_READ_OPERATIONS | _GATEWAY_DELEGATED_READ_OPERATIONS | _GATEWAY_PARENT_OPERATIONS | _GATEWAY_DELEGATE_ACTOR_OPERATIONS | _GATEWAY_STRATEGY_FINANCIAL_OPERATIONS))
                    or (capability.port == "service.account_read" and operation in (_GATEWAY_ACCOUNT_OBSERVER_OPERATIONS | _GATEWAY_ACCOUNT_STRATEGY_READ_OPERATIONS))):
                with self._read_snapshot():
                    code = self._direct_dispatch_admission_error(context.registration_id, self._epoch,
                        instance_id, principal, identity, scope, operation)
                    if code is not None:
                        return self._failure(code)
                    if (operation == _GATEWAY_ACCEPTANCE_OPERATION and
                            (admission is None or self.gateway_parent_acceptance_grant(
                                instance_id=instance_id, scope_ref=row['scope_ref'], identity=identity, scope=scope)
                             is not admission._binding.acceptance_grant or
                             self._gateway_acceptance_generation_for(instance_id=instance_id,
                                scope_ref=row['scope_ref'], identity=identity, scope=scope)
                             != admission._binding.acceptance_generation)):
                        return self._failure('SCOPE_DENIED')
            if capability.port == _EXECUTION_PARENT_PORT and operation in _EXECUTION_PARENT_OPERATIONS:
                with self._read_snapshot():
                    code = self._execution_parent_admission_error(context.registration_id, self._epoch,
                        instance_id, principal, identity, scope, operation,
                        replay_proof=None if admission is None else admission._binding.execution_parent_replay)
                    if code is not None:
                        # The handler may already have committed a parent. The
                        # existing client maps HANDLER_FAILED to business
                        # outcome UNKNOWN; it is never a not-sent proof.
                        return self._failure('HANDLER_FAILED')
                    if (admission is None or self._execution_parent_generation_for(
                            instance_id=instance_id, scope_ref=row['scope_ref'], identity=identity, scope=scope)
                            != admission._binding.execution_parent_generation
                            or self.execution_parent_grant(instance_id=instance_id, scope_ref=row['scope_ref'],
                                identity=identity, scope=scope, operation=operation) is not admission._binding.execution_parent_grant):
                        return self._failure('HANDLER_FAILED')
            if capability.port == _EXECUTION_RELEASE_PORT and operation in _EXECUTION_RELEASE_OPERATIONS:
                with self._read_snapshot():
                    code = self._execution_release_admission_error(context.registration_id, self._epoch,
                        instance_id, principal, identity, scope, operation)
                    record = (None if release_action_binding is None else
                              self._execution_action_bindings.get(release_action_binding))
                    if (code is not None or record is None
                            or record[0] is not release_action_binding._request
                            or record[1] is not release_action_binding._grant
                            or record[2] != release_action_binding._generation
                            or self._execution_action_grants.get(record[3]) is not record[1]
                            or self._execution_action_generations.get(record[3], 0) != record[2]
                            or _execution_action_facts(record[0]) != record[4]
                            or not _execution_action_allows(record[1], record[0])
                            or not _release_action_facts_match(record[0], arguments)
                            or encode_wire_value(arguments) != admission._binding.arguments):
                        return self._failure('HANDLER_FAILED')
            if (capability.port == "service.account_read" and operation in {
                "account.instrument_spec.begin", "account.instrument_spec.status",
                "account.instrument_spec.cancel",
            }) or (capability.port == _PRODUCT_PORT and operation in _PRODUCT_OPERATIONS):
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
                                for required in (_PRODUCT_OPERATIONS if capability.port == _PRODUCT_PORT else (
                                    "account.instrument_spec.begin", "account.instrument_spec.status",
                                    "account.instrument_spec.cancel")))):
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
                    (_PRODUCT_PORT, "instrument.product.begin.v1"),
                    (_PRODUCT_PORT, "instrument.product.status.v1"),
                    (_PRODUCT_PORT, "instrument.product.cancel.v1"),
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
        finally:
            if capability.port == _EXECUTION_RELEASE_PORT and admission is not None:
                admission.close()
                with self._lock:
                    self._dispatch_admissions.pop(admission, None)

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

    def _current_resource_allows_guards(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        capability: ServiceCapability,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operation: str,
    ) -> bool:
        if port == _EXECUTION_RELEASE_PORT:
            if (operation not in _EXECUTION_RELEASE_OPERATIONS
                    or not _is_execution_release_scope(identity, port, scope)
                    or self._execution_release_policy(instance_id, identity, scope) is None):
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
        if (operation in capability.write_operations
                or (port == "service.risk_admin" and operation in _RISK_ADMIN_WRITE_OPERATIONS)) and not (
            _is_exact_owner_cancel_scope(
                identity, port=port, scope=scope, operation=operation
            )
            or _is_exact_owner_reduce_scope(
                identity, port=port, scope=scope, operation=operation
            )
        ):
            if port == "service.risk_admin" and operation in _RISK_ADMIN_WRITE_OPERATIONS:
                if not _risk_admin_write_scope(identity, scope):
                    return False
            elif (identity.role is WireRole.APPLICATION_CLIENT and port == "service.direct_write"
                    and operation in _GATEWAY_AUTHENTICATED_CONTROL_WRITE_OPERATIONS):
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
            elif (
                self._risk_parent_execution_writes_enabled
                and identity.role is WireRole.SERVICE
                and port == "service.risk_order"
                and operation in _RISK_EXECUTION_PARENT_WRITE_OPERATIONS
            ):
                if (
                    scope.account_ref is None
                    or scope.account_refs
                    or scope.strategy_ref is None
                    or scope.run_id is None
                    or scope.manual_book_ref is not None
                    or scope.portfolio_ref is not None
                    or scope.group_id is not None
                    or scope.leg_id is not None
                    or identity.delegate_ref is not None
                ):
                    return False
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
        return True

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
        if not self._current_resource_allows_guards(
            principal,
            identity,
            capability=capability,
            instance_id=instance_id,
            port=port,
            scope_ref=scope_ref,
            scope=scope,
            operation=operation,
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

    def _current_resource_allows_all(
        self,
        principal: str,
        identity: WireIdentity,
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        scope: WireScope,
        operations: frozenset[str],
    ) -> bool:
        """Exact batch equivalent of checking ``_current_resource_allows`` for every op."""
        capability = self._capabilities.get((instance_id, port))
        if capability is None or not operations.issubset(capability.operations):
            return False
        allowed_ops: set[str] = set()
        for principal_grant in self._grants.get(principal, ()):
            if principal_grant.identity != identity:
                continue
            for resource in principal_grant.resources:
                if (
                    resource.instance_id == instance_id
                    and resource.port == port
                    and resource.scope_ref == scope_ref
                    and self._resource_scope_matches(resource, scope, identity)
                ):
                    allowed_ops.update(resource.operations)
        if not operations.issubset(allowed_ops):
            return False
        if port in (
            _EXECUTION_RELEASE_PORT,
            "service.group_reservations",
            "service.order_entry",
            "service.risk_admin",
        ):
            ops_to_guard = operations
        else:
            ops_to_guard = operations.intersection(capability.write_operations)
        for operation in ops_to_guard:
            if not self._current_resource_allows_guards(
                principal,
                identity,
                capability=capability,
                instance_id=instance_id,
                port=port,
                scope_ref=scope_ref,
                scope=scope,
                operation=operation,
            ):
                return False
        return True

    def _strategy_claim_exists(self, scope: WireScope, registration_id: str) -> bool:
        if scope.strategy_ref is None or scope.run_id is None:
            return False
        accounts = (scope.account_ref,) if scope.account_ref is not None else scope.account_refs
        if not accounts:
            return False
        session = self._db.execute('SELECT principal,identity_json FROM sessions WHERE registration_id=?',
                                   (registration_id,)).fetchone()
        if session is None:
            return False
        try:
            identity = self._stored_identity(session['identity_json'])
            if self._execution_subject_claim(scope, session['principal'], identity, accounts, frozenset(),
                                             probe_only=True, active_registration_id=registration_id) is not None:
                return False
        except (TypeError, ValueError, KeyError):
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
