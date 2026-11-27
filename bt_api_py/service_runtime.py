"""SDK service proxies: explicit registration, bounded RPC, no direct fallback.

This module imports neither a domain component nor a transport backend. A
successful start establishes registrations; consumers must still restore their
data baselines before declaring a strategy ready to trade.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import BuiltinFunctionType, MappingProxyType
from typing import Any

from bt_api_base.risk_service_protocol import (
    RISK_ADMIN_OPERATIONS,
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_SERVICE_PORT,
)
from bt_api_base.service_protocol import (
    MAX_WIRE_MESSAGE_BYTES,
    WIRE_PROTOCOL_VERSION,
    RequestReplyTransport,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    encode_envelope,
    encode_wire_value,
    validate_reply,
    wire_contract_digest,
)

from bt_api_py.service_config import ClientConfig, ServiceConfig
from bt_api_py.service_deployment import MOUNT_PORTS, OPTIONAL_MOUNT_PORTS
from bt_api_py.service_registry import SERVICE_ERROR_CODES

# An uncertain raw cleanup must survive the caller dropping its runtime, even
# when a notification callback is absent in effect or fails. These roots are
# deliberately retained for the process lifetime; there is no automatic
# retry or release of an unresolved cleanup.
_RAW_STICKY_CLEANUP_ROOTS: dict[int, Any] = {}
_RAW_STICKY_CLEANUP_ROOTS_LOCK = threading.RLock()
# Bound sends compare lease times only in this process's native clock domain.
# Assumes this module is imported before the process clock is replaced.
_BOUND_MONOTONIC = time.monotonic
_BOUND_MONOTONIC_NS = time.monotonic_ns
_BOUND_RISK_WALL_NS = time.time_ns
_EXECUTION_SLEEP_CLOCK = getattr(time, 'clock_gettime_ns', None)
_EXECUTION_NONCE_BYTES = os.urandom
_EXECUTION_SLEEP_CLOCK_ID = (getattr(time, 'CLOCK_MONOTONIC', None) if sys.platform == 'darwin'
                             else getattr(time, 'CLOCK_BOOTTIME', None) if sys.platform.startswith('linux')
                             else None)


class _ExecutionSuspendClock:
    """Owner-process clock; failure or regression permanently withdraws it."""
    __slots__ = ('_pid', '_thread', '_last', '_failed')

    def __init__(self):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._last, self._failed = None, False

    def now(self):
        if self._pid != os.getpid() or self._thread != threading.get_ident() or self._failed:
            raise ValueError('execution clock unavailable')
        try:
            if (type(_EXECUTION_SLEEP_CLOCK) is not BuiltinFunctionType
                    or _EXECUTION_SLEEP_CLOCK.__module__ != 'time'
                    or _EXECUTION_SLEEP_CLOCK.__name__ != 'clock_gettime_ns'
                    or type(_EXECUTION_SLEEP_CLOCK_ID) is not int):
                raise ValueError
            value = _EXECUTION_SLEEP_CLOCK(_EXECUTION_SLEEP_CLOCK_ID)
            if (type(value) is not int or not 0 <= value < 2**63
                    or (self._last is not None and value < self._last)):
                raise ValueError
        except (OSError, ValueError, TypeError, OverflowError):
            self._failed = True
            raise ValueError('execution clock unavailable') from None
        self._last = value
        return value


def _execution_ttl_ns(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('invalid execution lease duration')
    numerator, denominator = value.as_integer_ratio()
    result = numerator * 1_000_000_000 // denominator
    if not 0 < result < 2**63:
        raise ValueError('invalid execution lease duration')
    return result


def _bound_clock_current() -> bool:
    try:
        from bt_api_transport_zmq import service_v2
        return (time.monotonic is _BOUND_MONOTONIC and time.monotonic_ns is _BOUND_MONOTONIC_NS
                and getattr(service_v2, '_BOUND_MONOTONIC', None) is _BOUND_MONOTONIC
                and getattr(service_v2, '_BOUND_MONOTONIC_NS', None) is _BOUND_MONOTONIC_NS
                and service_v2.time.monotonic is _BOUND_MONOTONIC
                and service_v2.time.monotonic_ns is _BOUND_MONOTONIC_NS)
    except (AttributeError, ImportError):
        return False


def _deadline_ns_floor(deadline: float) -> int:
    """Convert a finite monotonic deadline without rounding it later."""
    numerator, denominator = deadline.as_integer_ratio()
    return numerator * 1_000_000_000 // denominator


def _clock_domain_margin_ns(before: float, after: float) -> int:
    numerator, denominator = max(math.ulp(before), math.ulp(after)).as_integer_ratio()
    return max(1000, (numerator * 1_000_000_000 + denominator - 1) // denominator)


def _bound_receipt_time_valid(received_ns: int, started_ns: int, deadline: float) -> bool:
    if not _bound_clock_current():
        return False
    before = _BOUND_MONOTONIC()
    now_ns = _BOUND_MONOTONIC_NS()
    after = _BOUND_MONOTONIC()
    # This tolerance is only for the cross-unit domain sanity check. Request
    # and deadline bounds below use exact integer nanoseconds.
    margin = _clock_domain_margin_ns(before, after)
    return (_bound_clock_current() and type(now_ns) is int
            and _deadline_ns_floor(before) - margin <= now_ns <= _deadline_ns_floor(after) + margin
            and started_ns <= received_ns <= now_ns
            and received_ns <= _deadline_ns_floor(deadline))

# Code-owned action lists. Deployments may expose a subset; configuration and
# remote discovery cannot introduce new actions into a proxy.
_CONTROL_INTENT_OPERATIONS = frozenset({"direct.control.intent.apply"})
PORT_OPERATIONS = MappingProxyType(
    {
        "service.calendar_read": frozenset({"calendar.query.v1", "calendar.windows.v1", "calendar.monitor.windows.v1"}),
        "service.instrument_product_read": frozenset({"instrument.product.begin.v1", "instrument.product.status.v1", "instrument.product.cancel.v1"}),
        "service.execution_delegate": frozenset(
            {"execution.child.release_policy_check"}
        ),
        "service.execution_parent": frozenset(
            {"execution.parent.submit", "execution.parent.query"}
        ),
        "service.execution_management_read": frozenset(
            {"execution.management.parent.read.v1", "execution.management.parent.plan.read.v1",
             "execution.management.parent.by_request.read.v1", "execution.management.parent.discover_recent.v1"}
        ),
        "service.direct_write": frozenset(
            {"direct.reserve", "direct.risk_first_reserve.v2", "direct.release", "direct.command_status", "direct.dispatch",
             "direct.cancel", "direct.cancel_status",
             "direct.strategy_position", "direct.strategy_close_capacity", "direct.strategy_financial", "direct.instrument_status",
             "direct.delegated_command_status",
             "direct.delegated_cancel", "direct.delegated_cancel_status",
             "direct.delegated_reserve", "direct.delegated_dispatch",
             "direct.delegate_actor_status", "direct.delegate_actor_cancel", "direct.delegate_actor_cancel_status", "direct.delegate_actor_release", "direct.delegate_actor_release_status",
             "direct.delegate_actor_status_v2", "direct.delegate_actor_release_status_v2",
             "direct.delegate_events.read_after", "direct.delegate_events.progress", "direct.delegate_events.ack",
             "direct.parent.register", "direct.parent.query", "direct.parent.cancel", "direct.parent.operation", "direct.parent.source", "direct.parent.register_v2", "direct.parent.operation_v2", "direct.parent.cancel_v2", "direct.parent.acceptance_order",
             "direct.control.apply", "direct.control.resume", "direct.control.identity", "direct.control.status", "direct.control.targets", "direct.control.list", "direct.control.drain_target", "direct.control.reconcile",
             "direct.control.resume_status", "direct.control.target_status",
             "direct.control.cancel_scope", "direct.control.cancel_scope_status",
             *_CONTROL_INTENT_OPERATIONS,
             "direct.events.read_after", "direct.events.progress", "direct.events.ack"}
        ),
        "service.market_source": frozenset(
            {
                "market.bootstrap_many",
                "market.bootstrap_begin",
                "market.bootstrap_status",
                "market.bootstrap_cancel",
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
                "market.read_after_bounded",
                "market.clock_observation",
                "market.history",
            }
        ),
        "service.account_read": frozenset(
            {
                "account.snapshot",
                "account.snapshot.typed",
                "account.head",
                "account.read_after",
                "account.ack",
                "account.progress",
                "account.configuration",
                "account.server_time_probe",
                "account.instrument_metadata",
                "account.instrument_spec.begin",
                "account.instrument_spec.status",
                "account.instrument_spec.cancel",
                "account.strategy_net_comparison",
                "account.strategy_financial",
                "account.strategy_book",
                "account.executions.recent",
                "account.events.read_after",
                "account.events.progress",
                "account.events.ack",
                "account.facts.read_after",
                "account.facts.read_after.v2",
                "account.facts.progress",
                "account.facts.ack",
                "account.facts.status",
                "account.facts.snapshot_info",
                "account.facts.snapshot_read",
                "account.facts.snapshot_confirm",
                "account.order_cut.current.v1",
                "account.order_cut.query_page.v1",
                "account.order_cut.receipt_page.v1",
                "account.cancel_attempts.read_after",
                "account.cancel_attempts.progress",
                "account.cancel_attempts.ack",
                "account.reconciliation_cut",
            }
        ),
        "service.order_entry": frozenset(
            {
                "orders.submit",
                "orders.submit_approved_target",
                "orders.cancel",
                "orders.submit_group_leg",
                "orders.resolve_group_leg",
                "orders.cancel_group_leg",
                "orders.submit_group_compensation_close",
                "orders.submit_group_roll_exit",
                "orders.submit_recovery_close",
            }
        ),
        "service.order_query": frozenset(
            {
                "orders.query",
                "orders.snapshot",
                "orders.head",
                "orders.read_after",
                "orders.ack",
                "orders.query_group_leg",
                "orders.query_group_cancel",
                "execution.account_books.query",
                "orders.prepare_group_compensation_close",
                "orders.query_group_compensation_close",
                "orders.prepare_group_roll_exit",
                "orders.query_group_roll_exit",
            }
        ),
        "service.order_group_entry": frozenset(
            {
                "groups.submit",
                "groups.query",
                "groups.cancel",
                "groups.roll_exit",
                "groups.query_roll_exit",
            }
        ),
        "service.risk_query": frozenset(
            {"risk.snapshot", "risk.head", "risk.read_after", "risk.ack"}
        ),
        # Iteration 64: the independent Risk service (names frozen in Base).
        RISK_SERVICE_PORT: RISK_ORDER_OPERATIONS,
        RISK_ADMIN_PORT: RISK_ADMIN_OPERATIONS,
        "service.risk_analysis": frozenset(
            {"analysis.submit", "analysis.query", "analysis.cancel", "analysis.input_upload"}
        ),
        "service.risk_policy": frozenset(
            {
                "policy.read", "policy.draft", "policy.approve", "policy.publish", "policy.receipt",
                "policy.inspect", "policy.confirmation.read",
                "policy.budget.propose", "policy.budget.approve", "policy.budget.publish_member",
                "policy.budget.confirm_member", "policy.budget.owner_confirm_member",
                "policy.budget.reject_member",
                "policy.budget.mark_unknown", "policy.budget.retract", "policy.budget.read",
            }
        ),
        "service.telemetry_ingress": frozenset({"telemetry.append", "events.append", "monitor.heartbeat"}),
        "service.monitor_query": frozenset(
            {"monitor.query", "monitor.read_after", "telemetry.snapshot", "observation.query"}
        ),
        "service.monitor_control": frozenset(
            {"monitor.control", "monitor.control_status"}
        ),
        "service.owner_control": frozenset(
            {"owner.session", "owner.stop_new", "owner.cancel", "owner.reduce"}
        ),
        "service.group_reservations": frozenset(
            {"reservations.reserve", "reservations.query", "reservations.release"}
        ),
        "service.paper_legacy_import": frozenset(
            {
                "paper.import.status",
                "paper.import.inspect_source",
                "paper.import.prepare",
                "paper.import.activate",
                "paper.import.abort",
            }
        ),
    }
)

_INSTRUMENT_SPEC_OPERATIONS = frozenset(
    {
        "account.instrument_spec.begin",
        "account.instrument_spec.status",
        "account.instrument_spec.cancel",
    }
)
_DEFAULT_ACCOUNT_READ_OPERATIONS = frozenset(
    {"account.read_after", "account.snapshot", "account.head"}
)
_DEFAULT_TELEMETRY_INGRESS_OPERATIONS = frozenset({"telemetry.append"})
_GROUP_MONITOR_OPERATIONS = frozenset({"monitor.read_after"})
_GROUP_ORDER_ENTRY_OPERATIONS = frozenset(
    {"orders.submit_group_leg", "orders.resolve_group_leg"}
)
_GROUP_ORDER_CANCEL_ENTRY_OPERATIONS = frozenset({"orders.cancel_group_leg"})
_GROUP_ORDER_ENTRY_ALL_OPERATIONS = (
    _GROUP_ORDER_ENTRY_OPERATIONS | _GROUP_ORDER_CANCEL_ENTRY_OPERATIONS
)
_DEFAULT_GROUP_ORDER_ENTRY_OPERATIONS = frozenset({"groups.submit", "groups.query"})
_APPROVED_ORDER_ENTRY_OPERATIONS = frozenset({"orders.submit_approved_target"})
_GROUP_COMPENSATION_ORDER_ENTRY_OPERATIONS = frozenset(
    {"orders.submit_group_compensation_close"}
)
_GROUP_ROLL_EXIT_ORDER_ENTRY_OPERATIONS = frozenset(
    {"orders.submit_group_roll_exit"}
)
_RECOVERY_CLOSE_ORDER_ENTRY_OPERATIONS = frozenset(
    {"orders.submit_recovery_close"}
)
_REGISTRATION_POLICY_FIELDS = frozenset(
    {
        "access_policy_ref",
        "access_policy_version",
        "risk_policy_status",
        "risk_policy_ref",
        "risk_policy_version",
        "requirements_ref",
    }
)


class ServiceRuntimeError(RuntimeError):
    """Finite diagnostics, with no configuration values or remote error text."""

    def __init__(self, code: str) -> None:
        self.code = code
        # Optional exact rejection detail from a control reply (for example
        # the released durable strategy-write run a registration may adopt).
        # Never carries remote error text or configuration values.
        self.result: Mapping[str, Any] = {}
        # Set only by ``_rpc`` when this error is the service's own rejection
        # reply to the request just sent (reply correlated to that request and
        # from the authenticated peer); then it equals ``code``. None for every
        # local, transport, timeout or lost-reply failure. It names who answered,
        # not that the rejected operation had no effect: callers decide that per
        # operation.
        self.service_reply_code: str | None = None
        super().__init__(code)


class ServiceRuntimeStartupCleanupError(ServiceRuntimeError):
    """A transport factory failed but retained a partial owner-thread resource."""

    def __init__(self, transport: RequestReplyTransport) -> None:
        self.transport = transport
        super().__init__("TRANSPORT_STARTUP_CLEANUP_FAILED")


class ServiceRuntimeRequestNotSentError(ServiceRuntimeError):
    """A transport has proved that a request was never queued for delivery."""

    def __init__(self) -> None:
        super().__init__("REQUEST_NOT_SENT")


class _LiveParentSourceCapability:
    """Opaque process-local, single-use handle; never a durable authority."""

    __slots__ = ('_revoked', '_owner_ref', '_owner_pid')

    def __init__(self, owner=None):
        self._revoked = threading.Event()
        self._owner_ref = None if owner is None else weakref.ref(owner)
        self._owner_pid = os.getpid()

    def _revoke(self):
        # Never acquire an inherited Event or registry lock in a fork child.
        # Child runtimes reject this capability without changing parent memory.
        if os.getpid() != self._owner_pid:
            return
        self._revoked.set()
        owner = None if self._owner_ref is None else self._owner_ref()
        if owner is not None:
            with owner._live_parent_capabilities_lock:
                owner._live_parent_capabilities.pop(self, None)

    def __copy__(self):
        raise TypeError("capability cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("capability cannot be copied")

    def __reduce_ex__(self, _protocol):
        raise TypeError("capability cannot be serialized")


class _RiskFirstWireReceipt:
    """Opaque owner-local lookup key; its type alone conveys no authority."""

    __slots__ = ()

    def __copy__(self):
        raise TypeError("receipt cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("receipt cannot be copied")

    def __reduce_ex__(self, _protocol):
        raise TypeError("receipt cannot be serialized")


class ServiceCallOutcomeUnknownError(ServiceRuntimeError):
    """A dispatched handler failed without proving that it had no side effects."""

    def __init__(self, message_id: str, operation: str) -> None:
        self.message_id = message_id
        self.operation = operation
        super().__init__("BUSINESS_OUTCOME_UNKNOWN")


@dataclass(frozen=True)
class ClientCurveKeys:
    """Resolved private material, supplied separately from public config YAML."""

    server_key: bytes
    public_key: bytes
    secret_key: bytes = field(repr=False)


@dataclass(frozen=True)
class BoundServiceTarget:
    """Trusted deployment expectations for one registered service route.

    The composing application must obtain the expected peer, endpoint and
    server-key fingerprint independently of the runtime's registration reply.
    This value binds a call to a registration, but is not account authority or
    proof of continuity with a prior service owner.
    """

    mount: str
    port: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity
    run_id: str
    transport_ref: str
    security_profile_ref: str
    endpoint: str
    expected_peer: WireIdentity
    server_key_fingerprint: str = field(repr=False)
    registration_id: str
    service_epoch: str

    def __post_init__(self) -> None:
        self._validate(WireRole.SERVICE)

    def _validate(self, caller_role: WireRole) -> None:
        texts = (self.mount, self.port, self.service_id, self.instance_id, self.scope_ref,
                 self.run_id, self.transport_ref, self.security_profile_ref, self.endpoint,
                 self.registration_id, self.service_epoch)
        if (any(type(value) is not str or not value or value != value.strip() or len(value) > 1024
                for value in texts)
                or type(self.scope) is not WireScope or self.scope.run_id != self.run_id
                or type(self.identity) is not WireIdentity or self.identity.role is not caller_role
                or self.identity.delegate_ref is not None
                or type(self.expected_peer) is not WireIdentity or self.expected_peer.role is not WireRole.SERVICE
                or self.expected_peer.delegate_ref is not None
                or type(self.server_key_fingerprint) is not str or len(self.server_key_fingerprint) != 64
                or any(character not in '0123456789abcdef' for character in self.server_key_fingerprint)
                or self.port not in MOUNT_PORTS.get(self.mount, ()) + OPTIONAL_MOUNT_PORTS.get(self.mount, ())
                or not self.endpoint.startswith('tcp://')):
            raise ValueError('INVALID_BOUND_SERVICE_TARGET')


@dataclass(frozen=True)
class BoundExecutionParentTarget(BoundServiceTarget):
    """Strategy caller bound exclusively to the Execution parent port."""

    def __post_init__(self) -> None:
        self._validate(WireRole.STRATEGY_CLIENT)
        if (self.mount != 'broker.execution_parent' or self.port != 'service.execution_parent'
                or self.scope.account_ref is None or self.scope.strategy_ref is None):
            raise ValueError('INVALID_BOUND_EXECUTION_PARENT_TARGET')


_ACCOUNT_FACTS_BOUND_OPERATIONS = frozenset({
    'account.facts.read_after', 'account.facts.read_after.v2', 'account.facts.ack', 'account.facts.progress', 'account.facts.status',
    'account.facts.snapshot_info', 'account.facts.snapshot_read', 'account.facts.snapshot_confirm',
    'account.order_cut.current.v1', 'account.order_cut.query_page.v1', 'account.order_cut.receipt_page.v1',
})
_ACCOUNT_BOUND_OPERATION = 'direct.parent.acceptance_order'
_MANAGEMENT_BOUND_OPERATION = 'execution.management.parent.read.v1'
_MANAGEMENT_PLAN_BOUND_OPERATION = 'execution.management.parent.plan.read.v1'
_MANAGEMENT_BY_REQUEST_BOUND_OPERATION = 'execution.management.parent.by_request.read.v1'
_MANAGEMENT_DISCOVERY_BOUND_OPERATION = 'execution.management.parent.discover_recent.v1'
_MONITOR_QUERY_BOUND_OPERATION = 'observation.query'


@dataclass(frozen=True)
class BoundAccountServiceTarget:
    """Trusted account route expectations; runtime_run_id is local only.

    This binding grants no authority and is not a Gateway owner generation.
    """

    mount: str
    port: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity
    runtime_run_id: str
    transport_ref: str
    security_profile_ref: str
    endpoint: str
    expected_peer: WireIdentity
    server_key_fingerprint: str = field(repr=False)
    registration_id: str
    service_epoch: str

    def __post_init__(self) -> None:
        texts = (self.mount, self.port, self.service_id, self.instance_id, self.scope_ref,
                 self.runtime_run_id, self.transport_ref, self.security_profile_ref, self.endpoint,
                 self.registration_id, self.service_epoch)
        if (any(type(value) is not str or not value or value != value.strip() or len(value) > 1024
                for value in texts)
                or self.mount != 'upstream.direct_write' or self.port != 'service.direct_write'
                or type(self.scope) is not WireScope or self.scope.account_ref is None
                or self.scope.account_refs != () or self.scope.strategy_ref is not None
                or self.scope.run_id is not None or self.scope.manual_book_ref is not None
                or self.scope.portfolio_ref is not None or self.scope.group_id is not None
                or self.scope.leg_id is not None
                or type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE
                or self.identity.delegate_ref is not None
                or type(self.expected_peer) is not WireIdentity or self.expected_peer.role is not WireRole.SERVICE
                or self.expected_peer.delegate_ref is not None
                or type(self.server_key_fingerprint) is not str or len(self.server_key_fingerprint) != 64
                or any(character not in '0123456789abcdef' for character in self.server_key_fingerprint)
                or not self.endpoint.startswith('tcp://')):
            raise ValueError('INVALID_BOUND_ACCOUNT_SERVICE_TARGET')


@dataclass(frozen=True)
class BoundExecutionManagementReadTarget:
    """Trusted application route; the runtime run id is never a strategy run."""

    mount: str
    port: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity
    runtime_run_id: str
    transport_ref: str
    security_profile_ref: str
    endpoint: str
    expected_peer: WireIdentity
    server_key_fingerprint: str = field(repr=False)
    registration_id: str
    service_epoch: str

    def __post_init__(self) -> None:
        from bt_api_py.execution_management_read_grant import _identity, _scope
        texts = (self.service_id, self.instance_id, self.scope_ref, self.runtime_run_id,
                 self.transport_ref, self.security_profile_ref, self.endpoint,
                 self.registration_id, self.service_epoch)
        if (any(type(value) is not str or not value or value != value.strip() or len(value) > 1024
                for value in texts)
                or self.mount != 'application.execution_management'
                or self.port != 'service.execution_management_read'
                or not _scope(self.scope) or not _identity(self.identity)
                or type(self.expected_peer) is not WireIdentity or self.expected_peer.role is not WireRole.SERVICE
                or self.expected_peer.delegate_ref is not None
                or type(self.server_key_fingerprint) is not str or len(self.server_key_fingerprint) != 64
                or any(character not in '0123456789abcdef' for character in self.server_key_fingerprint)
                or not self.endpoint.startswith('tcp://')):
            raise ValueError('INVALID_BOUND_EXECUTION_MANAGEMENT_READ_TARGET')


@dataclass(frozen=True)
class BoundMonitorQueryTarget:
    """Trusted, application-only binding for authenticated monitor pages.

    This fixes one monitor registration, peer, CURVE key, WireScope and
    application identity. The request HMAC remains a separate service-level
    check; this target grants no authority by itself.
    """

    mount: str
    port: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity
    runtime_run_id: str
    transport_ref: str
    security_profile_ref: str
    endpoint: str
    expected_peer: WireIdentity
    server_key_fingerprint: str = field(repr=False)
    registration_id: str
    service_epoch: str

    def __post_init__(self) -> None:
        texts = (self.mount, self.port, self.service_id, self.instance_id, self.scope_ref, self.runtime_run_id,
                 self.transport_ref, self.security_profile_ref, self.endpoint,
                 self.registration_id, self.service_epoch)
        if (any(type(value) is not str or not value or value != value.strip() or len(value) > 1024
                for value in texts)
                or self.mount != 'application.monitor'
                or self.port != 'service.monitor_query'
                or type(self.scope) is not WireScope
                or self.scope.account_refs != () or self.scope.group_id is not None
                or self.scope.leg_id is not None
                or type(self.identity) is not WireIdentity
                or self.identity.role is not WireRole.APPLICATION_CLIENT
                or self.identity.delegate_ref is not None
                or type(self.expected_peer) is not WireIdentity
                or self.expected_peer.role is not WireRole.SERVICE
                or self.expected_peer.delegate_ref is not None
                or type(self.server_key_fingerprint) is not str or len(self.server_key_fingerprint) != 64
                or any(character not in '0123456789abcdef' for character in self.server_key_fingerprint)
                or not self.endpoint.startswith('tcp://')):
            raise ValueError('INVALID_BOUND_MONITOR_QUERY_TARGET')


def _validate_bound_target_operation(target: Any, operation: Any) -> None:
    if type(operation) is not str:
        raise ValueError('INVALID_BOUND_OPERATION')
    if type(target) is BoundMonitorQueryTarget:
        BoundMonitorQueryTarget.__post_init__(target)
        if operation != _MONITOR_QUERY_BOUND_OPERATION:
            raise ValueError('INVALID_BOUND_OPERATION')
    elif type(target) is BoundExecutionManagementReadTarget:
        BoundExecutionManagementReadTarget.__post_init__(target)
        if operation not in (_MANAGEMENT_BOUND_OPERATION, _MANAGEMENT_PLAN_BOUND_OPERATION,
                             _MANAGEMENT_BY_REQUEST_BOUND_OPERATION, _MANAGEMENT_DISCOVERY_BOUND_OPERATION):
            raise ValueError('INVALID_BOUND_OPERATION')
    elif type(target) is BoundAccountServiceTarget:
        BoundAccountServiceTarget.__post_init__(target)
        if operation != _ACCOUNT_BOUND_OPERATION:
            raise ValueError('INVALID_BOUND_OPERATION')
    elif type(target) is BoundExecutionParentTarget:
        BoundExecutionParentTarget.__post_init__(target)
        if operation not in PORT_OPERATIONS['service.execution_parent']:
            raise ValueError('INVALID_BOUND_OPERATION')
    elif type(target) is BoundServiceTarget:
        BoundServiceTarget.__post_init__(target)
        if operation in (_ACCOUNT_BOUND_OPERATION, _MANAGEMENT_BOUND_OPERATION,
                         _MANAGEMENT_PLAN_BOUND_OPERATION, _MANAGEMENT_BY_REQUEST_BOUND_OPERATION,
                         _MANAGEMENT_DISCOVERY_BOUND_OPERATION,
                         _MONITOR_QUERY_BOUND_OPERATION):
            raise ValueError('INVALID_BOUND_OPERATION')
    else:
        raise ValueError('INVALID_BOUND_TARGET')


# Factory mode is pinned outside the mutable instance, including its owner PID.
# Weak identity references avoid extending a factory's lifetime or trusting an
# object that later reuses its id.
_FACTORY_MODES: dict[int, tuple[weakref.ReferenceType[Any], bool, int]] = {}
_FACTORY_MODES_LOCK = threading.Lock()


def _reset_factory_modes_after_fork() -> None:
    global _FACTORY_MODES, _FACTORY_MODES_LOCK
    _FACTORY_MODES = {}
    _FACTORY_MODES_LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_factory_modes_after_fork)


def _remember_factory_mode(factory: Any, mode: bool, owner_pid: int) -> weakref.ReferenceType[Any]:
    factory_id = id(factory)

    def forget(reference: weakref.ReferenceType[Any]) -> None:
        lock = globals().get("_FACTORY_MODES_LOCK")
        if lock is None:
            return
        with lock:
            modes = globals().get("_FACTORY_MODES")
            if modes is not None:
                current = modes.get(factory_id)
                if current is not None and current[0] is reference:
                    del modes[factory_id]

    reference = weakref.ref(factory, forget)
    with _FACTORY_MODES_LOCK:
        current = _FACTORY_MODES.get(factory_id)
        if current is not None and current[0]() is factory:
            raise ServiceRuntimeError("FACTORY_ALREADY_INITIALIZED")
        _FACTORY_MODES[factory_id] = (reference, mode, owner_pid)
    return reference


def _forget_factory_mode(factory: Any, reference: weakref.ReferenceType[Any]) -> None:
    with _FACTORY_MODES_LOCK:
        current = _FACTORY_MODES.get(id(factory))
        if current is not None and current[0] is reference:
            del _FACTORY_MODES[id(factory)]


class _PendingZmqTransport:
    """Owner-thread handoff slot held by a runtime before factory entry."""

    def __init__(self) -> None:
        self._owner_pid = os.getpid()
        self._owner_thread = threading.get_ident()
        self._transport = None

    def _require_owner(self) -> None:
        if os.getpid() != self._owner_pid or threading.get_ident() != self._owner_thread:
            raise ServiceRuntimeError("TRANSPORT_OWNER_MISMATCH")

    def bind_client(self, client: RequestReplyTransport) -> None:
        self._require_owner()
        if self._transport is not None:
            raise ServiceRuntimeError("TRANSPORT_HANDOFF_INVALID")
        self._transport = client

    def bind_adapter(self, client: RequestReplyTransport, adapter: Any, startup_owner: Any = None) -> None:
        self._require_owner()
        if (self._transport is not client
                and (startup_owner is None or self._transport is not startup_owner
                     or startup_owner._client is not client)):
            raise ServiceRuntimeError("TRANSPORT_HANDOFF_INVALID")
        self._transport = adapter

    def owns_client(self, client: RequestReplyTransport, startup_owner: Any = None) -> bool:
        transport = self._transport
        return transport is client or (type(transport) is _RequestNotSentAdapter
                                       and transport._transport is client) or (
            startup_owner is not None and transport is startup_owner and startup_owner._client is client
        )

    def close(self) -> None:
        self._require_owner()
        transport = self._transport
        if transport is not None:
            transport.close()
            self._transport = None


class ZmqTransportFactory:
    """Code-registered ZMQ factory sharing one lazy context per process."""

    def __init__(self, security_profiles: Mapping[str, ClientCurveKeys], *,
                 strict_single_connection: bool = False) -> None:
        if type(strict_single_connection) is not bool:
            raise ValueError("strict_single_connection must be a boolean")
        owner_pid = os.getpid()
        reference = _remember_factory_mode(self, strict_single_connection, owner_pid)
        try:
            self._profiles = dict(security_profiles)
            self._strict_single_connection = strict_single_connection
            self._owner_pid = owner_pid
            self._context_pool_init_lock = threading.Lock()
            self._context_pool = None
        except BaseException:
            _forget_factory_mode(self, reference)
            raise

    def __call__(
        self, profile: Mapping[str, Any], expected_peer: WireIdentity, *,
        _pending: _PendingZmqTransport | None = None,
    ) -> RequestReplyTransport:
        if _pending is not None:
            if type(_pending) is not _PendingZmqTransport:
                raise ServiceRuntimeError("TRANSPORT_HANDOFF_INVALID")
            _pending._require_owner()
        mode = self._require_factory_mode()
        if profile.get("backend") != "zmq":
            raise ServiceRuntimeError("UNKNOWN_BACKEND")
        keys = self._profiles.get(profile["security_profile_ref"])
        if not isinstance(keys, ClientCurveKeys):
            raise ServiceRuntimeError("SECURITY_PROFILE_UNAVAILABLE")
        endpoint = profile["endpoints"].get("rpc_connect")
        if not endpoint:
            raise ServiceRuntimeError("RPC_ENDPOINT_REQUIRED")

        # Only this explicit activation point loads the optional transport.
        from bt_api_transport_zmq.server import CurveClientCredentials
        from bt_api_transport_zmq.service_v2 import (
            RequestNotSentError,
            ServiceTransportStartupCleanupError,
            ZmqServiceClient,
            ZmqServiceContextPool,
        )
        if mode:
            try:
                from bt_api_transport_zmq.service_v2 import (
                    _ServiceSocketBinding,
                    _strict_client_binding,
                )
            except ImportError:
                raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE") from None
        startup_owner_type = None
        if mode or _pending is not None:
            try:
                from bt_api_transport_zmq.service_v2 import _ServiceClientStartupOwner
                startup_owner_type = _ServiceClientStartupOwner
            except ImportError:
                if mode:
                    raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE") from None

        credentials = CurveClientCredentials(
            keys.server_key, keys.public_key, keys.secret_key
        )
        high_water_mark = profile["options"]["high_water_mark"]
        max_message_bytes = profile["options"]["max_message_bytes"]
        context_pool = self._shared_context_pool(ZmqServiceContextPool)
        self._require_factory_mode()
        startup_owner = startup_owner_type() if _pending is not None and startup_owner_type is not None else None
        if startup_owner is not None:
            _pending.bind_client(startup_owner)
        client = None
        try:
            try:
                options = {
                    "credentials": credentials,
                    "high_water_mark": high_water_mark,
                    "max_message_bytes": max_message_bytes,
                    "context_pool": context_pool,
                }
                if mode:
                    options["strict_single_connection"] = True
                if startup_owner is not None:
                    options["_startup_owner"] = startup_owner
                client = ZmqServiceClient(endpoint, expected_peer, **options)
            except ServiceTransportStartupCleanupError as error:
                if _pending is not None and startup_owner is None:
                    _pending.bind_client(error.transport)
                raise ServiceRuntimeStartupCleanupError(error.transport) from None
            except Exception:
                if not mode:
                    raise
                raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE") from None
            if _pending is not None and startup_owner is None:
                _pending.bind_client(client)
            try:
                self._require_factory_mode()
                if startup_owner is not None and startup_owner._client is not client:
                    raise ServiceRuntimeError("TRANSPORT_HANDOFF_INVALID")
                if mode:
                    binding = _strict_client_binding(client)
                    if (type(client) is not ZmqServiceClient
                            or type(binding) is not _ServiceSocketBinding
                            or binding is not client._socket_binding
                            or binding.strict_single_connection is not True
                            or client._strict_single_connection is not True
                            or not client._socket_binding_current(binding)):
                        raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE")
                adapter = _RequestNotSentAdapter(client, RequestNotSentError)
                self._require_factory_mode()
                if _pending is not None:
                    _pending.bind_adapter(client, adapter, startup_owner)
            except Exception:
                raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE") from None
            return adapter
        except BaseException:
            if client is not None and (_pending is None or not _pending.owns_client(client, startup_owner)):
                try:
                    client.close()
                except BaseException:
                    raise ServiceRuntimeStartupCleanupError(client) from None
            raise

    def _require_factory_mode(self) -> bool:
        self._require_owner_process()
        with _FACTORY_MODES_LOCK:
            saved = _FACTORY_MODES.get(id(self))
            if (saved is None or saved[0]() is not self or saved[2] != os.getpid()
                    or getattr(self, "_strict_single_connection", None) is not saved[1]):
                raise ServiceRuntimeError("STRICT_TRANSPORT_UNAVAILABLE")
            return saved[1]

    def _require_owner_process(self) -> None:
        if os.getpid() != self._owner_pid:
            raise ServiceRuntimeError("PROCESS_BOUNDARY")

    def _shared_context_pool(self, pool_type: type[Any]) -> Any:
        self._require_owner_process()
        context_pool = self._context_pool
        if context_pool is not None:
            return context_pool
        with self._context_pool_init_lock:
            self._require_owner_process()
            context_pool = self._context_pool
            if context_pool is None:
                context_pool = pool_type()
                self._context_pool = context_pool
        return context_pool


class _RequestNotSentAdapter:
    """Translate one exact backend contract without exposing it to the SDK."""

    def __init__(self, transport: RequestReplyTransport, error_type: type[BaseException]):
        self._transport = transport
        self._error_type = error_type

    @property
    def remaining_socket_message_ids(self) -> int | None:
        value = getattr(self._transport, "remaining_socket_message_ids", None)
        return value if type(value) is int and value >= 0 else None

    def request(self, request: WireEnvelope, *, timeout: float,
                deadline_monotonic: float | None = None) -> WireEnvelope:
        try:
            if request.header.operation == 'service.register':
                try:
                    from bt_api_transport_zmq import service_v2 as zmq_service
                except ImportError:
                    zmq_service = None
                client = self._transport
                strict_lookup = getattr(zmq_service, '_strict_client_binding', None)
                if (zmq_service is not None and type(client) is zmq_service.ZmqServiceClient
                        and callable(strict_lookup) and strict_lookup(client) is not None):
                    trusted = getattr(zmq_service, '_TRUSTED_CLIENT_REQUEST', None)
                    if trusted is None or zmq_service.ZmqServiceClient.request is not trusted or 'request' in vars(client):
                        raise ServiceRuntimeRequestNotSentError()
                    return trusted(client, request, timeout=timeout, deadline_monotonic=deadline_monotonic)
            if deadline_monotonic is None:
                return self._transport.request(request, timeout=timeout)
            return self._transport.request(request, timeout=timeout, deadline_monotonic=deadline_monotonic)
        except self._error_type:
            raise ServiceRuntimeRequestNotSentError() from None

    def _request_bound(self, request: WireEnvelope, *, timeout: float,
                       lineage: tuple, verify_owner) -> WireEnvelope:
        """Delegate only to the exact transport that owns this lineage."""
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        if type(self._transport) is not ZmqServiceClient or lineage[0] is not self._transport:
            raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
        try:
            return self._transport._request_bound(
                request, timeout=timeout, lineage=lineage, verify_owner=verify_owner
            )
        except self._error_type:
            raise ServiceRuntimeRequestNotSentError() from None

    def request_bound(self, request: WireEnvelope, *, timeout: float, deadline_monotonic: float,
                      transport: RequestReplyTransport, socket_binding: object,
                      _trusted_request: bool = False, _send_wall_deadline_ns: int | None = None) -> WireEnvelope:
        # Use the transport object which was checked, even if another thread
        # changes the adapter after this check. A replacement before this check
        # is rejected without entering either transport.
        if self._transport is not transport:
            raise ServiceRuntimeRequestNotSentError()
        from bt_api_transport_zmq.service_v2 import RequestNotSentError
        try:
            if _trusted_request:
                from bt_api_transport_zmq import service_v2 as zmq_service
                if (type(transport) is not zmq_service.ZmqServiceClient
                        or zmq_service.ZmqServiceClient.request is not zmq_service._TRUSTED_CLIENT_REQUEST
                        or 'request' in vars(transport)):
                    raise ServiceRuntimeRequestNotSentError()
                kwargs = {} if _send_wall_deadline_ns is None else {'_send_wall_deadline_ns': _send_wall_deadline_ns}
                return zmq_service._TRUSTED_CLIENT_REQUEST(transport, request, timeout=timeout,
                    deadline_monotonic=deadline_monotonic, socket_binding=socket_binding, **kwargs)
            if _send_wall_deadline_ns is not None:
                raise ServiceRuntimeRequestNotSentError()
            return transport.request(request, timeout=timeout, deadline_monotonic=deadline_monotonic,
                                     socket_binding=socket_binding)
        except RequestNotSentError as error:
            if type(error) is not RequestNotSentError:
                raise
            raise ServiceRuntimeRequestNotSentError() from None

    @property
    def last_reply_receipt(self):
        # The optional receipt is currently defined only by this exact ZMQ
        # backend. Other transports cannot supply claims via duck typing.
        try:
            from bt_api_transport_zmq.service_v2 import ZmqServiceClient
            if type(self._transport) is not ZmqServiceClient:
                return None
            return self._transport.last_reply_receipt
        except (ImportError, RuntimeError):
            return None

    def close(self) -> None:
        self._transport.close()


@dataclass(frozen=True)
class RegisteredBinding:
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    registration_id: str
    service_epoch: str
    operations: frozenset[str]
    expires_at: float
    policy_version: str
    requirements_version: str
    transport_ref: str
    timeout: float
    lease_deadline: float = 0.0
    remote_lease_inactive: bool = False
    renewal_due_at: float = 0.0
    required: bool = True
    heartbeat_interval_seconds: float | None = None
    active: bool = True
    access_policy_ref: str | None = None
    access_policy_version: str | None = None
    risk_policy_status: str | None = None
    risk_policy_ref: str | None = None
    risk_policy_version: str | None = None
    requirements_ref: str | None = None


def _bound_binding_values(binding: RegisteredBinding) -> tuple:
    """Copy routing fields for a local same-owner check, not an authority proof."""
    if type(binding) is not RegisteredBinding or type(binding.scope) is not WireScope:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    scope = tuple(vars(WireScope(**vars(binding.scope))).items())
    return tuple((key, scope if key == "scope" else value) for key, value in vars(binding).items())


def _bound_runtime_values(runtime: ServiceClientRuntime) -> tuple:
    if type(runtime.identity) is not WireIdentity:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    return (*tuple(vars(WireIdentity(**vars(runtime.identity))).items()), ("run_id", runtime.run_id))


@dataclass(frozen=True)
class _BoundSendFence:
    """Owner-local expectation for the actual send; this is not an authority token."""

    binding: RegisteredBinding
    binding_values: tuple
    runtime_values: tuple
    transport: object
    peer: WireIdentity
    fingerprint: str
    mount: str
    operation: str
    account_ref: str | None
    portfolio_ref: str | None
    scope_ref: str | None
    checked_at: float
    lease_deadline: float
    strict_facts: bool = False


@dataclass(frozen=True)
class _ExecutionUpstreamClockLease:
    runtime: object
    pid: int
    thread: int
    binding: RegisteredBinding
    identity: WireIdentity
    proof: tuple
    generation: object
    clock: _ExecutionSuspendClock
    deadline: int


@dataclass(frozen=True)
class ServiceMarketBindingView:
    """Validated, immutable market-transport details for the SDK facade."""

    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    registration_id: str
    service_epoch: str
    rpc_scope: WireScope
    operations: frozenset[str]
    transport_ref: str
    timeout: float
    peer: WireIdentity
    market_endpoint: str
    security_profile_ref: str
    high_water_mark: int
    max_message_bytes: int
    market_wire_version: int = 1


@dataclass(frozen=True)
class ServiceAccountBindingView:
    """Validated, immutable account-read routing details for the SDK facade."""

    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    account_ref: str
    registration_id: str
    service_epoch: str
    rpc_scope: WireScope
    operations: frozenset[str]
    transport_ref: str
    timeout: float
    max_message_bytes: int = MAX_WIRE_MESSAGE_BYTES


@dataclass(frozen=True)
class RegistrationSummary:
    bindings: tuple[RegisteredBinding, ...]
    degraded_count: int
    degradation_codes: tuple[str, ...] = ()


class ServiceClientRuntime:
    """A client session for a parsed strategy, application or service config.

    Scope/peer resolvers are trusted deployment wiring, not arbitrary Python
    import names from YAML. Their scope values are still requests: the server
    checks them against its authenticated grants on registration and every call.
    Calls and renewals execute on the creating transport thread.
    """

    _RENEWAL_FRACTION = 1.0 / 3.0
    _STARTUP_RPC_TIMEOUT_FRACTION = 0.5

    def __init__(
        self,
        config: ClientConfig | ServiceConfig,
        *,
        identity: WireIdentity,
        scope_resolver: Callable[[str], WireScope],
        peer_resolver: Callable[[str], WireIdentity],
        transport_factory: Callable[[Mapping[str, Any], WireIdentity], RequestReplyTransport],
        client_session_id: str | None = None,
        run_id: str | None = None,
        requested_operations: Mapping[str, frozenset[str]] | None = None,
        registration_mounts: frozenset[str] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        require_unregister_ack: bool = False,
        cleanup_unknown: Callable[[], bool] | None = None,
        on_cleanup_sticky: Callable[[], None] | None = None,
        execution_upstream_lease: bool = False,
    ) -> None:
        if type(config) not in (ClientConfig, ServiceConfig):
            raise ServiceRuntimeError("INVALID_CONFIG")
        self._config = config.to_public_dict()
        if registration_mounts is not None:
            if (type(config) is not ClientConfig or type(registration_mounts) is not frozenset
                    or not registration_mounts or any(type(mount) is not str for mount in registration_mounts)):
                raise ServiceRuntimeError("INVALID_REGISTRATION_MOUNTS")
            mounts = [binding["mount"] for client in self._config.get("clients", {}).values()
                      for binding in client["settings"]["bindings"]]
            if any(mount not in MOUNT_PORTS or mounts.count(mount) != 1 for mount in registration_mounts):
                raise ServiceRuntimeError("INVALID_REGISTRATION_MOUNTS")
        self._registration_mounts = registration_mounts
        expected_role = self._config.get("process_role")
        if expected_role is not None and identity.role.value != expected_role:
            raise ServiceRuntimeError("ROLE_MISMATCH")
        self.identity = identity
        self.client_session_id = client_session_id or uuid.uuid4().hex
        self.run_id = run_id or uuid.uuid4().hex
        if not all(
            isinstance(value, str) and 0 < len(value) <= 128
            for value in (self.client_session_id, self.run_id)
        ):
            raise ServiceRuntimeError("INVALID_SESSION_ID")
        self._scope_resolver = scope_resolver
        self._peer_resolver = peer_resolver
        self._factory = transport_factory
        if not callable(monotonic_clock):
            raise ServiceRuntimeError("INVALID_MONOTONIC_CLOCK")
        if type(execution_upstream_lease) is not bool:
            raise ServiceRuntimeError("INVALID_EXECUTION_UPSTREAM_CONFIGURATION")
        self._execution_upstream_lease_enabled = execution_upstream_lease
        self._monotonic_clock = monotonic_clock
        if type(require_unregister_ack) is not bool:
            raise ServiceRuntimeError("INVALID_CLEANUP_POLICY")
        self._require_unregister_ack = require_unregister_ack
        if (cleanup_unknown is None) != (on_cleanup_sticky is None) or (
            cleanup_unknown is not None
            and (not callable(cleanup_unknown) or not callable(on_cleanup_sticky))
        ):
            raise ServiceRuntimeError("INVALID_CLEANUP_POLICY")
        self._cleanup_unknown = cleanup_unknown
        self._on_cleanup_sticky = on_cleanup_sticky
        self._cleanup_sticky = False
        self._cleanup_in_progress = False
        self._cleanup_evidence: tuple[Any, ...] | None = None
        self._cleanup_reentrant_evidence: list[tuple[Any, ...]] = []
        self._cleanup_latch: dict[str, object] = {}
        self._start_in_progress = False
        self._registration_in_progress: dict[str, Any] | None = None
        self._owner_pid = os.getpid()
        self._owner_thread_id = threading.get_ident()
        self._observation_owner_pid = os.getpid()
        self._observation_owner_thread = threading.current_thread()
        self._requested_operations = dict(requested_operations or {})
        for mount, operations in self._requested_operations.items():
            if registration_mounts is not None and mount not in registration_mounts:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            if mount not in MOUNT_PORTS or type(operations) is not frozenset or not operations:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            if mount == "broker.group_monitor" and not operations <= _GROUP_MONITOR_OPERATIONS:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            ports = MOUNT_PORTS[mount] + OPTIONAL_MOUNT_PORTS.get(mount, ())
            allowed = frozenset().union(*(PORT_OPERATIONS[p] for p in ports))
            if not operations <= allowed:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            instrument_operations = operations & _INSTRUMENT_SPEC_OPERATIONS
            if instrument_operations and instrument_operations != _INSTRUMENT_SPEC_OPERATIONS:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
        self._transports: dict[tuple[str, str], RequestReplyTransport] = {}
        self._peers: dict[str, WireIdentity] = {}
        self._authenticated_registration_peers: dict[tuple[str, str, str, str], tuple[WireIdentity, str]] = {}
        # The registration proof also belongs to the exact transport object
        # which received it. None permanently poisons a replaced object for
        # that same registration key within this runtime instance.
        self._authenticated_registration_transports: dict[tuple[str, str, str, str], tuple[object, object, object] | None] = {}
        self._strict_registration_transports: dict[tuple[str, str, str, str], tuple[object, object, object]] = {}
        self._execution_upstream_clock = _ExecutionSuspendClock()
        self._execution_upstream_clock_leases = {}
        self._execution_upstream_observation = None
        self._execution_upstream_registration_nonce = None
        self._execution_upstream_nonce_failed = False
        self._execution_upstream_attempted_keys = set()
        self._live_parent_capabilities_lock = threading.RLock()
        self._live_parent_capabilities: dict[_LiveParentSourceCapability, tuple[Any, ...]] = {}
        self._recorded_parent_attempts: dict[object, tuple[Any, ...]] = {}
        self._live_parent_revisions: dict[tuple[Any, ...], int] = {}
        self._live_parent_source_identities: dict[tuple[Any, str], tuple[str, str]] = {}
        self._active_live_source_marker = None
        self._risk_first_receipts: dict[_RiskFirstWireReceipt, tuple[Any, ...]] = {}
        self._risk_first_capture_marker = None
        self._risk_first_send_context = None
        self._bindings: list[RegisteredBinding] = []
        self._registration_lineage: dict[int, tuple[Any, ...]] = {}
        self._registration_failure_generation: dict[tuple[str, str], int] = {}
        self._state = "new"
        self._startup_maintaining = False
        self._startup_rollback = False
        self._degraded = 0
        self._degradation_codes: list[str] = []
        self._cleanup_failures = 0
        # Values are deliberately unbounded: the client cannot prove server
        # expiry from a local timeout or from its own wall clock.
        self._unregister_uncertainties: dict[str, None] = {}
        self._unresolved_unregister_count = 0
        self._last_unresolved_registration_id: str | None = None
        self._unresolved_unregister_ids: list[str] = []
        self._unknown_registration_count = 0
        self._last_unknown_registration_key: str | None = None
        # Evidence kept for callers deciding whether this stopped runtime may be
        # replaced. ``_maintenance_code`` is the finite cause of the maintenance
        # failure; ``_remote_inactive_registrations`` holds only registrations
        # the registry itself refused as NOT_REGISTERED/STALE_EPOCH on renewal
        # (an authoritative reply, not a local timeout or elapsed lease).
        self._maintenance_code: str | None = None
        self._remote_inactive_registrations: dict[str, str] = {}

    @property
    def sticky_cleanup_enabled(self) -> bool:
        # Minimal pre-strict test/factory objects predate the optional cleanup
        # callback. Normal construction always initializes this field.
        return getattr(self, '_cleanup_unknown', None) is not None

    def _has_sticky_cleanup(self) -> bool:
        if self._cleanup_sticky:
            return True
        # Rooting may have completed in a finally block after the flag write
        # was interrupted. A retained runtime must never become retryable.
        with _RAW_STICKY_CLEANUP_ROOTS_LOCK:
            return _RAW_STICKY_CLEANUP_ROOTS.get(id(self)) is self

    @property
    def state(self) -> str:
        return self._state

    @property
    def cleanup_failures(self) -> int:
        return self._cleanup_failures

    @property
    def unregister_uncertainties(self) -> int:
        """Registrations that may remain live until an explicit server response."""

        return self._unresolved_unregister_count + len(self._unregister_uncertainties)

    @property
    def open_transport_count(self) -> int:
        """Transports still held; a nonzero value may hide an old writer path."""

        return len(self._transports)

    @property
    def min_socket_message_ids_remaining(self) -> int | None:
        """Smallest remaining socket message-id budget across open transports, if exposed."""

        remaining: list[int] = []
        for transport in self._transports.values():
            value = getattr(transport, "remaining_socket_message_ids", None)
            if type(value) is int and value >= 0:
                remaining.append(value)
        return min(remaining) if remaining else None

    @property
    def last_unresolved_registration_id(self) -> str | None:
        """Most recent registration ID no longer locally retryable, if any."""

        return self._last_unresolved_registration_id

    @property
    def unresolved_registration_ids(self) -> tuple[str, ...]:
        """Every registration whose release was never acknowledged.

        Folded (no longer retryable) ids come first, then ids still held in the
        pending mapping. The count equals :attr:`unregister_uncertainties`.
        """

        return tuple(self._unresolved_unregister_ids) + tuple(self._unregister_uncertainties)

    @property
    def maintenance_code(self) -> str | None:
        """Finite cause of the last maintenance failure, if the runtime stopped."""

        return self._maintenance_code

    @property
    def remote_inactive_registrations(self) -> tuple[tuple[str, str], ...]:
        """``(registration_id, service_epoch)`` pairs the registry proved inactive.

        A pair is recorded only when ``service.renew`` itself was definitively
        refused with NOT_REGISTERED or STALE_EPOCH. A local lease expiry, a
        transport timeout, or a rejected unregister is not this proof.
        """

        return tuple(self._remote_inactive_registrations.items())

    @property
    def lease_expiry_reliance_count(self) -> int:
        """Always zero: the client does not infer remote expiry from elapsed time."""

        return 0

    @property
    def unknown_registration_count(self) -> int:
        """Cumulative count of register requests whose result remains unknown."""

        return self._unknown_registration_count

    @property
    def last_unknown_registration_key(self) -> str | None:
        """Most recent opaque registration key with an unknown result, if any."""

        return self._last_unknown_registration_key

    @property
    def healthy(self) -> bool:
        if (self._state != "registered" or getattr(self, '_cleanup_in_progress', False)
                or (self.sticky_cleanup_enabled and self._has_sticky_cleanup())):
            return False
        now = self._monotonic_now()
        return not any(
            binding.active and binding.required and now >= binding.lease_deadline
            for binding in self._bindings
        )

    @property
    def bindings(self) -> tuple[RegisteredBinding, ...]:
        return tuple(self._bindings)

    def authenticated_peer_for_binding(self, binding: RegisteredBinding):
        """Return only a peer proven by this binding's authenticated ZMQ registration."""
        self._assert_owner_thread()
        self._strict_business_entry()
        if (type(binding) is not RegisteredBinding or not binding.active or binding not in self._bindings
                or not self.healthy or self._state != 'registered'):
            return None
        proof_key = (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)
        proof = self._authenticated_registration_peers.get(proof_key)
        if (proof is None or self._peers.get(binding.service_id) != proof[0]
                or proof[0].role is not WireRole.SERVICE or proof[0].delegate_ref is not None):
            return None
        transport = self._transports.get((binding.service_id, binding.transport_ref))
        try:
            from bt_api_transport_zmq.service_v2 import ZmqServiceClient
            if type(transport) is not _RequestNotSentAdapter or type(transport._transport) is not ZmqServiceClient:
                return None
            transport_proof = self._authenticated_registration_transports.get(proof_key)
            if (type(transport_proof) is not tuple or len(transport_proof) != 3
                    or transport_proof[0] is not transport or transport_proof[1] is not transport._transport
                    or not transport._transport._socket_binding_current(transport_proof[2])):
                return None
            server_key = transport._transport._server_key
            if type(server_key) is not bytes or hashlib.sha256(server_key).hexdigest() != proof[1]:
                return None
        except (ImportError, AttributeError):
            return None
        return proof

    def _has_current_registration_observation(self, binding: RegisteredBinding, *,
                                              _inflight: bool = False, _startup: bool = False) -> bool:
        """Observe local registration lineage, not remote liveness or authority."""
        if (os.getpid() != self._observation_owner_pid
                or threading.get_ident() != self._owner_thread_id
                or threading.current_thread() is not self._observation_owner_thread):
            return False
        proof = self._registration_lineage.get(id(binding))
        if proof is None or proof[0] is not binding:
            return False
        try:
            valid = (
                type(binding) is RegisteredBinding
                and ((not _startup and self._state == "registered" and self.healthy)
                     or (_startup and self._state == "starting" and self._startup_maintaining
                         and not self._startup_rollback
                         and self._monotonic_clock is _BOUND_MONOTONIC
                         and all(not item.active or not item.required
                                 or self._monotonic_now() < item.lease_deadline
                                 for item in self._bindings)))
                and any(item is binding for item in self._bindings)
                and binding.active and not binding.remote_lease_inactive
                and self._monotonic_now() < binding.lease_deadline
                and tuple(vars(binding).items()) == proof[1]
                and (self._registration_transport_is_current(proof[2], _inflight=True)
                     if _inflight else self._registration_transport_is_current(proof[2]))
                and proof[3].payload.get("result") is proof[5]
                and encode_envelope(proof[3]) == proof[6]
                and tuple(vars(proof[4]).items()) == proof[7]
                and self._renewal_lineage_is_current(proof, binding)
            )
        except Exception:
            valid = False
        if not valid:
            self._registration_lineage.pop(id(binding), None)
        return valid

    def _renewal_lineage_is_current(self, proof: tuple, binding: RegisteredBinding) -> bool:
        """Recheck the last validated renewal while retaining original registration evidence."""
        if len(proof) == 9:
            return True
        if len(proof) != 10:
            return False
        reply, receipt, result, encoded, receipt_fields, started = proof[9]
        self._validate_renewal_receipt(result, binding)
        return (
            reply.payload.get("result") is result
            and reply.header.operation == "service.renew"
            and receipt.operation == "service.renew"
            and reply.header.correlation_id == receipt.request_message_id
            and reply.header.message_id == receipt.reply_message_id
            and encode_envelope(reply) == encoded
            and tuple(vars(receipt).items()) == receipt_fields
            and binding.lease_deadline == started + self._lease_remaining(result)
        )

    def _registration_transport_is_current(self, proof: tuple[Any, ...], *, _inflight: bool = False) -> bool:
        """Check captured objects independently of the backend's last receipt."""
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient, _ServiceSocketBinding
        key, adapter, client, socket, context, generation, server_key, client_key, failures = proof
        socket_binding = client._socket_binding
        binding_current = (
            socket_binding == (socket, context, generation, server_key, client_key)
            or (type(socket_binding) is _ServiceSocketBinding
                and socket_binding.socket is socket
                and socket_binding.server_key == server_key
                and socket_binding.public_key == client_key
                and client._socket_binding_current(socket_binding))
        )
        return (
            self._transports.get(key) is adapter
            and self._registration_failure_generation.get(key, 0) == failures
            and type(adapter) is _RequestNotSentAdapter
            and adapter._transport is client and type(client) is ZmqServiceClient
            and client._owner_pid == self._observation_owner_pid
            and client._owner_thread == self._owner_thread_id
            and client._observation_owner_thread is self._observation_owner_thread
            and not client._closed and not client._close_pending and client._inflight is _inflight
            and not client._socket_retirement_pending
            and client._socket is socket and client._context is context
            and socket is not None and context is not None
            and not socket.closed and not context.closed
            and client._socket_generation == generation
            and client._server_key == server_key and client._public_key == client_key
            and client._fixed_curve_keys == (server_key, client_key)
            and binding_current
        )

    @staticmethod
    def _execution_upstream_key(binding):
        return (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)

    def _execution_upstream_live(self, token, binding, *, check_socket):
        from bt_api_transport_zmq.service_v2 import _ServiceSocketBinding, _SocketGeneration
        if (type(token) is not _ExecutionUpstreamClockLease or token.runtime is not self
                or token.pid != os.getpid() or token.thread != threading.get_ident()
                or token.binding is not binding or token.identity is not self.identity
                or token.clock is not self._execution_upstream_clock
                or self._owner_pid != token.pid or self._owner_thread_id != token.thread
                or type(token.proof) is not tuple or len(token.proof) != 3
                or type(token.proof[2]) is not _ServiceSocketBinding
                or type(token.generation) is not _SocketGeneration
                or token.proof[2]._generation is not token.generation or token.generation._revoked is not False
                or self._strict_registration_transports.get(self._execution_upstream_key(binding)) is not token.proof
                or self._authenticated_registration_transports.get(self._execution_upstream_key(binding)) is not token.proof
                or self._transports.get((binding.service_id, binding.transport_ref)) is not token.proof[0]
                or token.proof[0]._transport is not token.proof[1]
                or _ExecutionSuspendClock.now(token.clock) >= token.deadline):
            return False
        return not check_socket or token.proof[1]._socket_binding_current(token.proof[2])

    def _execution_upstream_lease_for(self, binding):
        """Verify strict local registration evidence outside a journal lock."""
        self._assert_owner_thread()
        try:
            token = self._execution_upstream_clock_leases.get(self._execution_upstream_key(binding))
            if (getattr(self, '_execution_upstream_lease_enabled', False) is not True
                    or type(binding) is not RegisteredBinding or binding.mount != 'upstream.direct_write'
                    or self.identity.role is not WireRole.SERVICE or not binding.active or not binding.required
                    or not any(item is binding for item in self._bindings)
                    or not self._execution_upstream_live(token, binding, check_socket=True)):
                return None
            return token
        except (AttributeError, ImportError, KeyError, TypeError, ValueError):
            return None

    def _publish_execution_upstream_lease(self, binding, timing, remaining, *, old=None):
        """Publish only a strict issued RPC observation; ordinary routes ignore it."""
        if (getattr(self, '_execution_upstream_lease_enabled', False) is not True
                or binding.mount != 'upstream.direct_write' or self.identity.role is not WireRole.SERVICE
                or not {'direct.parent.register', 'direct.parent.query'} <= binding.operations):
            return
        key = self._execution_upstream_key(binding)
        leases = getattr(self, '_execution_upstream_clock_leases', None)
        if type(leases) is not dict:
            return
        leases.pop(key, None)
        try:
            observation = timing.get('execution_control_observation')
            if observation is None or self._execution_upstream_observation is not observation:
                return
            self._execution_upstream_observation = None
            _, operation, request, reply, proof, started = observation
            result = reply.payload['result']
            if (type(started) is not int or type(remaining) not in (int, float)
                    or not math.isfinite(remaining) or remaining <= 0
                    or type(proof) is not tuple or len(proof) != 3
                    or operation not in ('service.register', 'service.renew')
                    or (operation == 'service.register' and
                        timing.get('execution_first_registration_attempt') is not True)
                    or (operation == 'service.renew') != (old is not None)
                    or request.header.identity is not self.identity or request.header.scope != binding.scope
                    or reply.header.correlation_id != request.header.message_id
                    or result.get('registration_id') != binding.registration_id
                    or result.get('service_epoch') != binding.service_epoch
                    or self._lease_remaining(result) != remaining
                    or self._strict_registration_transports.get(key) is not proof
                    or self._authenticated_registration_transports.get(key) is not proof
                    or (old is not None and (old.proof is not proof
                        or not self._execution_upstream_live(old, old.binding, check_socket=True)))):
                return
            duration_ns = _execution_ttl_ns(remaining)
            deadline = started + duration_ns
            if not 0 < duration_ns < 2**63 or deadline >= 2**63 or _ExecutionSuspendClock.now(self._execution_upstream_clock) >= deadline:
                return
            generation = proof[2]._generation
            token = _ExecutionUpstreamClockLease(self, self._owner_pid, self._owner_thread_id,
                binding, self.identity, proof, generation, self._execution_upstream_clock, deadline)
            if self._execution_upstream_live(token, binding, check_socket=True):
                leases[key] = token
        except (AttributeError, ImportError, KeyError, TypeError, ValueError, OverflowError):
            return

    def market_binding_view(
        self, mount: str, *, required_operations: frozenset[str]
    ) -> ServiceMarketBindingView:
        """Return a trusted frozen profile view for one registered market mount."""

        self._assert_owner_thread()
        self._strict_business_entry()
        self._require_healthy()
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or "service.market_source" not in MOUNT_PORTS[mount]
        ):
            raise ServiceRuntimeError("INVALID_MARKET_MOUNT")
        if (
            type(required_operations) is not frozenset
            or not required_operations
            or not required_operations <= PORT_OPERATIONS["service.market_source"]
        ):
            raise ServiceRuntimeError("INVALID_MARKET_OPERATIONS")
        matches = [
            binding for binding in self._bindings if binding.active and binding.mount == mount
        ]
        if len(matches) != 1 or not required_operations <= matches[0].operations:
            raise ServiceRuntimeError("MARKET_BINDING_UNAVAILABLE")
        binding = matches[0]
        peer = self._peers.get(binding.service_id)
        profile = self._config["communication"]["profiles"].get(binding.transport_ref)
        if type(peer) is not WireIdentity or peer.role is not WireRole.SERVICE:
            raise ServiceRuntimeError("INVALID_PEER")
        if not isinstance(profile, Mapping) or profile.get("backend") != "zmq":
            raise ServiceRuntimeError("UNKNOWN_BACKEND")
        endpoints = profile.get("endpoints")
        options = profile.get("options")
        endpoint = endpoints.get("market_connect") if isinstance(endpoints, Mapping) else None
        security_ref = profile.get("security_profile_ref")
        high_water_mark = options.get("high_water_mark") if isinstance(options, Mapping) else None
        max_message_bytes = (
            options.get("max_message_bytes") if isinstance(options, Mapping) else None
        )
        market_wire_version = (
            options.get("market_wire_version", 1) if isinstance(options, Mapping) else None
        )
        if not isinstance(endpoint, str) or not endpoint:
            raise ServiceRuntimeError("MARKET_ENDPOINT_REQUIRED")
        if not isinstance(security_ref, str) or not security_ref:
            raise ServiceRuntimeError("SECURITY_PROFILE_UNAVAILABLE")
        if type(high_water_mark) is not int or high_water_mark <= 0:
            raise ServiceRuntimeError("INVALID_MARKET_TRANSPORT_OPTIONS")
        if type(max_message_bytes) is not int or max_message_bytes <= 0:
            raise ServiceRuntimeError("INVALID_MARKET_TRANSPORT_OPTIONS")
        if type(market_wire_version) is not int or market_wire_version not in (1, 2):
            raise ServiceRuntimeError("MARKET_WIRE_VERSION_MISMATCH")
        return ServiceMarketBindingView(
            mount=binding.mount,
            service_id=binding.service_id,
            instance_id=binding.instance_id,
            scope_ref=binding.scope_ref,
            registration_id=binding.registration_id,
            service_epoch=binding.service_epoch,
            rpc_scope=binding.scope,
            operations=binding.operations,
            transport_ref=binding.transport_ref,
            timeout=binding.timeout,
            peer=peer,
            market_endpoint=endpoint,
            security_profile_ref=security_ref,
            high_water_mark=high_water_mark,
            max_message_bytes=max_message_bytes,
            market_wire_version=market_wire_version,
        )

    def account_binding_view(
        self,
        mount: str,
        account_ref: str,
        *,
        required_operations: frozenset[str],
    ) -> ServiceAccountBindingView:
        """Return a trusted frozen view for one registered account-read mount."""

        self._assert_owner_thread()
        self._strict_business_entry()
        self._require_healthy()
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or "service.account_read" not in MOUNT_PORTS[mount]
        ):
            raise ServiceRuntimeError("INVALID_ACCOUNT_MOUNT")
        if type(account_ref) is not str or not account_ref:
            raise ServiceRuntimeError("INVALID_ACCOUNT_REF")
        if (
            type(required_operations) is not frozenset
            or not required_operations
            or not required_operations <= PORT_OPERATIONS["service.account_read"]
        ):
            raise ServiceRuntimeError("INVALID_ACCOUNT_OPERATIONS")
        matches = [
            binding
            for binding in self._bindings
            if binding.active
            and binding.mount == mount
            and binding.scope.account_ref == account_ref
        ]
        if len(matches) != 1 or not required_operations <= matches[0].operations:
            raise ServiceRuntimeError("ACCOUNT_BINDING_UNAVAILABLE")
        binding = matches[0]
        max_message_bytes = self._profile_max_message_bytes(binding.transport_ref)
        return ServiceAccountBindingView(
            mount=binding.mount,
            service_id=binding.service_id,
            instance_id=binding.instance_id,
            scope_ref=binding.scope_ref,
            account_ref=account_ref,
            registration_id=binding.registration_id,
            service_epoch=binding.service_epoch,
            rpc_scope=binding.scope,
            operations=binding.operations,
            transport_ref=binding.transport_ref,
            timeout=binding.timeout,
            max_message_bytes=max_message_bytes,
        )

    def start(self) -> RegistrationSummary:
        self._assert_owner_thread()
        self._strict_public_lifecycle_entry()
        if self._state == "registered":
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        if self._state != "new":
            raise ServiceRuntimeError("SESSION_NOT_NEW")
        self._start_in_progress = self.sticky_cleanup_enabled
        self._state = "starting"
        try:
            for client in self._config.get("clients", {}).values():
                for binding in client["settings"]["bindings"]:
                    if (self._registration_mounts is not None
                            and binding["mount"] not in self._registration_mounts):
                        continue
                    first_binding = len(self._bindings)
                    try:
                        options = client["settings"].get("options", {})
                        timeout_ms = options.get("request_timeout_ms")
                        heartbeat_interval = options.get("heartbeat_interval_seconds")
                        self._register(
                            binding,
                            timeout_ms=timeout_ms,
                            heartbeat_interval_seconds=heartbeat_interval,
                        )
                        self._strict_start_check()
                        self._startup_maintain_if_due()
                    except Exception as error:
                        if isinstance(error, ServiceRuntimeStartupCleanupError):
                            raise
                        if self._state != "starting":
                            raise
                        if (
                            isinstance(error, ServiceRuntimeError)
                            and error.code
                            in {"INVALID_MARKET_CAPABILITY", "MARKET_WIRE_VERSION_MISMATCH"}
                        ):
                            raise
                        if binding.get("required", True):
                            if isinstance(error, ServiceRuntimeError):
                                raise
                            failure = ServiceRuntimeError("REQUIRED_REGISTRATION_FAILED")
                            # Finite cause: the exception class name only (for
                            # example a response timeout), never its text.
                            failure.result = {"cause_type": type(error).__name__}
                            raise failure from None
                        self._release_during_startup(first_binding)
                        self._degraded += 1
                        self._degradation_codes.append(
                            error.code
                            if isinstance(error, ServiceRuntimeError)
                            else "SERVICE_UNAVAILABLE"
                        )
            self._startup_maintain_if_due()
            self._startup_check_required_leases()
            self._strict_start_check()
            self._state = "registered"
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        except BaseException as error:
            # Startup can be interrupted after a transport or registration has
            # been acquired. Try every local close, and retain any transport
            # whose owner-thread cleanup did not complete.
            self._startup_rollback = True
            try:
                if not (self.sticky_cleanup_enabled and self._has_sticky_cleanup()):
                    try:
                        self._release(capture_base_exceptions=True)
                    except BaseException:
                        if self.sticky_cleanup_enabled and not isinstance(error, Exception):
                            raise error
                        raise
            finally:
                self._startup_rollback = False
            if self.sticky_cleanup_enabled and self._has_sticky_cleanup():
                self._state = "close_failed"
                if not isinstance(error, Exception):
                    raise
                raise ServiceRuntimeError("CLOSE_FAILED") from None
            self._state = "close_failed" if self._transports else "failed"
            if self._transports:
                raise ServiceRuntimeError("CLOSE_FAILED") from None
            raise
        finally:
            self._start_in_progress = False

    def call(
        self,
        mount: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
        include_receive_receipt: bool = False,
        _expected_binding: RegisteredBinding | None = None,
        _expected_binding_values: tuple | None = None,
        _expected_runtime_values: tuple | None = None,
        _expected_transport: object | None = None,
        _expected_peer: WireIdentity | None = None,
        _expected_fingerprint: str | None = None,
        _require_strict_facts: bool = False,
    ) -> Mapping[str, Any] | tuple[Mapping[str, Any], object | None]:
        self._assert_owner_thread()
        self._strict_business_entry()
        if operation in (_ACCOUNT_BOUND_OPERATION, _MANAGEMENT_BOUND_OPERATION,
                         _MANAGEMENT_PLAN_BOUND_OPERATION, _MANAGEMENT_BY_REQUEST_BOUND_OPERATION,
                         _MANAGEMENT_DISCOVERY_BOUND_OPERATION,
                         _MONITOR_QUERY_BOUND_OPERATION):
            raise ServiceRuntimeRequestNotSentError()
        if type(include_receive_receipt) is not bool:
            raise ServiceRuntimeError("INVALID_RECEIPT_REQUEST")
        if (type(_require_strict_facts) is not bool or (_require_strict_facts and
                (_expected_binding is None or operation not in _ACCOUNT_FACTS_BOUND_OPERATIONS))):
            raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
        self._require_healthy()
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        if max_reply_message_bytes is not None and (
            type(max_reply_message_bytes) is not int
            or not 1 <= max_reply_message_bytes <= MAX_WIRE_MESSAGE_BYTES
        ):
            raise ServiceRuntimeError("INVALID_REPLY_MESSAGE_LIMIT")
        matches = [
            b
            for b in self._bindings
            if b.active
            and b.mount == mount
            and operation in b.operations
            and (account_ref is None or b.scope.account_ref == account_ref)
            and (portfolio_ref is None or b.scope.portfolio_ref == portfolio_ref)
            and (scope_ref is None or b.scope_ref == scope_ref)
        ]
        if len(matches) != 1:
            raise ServiceRuntimeError("AMBIGUOUS_OR_MISSING_ROUTE")
        binding = matches[0]
        # Owner-bound calls may only use the exact binding inspected by their
        # owner immediately before this selection.  A caller-provided object is
        # not authority: normal selection, lease and transport checks still run.
        if _expected_binding is not None and binding is not _expected_binding:
            raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
        if operation not in binding.operations:
            raise ServiceRuntimeError("ACTION_NOT_GRANTED")
        reply_limit = max_reply_message_bytes
        tracked_account_read = (
            operation == "account.read_after"
            and isinstance(arguments, Mapping)
            and arguments.get("track_delivery") is True
        )
        if reply_limit is not None or tracked_account_read or operation == "direct.events.read_after":
            profile_limit = self._profile_max_message_bytes(binding.transport_ref)
            reply_limit = profile_limit if reply_limit is None else min(profile_limit, reply_limit)
        checked_at = self._monotonic_now()
        remaining = binding.lease_deadline - checked_at
        if remaining <= 0:
            self._expire_binding(binding)
            self._require_healthy()
            raise ServiceRuntimeError("LEASE_EXPIRED")
        payload = {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": dict(arguments),
        }
        if reply_limit is not None:
            payload["max_reply_message_bytes"] = reply_limit
        if _expected_binding is not None:
            try:
                final_matches = [
                    b for b in self._bindings
                    if b.active and b.mount == mount and operation in b.operations
                    and (account_ref is None or b.scope.account_ref == account_ref)
                    and (portfolio_ref is None or b.scope.portfolio_ref == portfolio_ref)
                    and (scope_ref is None or b.scope_ref == scope_ref)
                ]
                if (self._state != "registered"
                        or len(final_matches) != 1 or final_matches[0] is not _expected_binding
                        or binding is not _expected_binding
                        or _bound_binding_values(binding) != _expected_binding_values
                        or _bound_runtime_values(self) != _expected_runtime_values
                        or self._transports.get((binding.service_id, binding.transport_ref)) is not _expected_transport
                        or self._peers.get(binding.service_id) != _expected_peer
                        or self._authenticated_registration_peers.get(
                            (binding.service_id, binding.transport_ref,
                             binding.registration_id, binding.service_epoch)
                        ) != (_expected_peer, _expected_fingerprint)):
                    raise ValueError
            except Exception:
                raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE") from None
        bound_kwargs = {}
        if _expected_binding is not None:
            bound_kwargs["_bound_send_fence"] = _BoundSendFence(
                _expected_binding, _expected_binding_values, _expected_runtime_values,
                _expected_transport, _expected_peer, _expected_fingerprint,
                mount, operation, account_ref, portfolio_ref, scope_ref,
                checked_at, binding.lease_deadline, _require_strict_facts,
            )
        return self._rpc(
            binding.service_id,
            binding.transport_ref,
            binding.scope,
            operation,
            payload,
            control=False,
            timeout=min(binding.timeout, remaining * 0.8),
            include_receive_receipt=include_receive_receipt,
            **bound_kwargs,
        )

    def call_bound(
        self,
        target: BoundServiceTarget | BoundAccountServiceTarget | BoundExecutionParentTarget | BoundExecutionManagementReadTarget | BoundMonitorQueryTarget,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        deadline_monotonic: float,
    ) -> Mapping[str, Any]:
        return self._call_bound(target, operation, arguments, deadline_monotonic=deadline_monotonic)

    def _call_bound(
        self,
        target: BoundServiceTarget | BoundAccountServiceTarget | BoundExecutionParentTarget | BoundExecutionManagementReadTarget | BoundMonitorQueryTarget,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        deadline_monotonic: float,
        _require_issued_receipt: bool = False,
        _source_capture_marker: object | None = None,
        _risk_first_marker: object | None = None,
    ) -> Mapping[str, Any]:
        """Use one authenticated registration without selecting its route twice.

        ``target`` is trusted composition input, not an authorization grant.
        The deadline must come from the same real ``time.monotonic`` clock as
        the ZMQ transport. Existing direct clients do not use this method.
        """
        self._assert_owner_thread()
        if (type(operation) is str and operation == 'direct.risk_first_reserve.v2'
                and (_risk_first_marker is None
                     or _risk_first_marker is not self._risk_first_capture_marker
                     or _require_issued_receipt is not True)):
            raise ServiceRuntimeRequestNotSentError()
        try:
            valid_deadline = (type(deadline_monotonic) in (int, float) and math.isfinite(deadline_monotonic))
        except OverflowError:
            valid_deadline = False
        if (type(target) not in (BoundServiceTarget, BoundAccountServiceTarget, BoundExecutionParentTarget,
                                 BoundExecutionManagementReadTarget, BoundMonitorQueryTarget) or type(operation) is not str
                or not isinstance(arguments, Mapping) or not valid_deadline):
            raise ServiceRuntimeRequestNotSentError()
        try:
            # ``bindings`` and the caller's target are publicly observable.
            # Retain a distinct value snapshot even for direct owner calls so
            # mutation of either object cannot retarget the outgoing envelope.
            target = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                             expected_peer=replace(target.expected_peer))
            from bt_api_py.service_runtime_thread import _copy_wire
            arguments = _copy_wire(arguments)
            if type(arguments) is not dict:
                raise ValueError
            _validate_bound_target_operation(target, operation)
            runtime_run_id = (target.runtime_run_id if type(target) in
                              (BoundAccountServiceTarget, BoundExecutionManagementReadTarget,
                               BoundMonitorQueryTarget) else target.run_id)
            if (self._monotonic_clock is not _BOUND_MONOTONIC or not _bound_clock_current()
                    or self._state != 'registered'
                    or not self.healthy or self.identity != target.identity or self.run_id != runtime_run_id
                    or operation not in PORT_OPERATIONS[target.port]
                    or _BOUND_MONOTONIC() >= deadline_monotonic):
                raise ValueError
            matches = [binding for binding in self._bindings
                       if type(binding) is RegisteredBinding and binding.active is True
                       and binding.required is True and binding.mount == target.mount
                       and binding.service_id == target.service_id and binding.instance_id == target.instance_id
                       and binding.scope_ref == target.scope_ref and binding.scope == target.scope
                       and binding.registration_id == target.registration_id
                       and binding.service_epoch == target.service_epoch
                       and binding.transport_ref == target.transport_ref and operation in binding.operations]
            if len(matches) != 1:
                raise ValueError
            binding = matches[0]
            profile = self._config['communication']['profiles'][target.transport_ref]
            if (type(profile) is not dict or profile.get('backend') != 'zmq'
                    or profile.get('security_profile_ref') != target.security_profile_ref
                    or profile.get('endpoints', {}).get('rpc_connect') != target.endpoint
                    or type(self._factory) is not ZmqTransportFactory
                    or self._factory._owner_pid != os.getpid()):
                raise ValueError
            keys = self._factory._profiles[target.security_profile_ref]
            from bt_api_transport_zmq import service_v2 as zmq_service
            from bt_api_transport_zmq.service_v2 import RequestNotSentError, ZmqServiceClient
            adapter = self._transports[(target.service_id, target.transport_ref)]
            registration_key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
            registration_transport = self._authenticated_registration_transports.get(registration_key)
            if (not _bound_clock_current()
                    or type(keys) is not ClientCurveKeys or type(keys.server_key) is not bytes
                    or type(adapter) is not _RequestNotSentAdapter or adapter._error_type is not RequestNotSentError
                    or type(adapter._transport) is not ZmqServiceClient
                    or type(registration_transport) is not tuple or len(registration_transport) != 3
                    or registration_transport[0] is not adapter or registration_transport[1] is not adapter._transport
                    or not adapter._transport._socket_binding_current(registration_transport[2])
                    or self.authenticated_peer_for_binding(binding) != (target.expected_peer, target.server_key_fingerprint)
                    or hashlib.sha256(keys.server_key).hexdigest() != target.server_key_fingerprint
                    or adapter._transport._server_key != keys.server_key
                    or adapter._transport._public_key != keys.public_key
                    or adapter._transport._secret_key != keys.secret_key
                    or adapter._transport._endpoint != target.endpoint
                    or adapter._transport._identity != target.expected_peer):
                raise ValueError
            if type(target) in (BoundExecutionParentTarget, BoundExecutionManagementReadTarget):
                from bt_api_transport_zmq.service_v2 import _strict_client_binding
                strict = self._strict_registration_transports.get(registration_key)
                if (type(strict) is not tuple or len(strict) != 3
                        or any(strict[index] is not registration_transport[index] for index in range(3))
                        or _strict_client_binding(adapter._transport) is not registration_transport[2]
                        or registration_transport[2].strict_single_connection is not True):
                    raise ValueError
            now = _BOUND_MONOTONIC()
            final_deadline = min(float(deadline_monotonic), binding.lease_deadline)
            remaining = final_deadline - now
            if remaining <= 0:
                raise ValueError
            payload = {'registration_id': target.registration_id, 'service_epoch': target.service_epoch,
                       'instance_id': target.instance_id, 'arguments': dict(arguments)}
        except Exception:
            # No transport method has been entered yet. Avoid exposing which
            # credential, route, or registration condition failed.
            raise ServiceRuntimeRequestNotSentError() from None
        parent_target = type(target) in (BoundExecutionParentTarget, BoundExecutionManagementReadTarget)
        result = self._rpc(
            target.service_id, target.transport_ref, target.scope, operation, payload,
            control=False, timeout=min(binding.timeout, remaining),
            bound_transport=adapter, bound_client=registration_transport[1],
            bound_socket_binding=registration_transport[2],
            bound_peer=target.expected_peer, bound_identity=target.identity,
            deadline_monotonic=final_deadline,
            _require_issued_receipt=_require_issued_receipt or parent_target,
            _source_capture_marker=_source_capture_marker,
            _risk_first_capture_marker=_risk_first_marker,
        )
        # The parent path returns only the validated value to its caller. The
        # issued receipt also rechecks registration/socket continuity at reply.
        return result[0] if parent_target else result

    def _risk_first_binding(self, target: BoundServiceTarget):
        """Inspect the exact strict registration and socket on this owner only."""
        try:
            self._assert_owner_thread()
            if (type(target) is not BoundServiceTarget or target.mount != 'upstream.direct_write'
                    or target.port != 'service.direct_write' or target.identity.role is not WireRole.SERVICE
                    or self._state != 'registered' or not self.healthy or self.identity != target.identity
                    or self.run_id != target.run_id or self._monotonic_clock is not _BOUND_MONOTONIC
                    or not _bound_clock_current() or type(self._factory) is not ZmqTransportFactory
                    or self._factory._require_factory_mode() is not True):
                raise ValueError
            BoundServiceTarget.__post_init__(target)
            key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
            proof = self._strict_registration_transports.get(key)
            if (type(proof) is not tuple or len(proof) != 3
                    or self._authenticated_registration_transports.get(key) is not proof
                    or self._transports.get((target.service_id, target.transport_ref)) is not proof[0]):
                raise ValueError
            adapter, client, socket_binding = proof
            from bt_api_transport_zmq.service_v2 import (
                RequestNotSentError, ZmqServiceClient, _ServiceSocketBinding, _strict_client_binding,
            )
            if (type(adapter) is not _RequestNotSentAdapter or type(client) is not ZmqServiceClient
                    or adapter._error_type is not RequestNotSentError or adapter._transport is not client
                    or type(socket_binding) is not _ServiceSocketBinding
                    or socket_binding.strict_single_connection is not True
                    or _strict_client_binding(client) is not socket_binding
                    or not client._socket_binding_current(socket_binding)):
                raise ValueError
            profile = self._config['communication']['profiles'][target.transport_ref]
            keys = self._factory._profiles[target.security_profile_ref]
            if (type(profile) is not dict or profile.get('backend') != 'zmq'
                    or profile.get('security_profile_ref') != target.security_profile_ref
                    or profile.get('endpoints', {}).get('rpc_connect') != target.endpoint
                    or type(keys) is not ClientCurveKeys
                    or hashlib.sha256(keys.server_key).hexdigest() != target.server_key_fingerprint
                    or client._server_key != keys.server_key or client._public_key != keys.public_key
                    or client._secret_key != keys.secret_key or client._identity != target.expected_peer
                    or client._endpoint != target.endpoint):
                raise ValueError
            matches = [binding for binding in self._bindings if type(binding) is RegisteredBinding
                       and binding.active is True and binding.required is True
                       and binding.mount == target.mount and binding.service_id == target.service_id
                       and binding.instance_id == target.instance_id and binding.scope_ref == target.scope_ref
                       and binding.scope == target.scope and binding.registration_id == target.registration_id
                       and binding.service_epoch == target.service_epoch and binding.transport_ref == target.transport_ref
                       and 'direct.risk_first_reserve.v2' in binding.operations
                       and binding.lease_deadline > _BOUND_MONOTONIC()]
            if len(matches) != 1 or self.authenticated_peer_for_binding(matches[0]) != (
                    target.expected_peer, target.server_key_fingerprint):
                raise ValueError
            return proof, matches[0], replace(matches[0], scope=replace(matches[0].scope))
        except Exception:
            raise ServiceRuntimeRequestNotSentError() from None

    def _capture_risk_first(self, bridge, attempt, target, request, payload, *, deadline_monotonic):
        """Exchange one registered attempt for an opaque, strict socket observation."""
        from bt_api_base.gateway_risk_first_reserve_v2 import (
            GatewayRiskFirstReserveRequestV2, GatewayRiskFirstReserveResultV2,
        )
        from bt_api_py.risk_gateway_first_reserve_client import RiskGatewayFirstReserveBridge
        from bt_api_py.service_runtime_thread import _copy_wire
        self._assert_owner_thread()
        if type(bridge) is not RiskGatewayFirstReserveBridge:
            raise ServiceRuntimeRequestNotSentError()
        bridge._take_send(attempt, self, target, request, payload)
        proof, binding, binding_snapshot = self._risk_first_binding(target)
        target_snapshot = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                                  expected_peer=replace(target.expected_peer))
        request = GatewayRiskFirstReserveRequestV2.from_mapping(request.to_mapping())
        payload_copy = _copy_wire(payload)
        from bt_api_py.risk_first_payload_consistency import validate_risk_first_payload_consistency
        validate_risk_first_payload_consistency(request, payload_copy)
        arguments = {'request': request.to_mapping(), 'payload': payload_copy}
        encoded_arguments = encode_wire_value(arguments)
        bridge._check_sent(attempt, self, target, bridge._request_snapshot, payload)
        # Revalidate after projection and before entering the transport.  The
        # Registry scope and ledger transaction both end before network I/O.
        bridge._check_attempt(attempt)
        marker = object()
        if self._risk_first_capture_marker is not None or self._risk_first_send_context is not None:
            raise ServiceRuntimeRequestNotSentError()
        try:
            self._risk_first_capture_marker = marker
            self._risk_first_send_context = (marker, bridge, attempt, encoded_arguments)
            started = _BOUND_MONOTONIC()
            raw, receipt = self._call_bound(
                target_snapshot, 'direct.risk_first_reserve.v2',
                arguments,
                deadline_monotonic=deadline_monotonic, _require_issued_receipt=True,
                _risk_first_marker=marker,
            )
        finally:
            self._risk_first_capture_marker = None
            self._risk_first_send_context = None
        try:
            raw = _copy_wire(raw)
            if (type(raw) is not dict or set(raw) != {'status', 'result'}
                    or type(raw['status']) is not str or raw['status'] != 'RESERVED'
                    or type(raw['result']) is not dict):
                raise ValueError
            result = GatewayRiskFirstReserveResultV2.from_mapping_for_request(raw['result'], request)
            bridge._check_sent(attempt, self, target, bridge._request_snapshot, payload)
            bridge._check_attempt(attempt)
            current, live_binding, live_snapshot = self._risk_first_binding(target_snapshot)
            if (any(current[index] is not proof[index] for index in range(3))
                    or live_binding is not binding or live_snapshot != binding_snapshot
                    or _BOUND_MONOTONIC() >= deadline_monotonic):
                raise ValueError
            ticket = _RiskFirstWireReceipt()
            self._risk_first_receipts[ticket] = (bridge, attempt, target_snapshot, request, result,
                                                 proof, binding, binding_snapshot, started,
                                                 float(deadline_monotonic))
            return ticket
        except Exception:
            raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.risk_first_reserve.v2') from None

    def _consume_risk_first(self, bridge, attempt, ticket, marker):
        """Consume a receipt once after same-owner strict continuity checks."""
        from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveResultV2
        self._assert_owner_thread()
        entry = self._risk_first_receipts.pop(ticket, None) if type(ticket) is _RiskFirstWireReceipt else None
        try:
            if type(entry) is not tuple or len(entry) != 10 or entry[0] is not bridge or entry[1] is not attempt:
                raise ValueError
            bridge._check_consume_marker(marker)
            _, _, target, request, result, proof, binding, binding_snapshot, started, deadline = entry
            current, live_binding, live_snapshot = self._risk_first_binding(target)
            now = _BOUND_MONOTONIC()
            if (any(current[index] is not proof[index] for index in range(3))
                    or live_binding is not binding or live_snapshot != binding_snapshot
                    or not started <= now < deadline):
                raise ValueError
            remaining = result.remaining_ttl_ms - math.ceil((now - started) * 1000)
            if remaining < 1:
                raise ValueError
            mapped = result.to_mapping()
            mapped['remaining_ttl_ms'] = remaining
            return GatewayRiskFirstReserveResultV2.from_mapping_for_request(mapped, request)
        except Exception:
            raise ServiceCallOutcomeUnknownError('UNKNOWN', 'direct.risk_first_reserve.v2') from None

    def _live_parent_binding(self, target: BoundServiceTarget) -> tuple[Any, Any, Any]:
        """Recheck the exact issuer-verified registration on its owner thread."""
        try:
            self._assert_owner_thread()
            if (type(target) is not BoundServiceTarget or target.port != 'service.direct_write'
                    or target.identity.role is not WireRole.SERVICE or self._state != 'registered'
                    or not self.healthy or self.identity != target.identity or self.run_id != target.run_id
                    or self._monotonic_clock is not _BOUND_MONOTONIC or not _bound_clock_current()
                    or type(self._factory) is not ZmqTransportFactory or self._factory._require_factory_mode() is not True):
                raise ValueError
            BoundServiceTarget.__post_init__(target)
            key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
            proof = self._strict_registration_transports.get(key)
            if (type(proof) is not tuple or len(proof) != 3
                    or self._authenticated_registration_transports.get(key) != proof
                    or self._transports.get((target.service_id, target.transport_ref)) is not proof[0]):
                raise ValueError
            adapter, client, socket_binding = proof
            from bt_api_transport_zmq.service_v2 import (
                ZmqServiceClient, _ServiceSocketBinding, _strict_client_binding,
            )
            if (type(adapter) is not _RequestNotSentAdapter or type(client) is not ZmqServiceClient
                    or adapter._transport is not client or type(socket_binding) is not _ServiceSocketBinding
                    or _strict_client_binding(client) is not socket_binding
                    or not client._socket_binding_current(socket_binding)):
                raise ValueError
            profile = self._config['communication']['profiles'][target.transport_ref]
            keys = self._factory._profiles[target.security_profile_ref]
            if (type(profile) is not dict or profile.get('backend') != 'zmq'
                    or profile.get('security_profile_ref') != target.security_profile_ref
                    or profile.get('endpoints', {}).get('rpc_connect') != target.endpoint
                    or type(keys) is not ClientCurveKeys
                    or hashlib.sha256(keys.server_key).hexdigest() != target.server_key_fingerprint
                    or client._server_key != keys.server_key or client._public_key != keys.public_key
                    or client._secret_key != keys.secret_key or client._identity != target.expected_peer
                    or client._endpoint != target.endpoint):
                raise ValueError
            matches = [b for b in self._bindings if type(b) is RegisteredBinding and b.active is True
                       and b.required is True and b.mount == target.mount and b.service_id == target.service_id
                       and b.instance_id == target.instance_id and b.scope_ref == target.scope_ref
                       and b.scope == target.scope and b.registration_id == target.registration_id
                       and b.service_epoch == target.service_epoch and b.transport_ref == target.transport_ref
                       and {'direct.parent.source', 'direct.parent.register_v2'} <= b.operations
                       and b.lease_deadline > _BOUND_MONOTONIC()]
            if len(matches) != 1 or self.authenticated_peer_for_binding(matches[0]) != (
                    target.expected_peer, target.server_key_fingerprint):
                raise ValueError
            return proof
        except Exception:
            raise ServiceRuntimeRequestNotSentError() from None

    @staticmethod
    def _live_parent_spec(target: BoundServiceTarget, spec: Any) -> tuple[dict[str, Any], str]:
        try:
            from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
            if type(spec) is not dict:
                raise ValueError
            canonical = canonical_gateway_payload(spec)
            copied = json.loads(canonical)
            scope = target.scope
            if (type(copied) is not dict or target.identity.role is not WireRole.SERVICE
                    or copied.get('account_scope') != scope.account_ref
                    or copied.get('strategy_scope') != scope.strategy_ref
                    or copied.get('execution_domain_ref') != scope.execution_domain_ref
                    or any(type(copied.get(k)) is not str or not copied[k] or copied[k] != copied[k].strip()
                           or not copied[k].isprintable() or len(copied[k].encode('utf-8', 'strict')) > 256
                           for k in ('account_scope', 'strategy_scope', 'execution_domain_ref',
                                     'parent_ref', 'registration_operation_id'))):
                raise ValueError
            return copied, canonical
        except Exception:
            raise ServiceRuntimeRequestNotSentError() from None

    def capture_live_parent_source(self, target: BoundServiceTarget, spec: dict[str, Any], *,
                                   deadline_monotonic: float) -> _LiveParentSourceCapability:
        """Capture a legacy raw source; this establishes no recorded intake history."""
        return self._capture_live_parent_source(target, spec, deadline_monotonic=deadline_monotonic,
                                                expected_subject=None)

    def _capture_live_parent_source(self, target, spec, *, deadline_monotonic, expected_subject):
        self._assert_owner_thread()
        # A different SOURCE is also a new observation, not the same capture.
        self._invalidate_live_parent_capabilities()
        try:
            target = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                             expected_peer=replace(target.expected_peer))
            copied, canonical = self._live_parent_spec(target, spec)
            proof = self._live_parent_binding(target)
            args = {k: copied[k] for k in ('account_scope', 'strategy_scope', 'execution_domain_ref')}
            if expected_subject is not None:
                args['subject_version'] = 1
            marker = object()
            if self._active_live_source_marker is not None:
                raise ServiceRuntimeRequestNotSentError()
            self._active_live_source_marker = marker
            try:
                raw, receipt = self._call_bound(target, 'direct.parent.source', args,
                    deadline_monotonic=deadline_monotonic, _require_issued_receipt=True,
                    _source_capture_marker=marker)
            finally:
                self._active_live_source_marker = None
            try:
                from bt_api_py.service_runtime_thread import _copy_wire, _same_wire_value
                raw = _copy_wire(raw)
                current = self._live_parent_binding(target)
            except Exception:
                raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.parent.source') from None
            source = raw.get('result') if type(raw) is dict else None
            fields = {'status', 'schema_version', 'operation', 'result'}
            if expected_subject is not None:
                fields.add('strategy_subject')
            if (type(raw) is not dict or set(raw) != fields
                    or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                    or raw['schema_version'] != (3 if expected_subject is not None else 1)
                    or raw['operation'] != 'direct.parent.source'
                    or type(source) is not dict or set(source) != {'schema_version', 'account_scope',
                        'strategy_scope', 'execution_domain_ref', 'journal_id', 'gateway_generation', 'journal_revision'}
                    or type(source['schema_version']) is not int or source['schema_version'] != 1
                    or any(source[k] != args[k] for k in ('account_scope', 'strategy_scope', 'execution_domain_ref'))
                    or (expected_subject is not None and
                        not _same_wire_value(raw['strategy_subject'], expected_subject))
                    or any(type(source[k]) is not str or not source[k] or source[k] != source[k].strip()
                           or not source[k].isprintable() or len(source[k].encode('utf-8', 'strict')) > 256
                           for k in ('journal_id', 'gateway_generation'))
                    or type(source['journal_revision']) is not int or not 0 <= source['journal_revision'] < 2**63
                    or current != proof):
                raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.parent.source')
            revision_key = (proof[2], source['account_scope'], source['journal_id'], source['gateway_generation'])
            identity_key = (proof[2], source['account_scope'])
            identity = (source['journal_id'], source['gateway_generation'])
            established = self._live_parent_source_identities.get(identity_key)
            if established is not None and established != identity:
                raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.parent.source')
            self._live_parent_source_identities[identity_key] = identity
            previous = self._live_parent_revisions.get(revision_key, -1)
            if source['journal_revision'] < previous:
                raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.parent.source')
            self._live_parent_revisions[revision_key] = source['journal_revision']
            anchor = tuple(source[k] for k in ('account_scope', 'strategy_scope', 'execution_domain_ref',
                                               'journal_id', 'gateway_generation', 'journal_revision'))
            from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
            subject = (None if expected_subject is None else canonical_gateway_payload(raw['strategy_subject']))
            with self._live_parent_capabilities_lock:
                self._prune_live_parent_capabilities()
                if len(self._live_parent_capabilities) >= 1024 or _BOUND_MONOTONIC() >= deadline_monotonic:
                    raise ServiceCallOutcomeUnknownError(receipt.request_message_id, 'direct.parent.source')
                capability = _LiveParentSourceCapability(self)
                self._live_parent_capabilities[capability] = (target, canonical, proof, revision_key, os.getpid(),
                                                              threading.get_ident(), receipt.request_message_id,
                                                              anchor, receipt, subject)
            return capability
        except BaseException:
            self._invalidate_live_parent_capabilities()
            raise

    def capture_prepared_parent_source(self, target: BoundServiceTarget, preparation: Any, parent_ref: str, *,
                                       deadline_monotonic: float) -> _LiveParentSourceCapability:
        """Retired: host-managed parents require a recorded SOURCE attempt."""
        raise ServiceRuntimeRequestNotSentError()

    def _capture_recorded_parent_source(self, target, preparation, parent_ref, attempt_permit, *,
                                        deadline_monotonic):
        """Bind a durably recorded local attempt to one strict SOURCE observation.

        This local capability is not a Gateway registration, send permission,
        source of account truth, or database rollback proof.
        """
        self._assert_owner_thread()
        self._invalidate_live_parent_capabilities()
        capability = None
        try:
            from bt_api_execution.local_gateway_parent_preparation import (
                LocalGatewayParentPreparation, LocalGatewayPreparedSnapshot,
            )
            from bt_api_execution.submission_intake import _wire_scope
            if (type(target) is not BoundServiceTarget or type(preparation) is not LocalGatewayParentPreparation
                    or type(self._recorded_parent_attempts.get(attempt_permit)) is not tuple
                    or self._recorded_parent_attempts[attempt_permit][:3] != (preparation, parent_ref, target)
                    or type(parent_ref) is not str or not parent_ref or parent_ref != parent_ref.strip()
                    or not parent_ref.isprintable() or len(parent_ref.encode('utf-8', 'strict')) > 256
                    or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                    or not _bound_clock_current() or _BOUND_MONOTONIC() >= deadline_monotonic):
                raise ServiceRuntimeRequestNotSentError()
            target_snapshot = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                                      expected_peer=replace(target.expected_peer))
            names = ('_host', '_scope', '_lease', '_wire_scope', '_identity', '_store', '_connection',
                     '_lock', '_parent_scope', '_schema', '_manifest_incarnation')
            binding = tuple(getattr(preparation, name) for name in names)

            def unchanged():
                current_target = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                                         expected_peer=replace(target.expected_peer))
                return (type(target) is BoundServiceTarget and current_target == target_snapshot
                        and all(getattr(preparation, name) is value for name, value in zip(names, binding)))

            before = LocalGatewayParentPreparation.read_prepared(preparation, parent_ref)
            if type(before) is not LocalGatewayPreparedSnapshot or not unchanged():
                raise ServiceRuntimeRequestNotSentError()
            record = json.loads(before.canonical_record)
            if (type(record) is not dict or record.get('status') != 'LOCAL_PREPARED'
                    or record.get('parent_ref') != parent_ref
                    or record['intake']['wire_scope'] != _wire_scope(target_snapshot.scope)):
                raise ServiceRuntimeRequestNotSentError()
            expected_subject = {'identity': record['intake']['identity'], 'scope': record['intake']['wire_scope']}
            spec = json.loads(before.gateway_spec_json)
            copied, canonical = self._live_parent_spec(target_snapshot, spec)
            if canonical != before.gateway_spec_json or not unchanged() or _BOUND_MONOTONIC() >= deadline_monotonic:
                raise ServiceRuntimeRequestNotSentError()
            self._check_recorded_parent_attempt(attempt_permit)
            capability = self._capture_live_parent_source(target_snapshot, copied,
                deadline_monotonic=deadline_monotonic, expected_subject=expected_subject)
            with self._live_parent_capabilities_lock:
                entry = self._live_parent_capabilities.get(capability)
            from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
            if (type(entry) is not tuple or len(entry) != 10 or entry[0] != target_snapshot or entry[1] != canonical
                    or entry[9] != canonical_gateway_payload(expected_subject)):
                raise ServiceCallOutcomeUnknownError('UNKNOWN', 'direct.parent.source')
            try:
                after = LocalGatewayParentPreparation.read_prepared(preparation, parent_ref)
                self._check_recorded_parent_attempt(attempt_permit)
                current_proof = self._live_parent_binding(target_snapshot)
                if (type(after) is not LocalGatewayPreparedSnapshot or after.canonical_record != before.canonical_record
                        or after.gateway_spec_json != canonical or not unchanged()
                        or any(current_proof[i] is not entry[2][i] for i in range(3))
                        or not _bound_clock_current() or _BOUND_MONOTONIC() >= deadline_monotonic):
                    raise ValueError
                with self._live_parent_capabilities_lock:
                    if capability._revoked.is_set() or self._live_parent_capabilities.get(capability) is not entry:
                        raise ValueError
                    self._live_parent_capabilities[capability] = (self, entry, preparation, binding, parent_ref,
                                                                  before.canonical_record, target_snapshot, canonical)
            except Exception:
                raise ServiceCallOutcomeUnknownError(entry[6], 'direct.parent.source') from None
            return capability
        except BaseException as error:
            if type(capability) is _LiveParentSourceCapability:
                self._discard_live_parent_capability(capability)
            self._invalidate_live_parent_capabilities()
            if isinstance(error, (ServiceRuntimeRequestNotSentError, ServiceCallOutcomeUnknownError)):
                raise
            if isinstance(error, Exception):
                if capability is None:
                    raise ServiceRuntimeRequestNotSentError() from None
                raise ServiceCallOutcomeUnknownError('UNKNOWN', 'direct.parent.source') from None
            raise

    def _verify_recorded_parent_attempt(self, cursor, active, permit):
        from bt_api_execution.atomic_submission_intake import _receipt_origin_key
        from bt_api_execution.contracts import canonical_json
        from bt_api_execution.local_gateway_parent_preparation import _preparation_key, _source_attempt_key

        self._assert_owner_thread()
        entry = self._recorded_parent_attempts.get(permit)
        if type(entry) is not tuple or len(entry) != 8:
            raise ValueError
        preparation, parent_ref, target, attempt, binding, request_id, request_sha256, instance_id = entry
        preparation._check(cursor, active)
        prepared = preparation._expected_record(cursor, parent_ref)
        preparation_row = cursor.execute('SELECT value FROM execution_meta WHERE key=?',
            (_preparation_key(preparation._parent_scope, parent_ref),)).fetchone()
        attempt_row = cursor.execute('SELECT value FROM execution_meta WHERE key=?',
            (_source_attempt_key(preparation._parent_scope, parent_ref),)).fetchone()
        origin_row = cursor.execute('SELECT value FROM execution_meta WHERE key=?',
            (_receipt_origin_key(preparation._parent_scope, parent_ref),)).fetchone()
        if preparation_row is None or attempt_row is None or origin_row is None:
            raise ValueError
        attempt_value, origin = json.loads(attempt), json.loads(origin_row[0])
        expected_origin = {'version': 1, 'journal_incarnation': preparation._manifest_incarnation,
            'account_key': preparation._scope.account_key, 'parent_scope': preparation._parent_scope,
            'parent_ref': parent_ref, 'request_id': request_id, 'request_sha256': request_sha256,
            'instance_id': instance_id, 'service_identity': binding['identity'],
            'lease_owner': preparation._lease.owner_id, 'lease_token': preparation._lease.fencing_token}
        origin_hash = hashlib.sha256(canonical_json(expected_origin).encode('utf-8')).hexdigest()
        if (self._recorded_parent_attempts.get(permit) is not entry or preparation_row[0] != prepared
                or attempt_row[0] != attempt or canonical_json(attempt_value) != attempt
                or type(attempt_value) is not dict or attempt_value.get('target') != binding
                or attempt_value.get('origin_sha256') != origin_hash
                or attempt_value.get('preparation_sha256') != hashlib.sha256(prepared.encode('utf-8')).hexdigest()
                or origin != {'origin': expected_origin, 'sha256': origin_hash}
                or canonical_json(origin) != origin_row[0]):
            raise ValueError
        return entry

    def _check_recorded_parent_attempt(self, permit):
        from bt_api_execution.atomic_submission_intake import _nonblocking_store_transaction

        entry = self._recorded_parent_attempts.get(permit)
        if type(entry) is not tuple or len(entry) != 8:
            raise ValueError
        preparation = entry[0]
        with preparation._host.intake_scope(nonblocking=True) as active:
            with _nonblocking_store_transaction(preparation._store, write=False) as cursor:
                self._verify_recorded_parent_attempt(cursor, active, permit)

    def record_prepared_parent_source(self, target, preparation, parent_ref, *, request_id,
                                      request_sha256, instance_id, attempt_id, deadline_monotonic):
        """Persist a single local SOURCE attempt and its authenticated observation.

        A durable observation is history only; it is not a dispatch grant.
        Any uncertainty leaves the original attempt non-retryable.
        """
        from bt_api_execution.atomic_submission_intake import _nonblocking_store_transaction, _receipt_origin_key
        from bt_api_execution.contracts import canonical_json
        from bt_api_execution.local_gateway_parent_preparation import (
            LocalGatewayParentPreparation, _preparation_key, _source_attempt_key, _source_observation_key,
        )
        from bt_api_execution.submission_intake import _identity, _wire_scope

        self._assert_owner_thread()
        if type(target) is not BoundServiceTarget or type(preparation) is not LocalGatewayParentPreparation:
            raise ServiceRuntimeRequestNotSentError()
        binding = {'mount': target.mount, 'service_id': target.service_id, 'instance_id': target.instance_id,
            'scope_ref': target.scope_ref, 'scope': _wire_scope(target.scope), 'identity': _identity(target.identity),
            'run_id': target.run_id, 'transport_ref': target.transport_ref,
            'security_profile_ref': target.security_profile_ref, 'endpoint': target.endpoint,
            'expected_peer': _identity(target.expected_peer), 'server_key_fingerprint': target.server_key_fingerprint,
            'registration_id': target.registration_id, 'service_epoch': target.service_epoch}
        self._live_parent_binding(target)
        try:
            attempt = preparation.begin_source(parent_ref, request_id=request_id, request_sha256=request_sha256,
                                               instance_id=instance_id, attempt_id=attempt_id, target=binding)
        except Exception:
            # The SOURCE RPC has not yet been entered, even if the local
            # attempt transaction itself had an ambiguous outcome.
            raise ServiceRuntimeRequestNotSentError() from None
        if type(attempt) is tuple and attempt in (('HISTORICAL',), ('UNKNOWN',)):
            return {'status': attempt[0]}
        if type(attempt) is not str:
            raise ServiceRuntimeRequestNotSentError()
        capability = None
        attempt_permit = object()
        self._recorded_parent_attempts[attempt_permit] = (preparation, parent_ref, target, attempt, binding,
                                                           request_id, request_sha256, instance_id)
        try:
            capability = self._capture_recorded_parent_source(target, preparation, parent_ref, attempt_permit,
                                                               deadline_monotonic=deadline_monotonic)
            with self._live_parent_capabilities_lock:
                saved = self._live_parent_capabilities.get(capability)
            if (type(saved) is not tuple or len(saved) != 8 or saved[0] is not self
                    or saved[2] is not preparation or saved[4] != parent_ref or saved[6] != target):
                raise ValueError
            entry, prepared = saved[1], saved[5]
            if type(entry) is not tuple or len(entry) != 10:
                raise ValueError
            anchor = entry[7]
            if type(anchor) is not tuple or len(anchor) != 6:
                raise ValueError
            observation = {'version': 1, 'status': 'OBSERVED', 'attempt_id': attempt_id,
                'attempt_sha256': hashlib.sha256(attempt.encode('utf-8')).hexdigest(),
                'account_scope': anchor[0], 'strategy_scope': anchor[1], 'execution_domain_ref': anchor[2],
                'journal_id': anchor[3], 'gateway_generation': anchor[4], 'journal_revision': anchor[5]}
            encoded = canonical_json(observation)
            def check():
                if (not _bound_clock_current() or _BOUND_MONOTONIC() >= deadline_monotonic
                        or capability._revoked.is_set() or self._live_parent_binding(target) != entry[2]):
                    raise ValueError
                with self._live_parent_capabilities_lock:
                    if self._live_parent_capabilities.get(capability) is not saved:
                        raise ValueError
            with preparation._host.intake_scope(nonblocking=True) as active:
                with _nonblocking_store_transaction(preparation._store, write=True) as cursor:
                    check()
                    self._verify_recorded_parent_attempt(cursor, active, attempt_permit)
                    if (preparation._expected_record(cursor, parent_ref) != prepared
                            or cursor.execute('SELECT value FROM execution_meta WHERE key=?',
                                (_preparation_key(preparation._parent_scope, parent_ref),)).fetchone()[0] != prepared
                            or cursor.execute('SELECT value FROM execution_meta WHERE key=?',
                                (_source_attempt_key(preparation._parent_scope, parent_ref),)).fetchone()[0] != attempt):
                        raise ValueError
                    origin_row = cursor.execute('SELECT value FROM execution_meta WHERE key=?',
                        (_receipt_origin_key(preparation._parent_scope, parent_ref),)).fetchone()
                    origin = json.loads(origin_row[0])
                    attempt_value = json.loads(attempt)
                    if (type(origin) is not dict or set(origin) != {'origin', 'sha256'}
                            or origin['sha256'] != attempt_value['origin_sha256']
                            or hashlib.sha256(canonical_json(origin['origin']).encode('utf-8')).hexdigest()
                            != origin['sha256']):
                        raise ValueError
                    key = _source_observation_key(preparation._parent_scope, parent_ref)
                    if cursor.execute('SELECT 1 FROM execution_meta WHERE key=?', (key,)).fetchone() is not None:
                        raise ValueError
                    cursor.execute('INSERT INTO execution_meta(key,value) VALUES (?,?)', (key, encoded))
                    self._verify_recorded_parent_attempt(cursor, active, attempt_permit)
                    check()
            check()
            return {'status': 'OBSERVED'}
        except Exception:
            raise ServiceCallOutcomeUnknownError('UNKNOWN', 'direct.parent.source') from None
        finally:
            self._recorded_parent_attempts.pop(attempt_permit, None)
            if type(capability) is _LiveParentSourceCapability:
                self._discard_live_parent_capability(capability)

    def _invalidate_live_parent_capabilities(self) -> None:
        if os.getpid() != self._owner_pid:
            return
        with self._live_parent_capabilities_lock:
            receipts = getattr(self, '_risk_first_receipts', None)
            if type(receipts) is dict:
                receipts.clear()
            for capability in self._live_parent_capabilities:
                capability._revoked.set()
            self._live_parent_capabilities.clear()

    def _prune_live_parent_capabilities(self) -> None:
        with self._live_parent_capabilities_lock:
            for capability in tuple(self._live_parent_capabilities):
                if capability._revoked.is_set():
                    del self._live_parent_capabilities[capability]

    def _discard_live_parent_capability(self, capability: Any) -> None:
        self._assert_owner_thread()
        if type(capability) is _LiveParentSourceCapability:
            capability._revoke()

    def register_live_parent(self, capability: _LiveParentSourceCapability, target: BoundServiceTarget,
                             spec: dict[str, Any], *, deadline_monotonic: float) -> Mapping[str, Any]:
        """Fail closed until an Execution host dispatch claim can be verified."""
        self._assert_owner_thread()
        if type(capability) is _LiveParentSourceCapability:
            capability._revoke()
        self._invalidate_live_parent_capabilities()
        raise ServiceRuntimeRequestNotSentError()

    def renew(self) -> RegistrationSummary:
        """Force a renewal of every currently active binding on this thread."""

        return self._maintain(force=True)

    def maintain(self) -> RegistrationSummary:
        """Renew due leases synchronously on the owning transport thread."""

        return self._maintain(force=False)

    def next_maintenance_delay(self) -> float | None:
        """Seconds until the next active binding should be maintained, if any."""

        self._assert_owner_thread()
        self._strict_business_entry()
        if self._state != "registered":
            return None
        deadlines = [binding.renewal_due_at for binding in self._bindings if binding.active]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - self._monotonic_now())

    def _maintain(self, *, force: bool) -> RegistrationSummary:
        self._assert_owner_thread()
        self._strict_business_entry()
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        self._require_healthy()
        return self._maintain_bindings(force=force, startup=False)

    def _maintain_startup(self) -> RegistrationSummary:
        self._assert_owner_thread()
        if self._state != "starting":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        if self._startup_maintaining or self._startup_rollback:
            raise ServiceRuntimeError("STARTUP_MAINTENANCE_REENTRANT")
        self._startup_check_required_leases()
        self._startup_maintaining = True
        try:
            return self._maintain_bindings(force=False, startup=True)
        finally:
            self._startup_maintaining = False

    def _maintain_bindings(self, *, force: bool, startup: bool) -> RegistrationSummary:
        if startup:
            if self._state != "starting" or not self._startup_maintaining:
                raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        elif self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        now = self._monotonic_now()
        index = 0
        while index < len(self._bindings):
            binding = self._bindings[index]
            if not binding.active:
                index += 1
                continue
            now = self._monotonic_now()
            remaining = binding.lease_deadline - now
            if remaining <= 0:
                if binding.required:
                    self._mark_maintenance_failed("REQUIRED_LEASE_EXPIRED")
                    raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")
                self._expire_binding(binding)
                continue
            if not force and now < binding.renewal_due_at:
                index += 1
                continue
            renewal_timeout = min(binding.timeout, remaining * 0.5)
            timing: dict[str, Any] = {}
            old_lineage = None
            if ((binding.operations.intersection({
                    "account.cancel_attempts.read_after", "account.cancel_attempts.ack"})
                 or (binding.mount == "upstream.telemetry" and self.identity.role is WireRole.SERVICE
                     and "events.append" in binding.operations)
                 or (binding.mount == "upstream.instrument_products" and self.identity.role is WireRole.SERVICE
                     and binding.operations == PORT_OPERATIONS["service.instrument_product_read"])
                 or (binding.mount == "upstream.calendar" and self.identity.role is WireRole.SERVICE
                     and binding.operations == frozenset({"calendar.monitor.windows.v1"}))
                 or (binding.operations & _ACCOUNT_FACTS_BOUND_OPERATIONS
                     and type(self._factory) is ZmqTransportFactory
                     and self._factory._require_factory_mode() is True))
                    and self._has_current_registration_observation(binding, _startup=startup)):
                old_lineage = self._registration_lineage.get(id(binding))
            upstream_old = None
            if (getattr(self, '_execution_upstream_lease_enabled', False) is True
                    and binding.mount == 'upstream.direct_write' and self.identity.role is WireRole.SERVICE
                    and {'direct.parent.register', 'direct.parent.query'} <= binding.operations):
                upstream_old = self._execution_upstream_lease_for(binding)
                getattr(self, '_execution_upstream_clock_leases', {}).pop(self._execution_upstream_key(binding), None)
            try:
                receipt, request_started = self._rpc(
                    binding.service_id,
                    binding.transport_ref,
                    binding.scope,
                    "service.renew",
                    {
                        "registration_id": binding.registration_id,
                        "service_epoch": binding.service_epoch,
                    },
                    timeout=renewal_timeout,
                    include_started_at=True,
                    timing=timing,
                )
                self._validate_renewal_receipt(receipt, binding)
                expiry = self._expiry(receipt)
                lease_remaining = self._lease_remaining(receipt)
            except Exception as error:
                server_rejected = timing.get("server_rejected") is True
                definitely_inactive = (
                    server_rejected
                    and isinstance(error, ServiceRuntimeError)
                    and error.code in {"NOT_REGISTERED", "STALE_EPOCH"}
                )
                self._registration_lineage.pop(id(binding), None)
                if definitely_inactive:
                    # The registry itself refused this (registration, epoch) on
                    # renewal. That is authoritative evidence the old binding no
                    # longer resolves; a local lease expiry or transport timeout
                    # is not recorded here.
                    self._remote_inactive_registrations[
                        binding.registration_id
                    ] = binding.service_epoch
                self._bindings[index] = replace(
                    binding,
                    remote_lease_inactive=definitely_inactive,
                )
                if definitely_inactive and not self._require_unregister_ack:
                    self._unregister_uncertainties.pop(binding.registration_id, None)
                binding = self._bindings[index]
                if binding.required:
                    if (
                        not force
                        and not server_rejected
                        and (
                            isinstance(error, TimeoutError)
                            or (
                                isinstance(error, ServiceRuntimeRequestNotSentError)
                                and self.identity.role is WireRole.STRATEGY_CLIENT
                            )
                        )
                        and self._monotonic_now() < binding.lease_deadline
                    ):
                        # The renewal outcome is indeterminate (the transport
                        # timed out) while the current lease is still
                        # unexpired. Keep the binding due and retry on the
                        # next maintenance pass instead of failing the whole
                        # session; the lease deadline stays the hard boundary
                        # (a lease that never renews still fails with
                        # REQUIRED_LEASE_EXPIRED). A strategy client also
                        # waits out a renewal the transport proved it never
                        # sent (a paused or restarting Gateway, I63-E112); a
                        # service with a required upstream keeps failing fast
                        # so it never serves without it. Explicit hub
                        # rejections and forced renewals keep failing fast.
                        break
                    failure_code = (
                        error.code if isinstance(error, ServiceRuntimeError)
                        else "REQUIRED_RENEWAL_FAILED"
                    )
                    self._mark_maintenance_failed(failure_code)
                    if isinstance(error, ServiceRuntimeError):
                        raise
                    raise ServiceRuntimeError("REQUIRED_RENEWAL_FAILED") from None
                self._degrade_binding(binding, self._error_code(error))
                # A retained inactive binding stays at this index; a removed
                # one makes the next live binding shift into it.
                if (
                    index < len(self._bindings)
                    and self._bindings[index].registration_id == binding.registration_id
                ):
                    index += 1
                continue

            # A successful response carries a duration measured at the
            # registry. The request-start anchor is intentionally conservative
            # for local use and never revives an already expired local lease.
            new_deadline = request_started + lease_remaining
            if self._monotonic_now() >= binding.lease_deadline:
                if binding.required:
                    self._mark_maintenance_failed("LEASE_EXPIRED_DURING_RENEWAL")
                    raise ServiceRuntimeError("LEASE_EXPIRED_DURING_RENEWAL")
                self._expire_binding(binding)
                continue
            renewed = replace(
                binding,
                expires_at=expiry,
                lease_deadline=new_deadline,
                remote_lease_inactive=False,
                renewal_due_at=self._renewal_deadline(
                    request_started,
                    lease_remaining,
                    binding.heartbeat_interval_seconds,
                ),
            )
            lineage_unchanged = (old_lineage is not None
                                 and self._registration_lineage.get(id(binding)) is old_lineage)
            self._registration_lineage.pop(id(binding), None)
            self._bindings[index] = renewed
            if upstream_old is not None:
                self._publish_execution_upstream_lease(renewed, timing, lease_remaining, old=upstream_old)
            else:
                self._execution_upstream_observation = None
            if self._monotonic_now() >= new_deadline:
                if binding.required:
                    self._mark_maintenance_failed("LEASE_EXPIRED_DURING_RENEWAL")
                    raise ServiceRuntimeError("LEASE_EXPIRED_DURING_RENEWAL")
                self._expire_binding(renewed)
                continue
            if lineage_unchanged:
                try:
                    observed = timing.get("renewal_observation")
                    (transport_proof, observed_reply, observed_receipt, observed_result,
                     encoded_body, receipt_fields, observed_registration, observed_epoch) = observed
                    original_transport = old_lineage[2]
                    if (type(observed) is tuple and len(observed) == 8
                            and observed_result is receipt
                            and observed_reply.payload.get("result") is receipt
                            and observed_registration == binding.registration_id
                            and observed_epoch == binding.service_epoch
                            and tuple(vars(binding).items()) == old_lineage[1]
                            and transport_proof[0] == original_transport[0]
                            and all(transport_proof[i] is original_transport[i] for i in (1, 2, 3, 4))
                            and transport_proof[5:] == original_transport[5:]
                            and self._registration_transport_is_current(original_transport)
                            and encode_envelope(observed_reply) == encoded_body
                            and tuple(vars(observed_receipt).items()) == receipt_fields
                            and self._monotonic_now() < renewed.lease_deadline
                            and self._bindings[index] is renewed):
                        self._registration_lineage[id(renewed)] = (
                            renewed, tuple(vars(renewed).items()), *old_lineage[2:9],
                            (observed_reply, observed_receipt, observed_result, encoded_body,
                             receipt_fields, request_started),
                        )
                except Exception:
                    # The renewed service binding remains usable by ordinary
                    # paths, but the required registration lineage cannot be inferred.
                    pass
            index += 1
        if startup:
            self._startup_check_required_leases()
        else:
            self._require_healthy()
        return self._summary()

    def recover(self) -> RegistrationSummary:
        """Start a fresh registration session without replaying business calls."""

        self._assert_owner_thread()
        self._strict_public_lifecycle_entry()
        if not self.sticky_cleanup_enabled:
            self._registration_lineage.clear()
        self._invalidate_live_parent_capabilities()
        if self._state not in {"registered", "failed", "close_failed"}:
            raise ServiceRuntimeError("SESSION_NOT_RECOVERABLE")
        if self.sticky_cleanup_enabled:
            self._release()
            self.client_session_id = uuid.uuid4().hex
            self._degraded = 0
            self._degradation_codes.clear()
            self._state = "new"
            return self.start()
        try:
            self._release(capture_base_exceptions=True)
        except BaseException:
            self._state = "close_failed" if self._transports else "failed"
            if self._transports:
                raise ServiceRuntimeError("CLOSE_FAILED") from None
            raise
        if self._transports:
            self._state = "close_failed"
            raise ServiceRuntimeError("CLOSE_FAILED")
        self.client_session_id = uuid.uuid4().hex
        self._bindings.clear()
        self._degraded = 0
        self._degradation_codes.clear()
        self._state = "new"
        return self.start()

    def retire_required_monitor_session(self) -> bool:
        """Retire one heartbeat-only upstream without losing unregister evidence.

        This owner-thread operation disables business calls before the first
        unregister. A false result retains the original binding and transport
        for a later attempt; it never relies on a locally elapsed lease. Only
        after the Registry has acknowledged retirement (or an existing
        authenticated inactive-epoch proof is applicable) is local close tried.
        """
        self._assert_owner_thread()
        clients = self._config.get("clients", {})
        declared = [binding for client in clients.values()
                    for binding in client["settings"]["bindings"]]
        if (self.sticky_cleanup_enabled or self._state not in {
                "new", "registered", "failed", "monitor_cleanup_pending", "close_failed", "closed"}
                or type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE
                or self.identity.delegate_ref is not None
                or self._requested_operations != {"upstream.telemetry": frozenset({"monitor.heartbeat"})}
                or len(declared) != 1 or declared[0]["mount"] != "upstream.telemetry"
                or declared[0].get("required", True) is not True):
            raise ServiceRuntimeError("MONITOR_CLEANUP_UNAVAILABLE")
        if len(self._bindings) > 1 or any(
            type(binding) is not RegisteredBinding
            or binding.mount != "upstream.telemetry" or binding.required is not True
            or binding.service_id != declared[0]["service_id"]
            or binding.instance_id != declared[0]["instance_id"]
            or binding.scope_ref != declared[0]["scope_ref"]
            or binding.transport_ref != declared[0]["transport_ref"]
            or binding.operations != frozenset({"monitor.heartbeat"})
            for binding in self._bindings
        ):
            raise ServiceRuntimeError("MONITOR_CLEANUP_UNAVAILABLE")
        if self._state == "registered" and len(self._bindings) != 1:
            raise ServiceRuntimeError("MONITOR_CLEANUP_UNAVAILABLE")
        if self._state != "closed":
            self._invalidate_live_parent_capabilities()
            self._registration_lineage.clear()
            self._state = "monitor_cleanup_pending"
        if self._unknown_registration_count or self._unresolved_unregister_count:
            raise ServiceRuntimeError("MONITOR_CLEANUP_REGISTRATION_UNKNOWN")
        binding_ids = {binding.registration_id for binding in self._bindings}
        if set(self._unregister_uncertainties) - binding_ids:
            raise ServiceRuntimeError("MONITOR_CLEANUP_REGISTRATION_UNKNOWN")
        if self._state == "closed":
            if self._bindings or self._transports or self.unregister_uncertainties:
                raise ServiceRuntimeError("MONITOR_CLEANUP_UNAVAILABLE")
            return True
        if self._bindings:
            binding = self._bindings[0]
            if (binding.service_id, binding.transport_ref) not in self._transports:
                raise ServiceRuntimeError("MONITOR_CLEANUP_REGISTRATION_UNKNOWN")
            if not self._unregister_binding(binding):
                return False
            self._bindings.clear()
        if self.unregister_uncertainties:
            raise ServiceRuntimeError("MONITOR_CLEANUP_REGISTRATION_UNKNOWN")
        self.close()
        return self._state == "closed" and not self._transports

    def close(self) -> None:
        self._assert_owner_thread()
        self._strict_public_lifecycle_entry()
        self._invalidate_live_parent_capabilities()
        if self.sticky_cleanup_enabled:
            try:
                self._release()
                self._state = "closed"
            except BaseException as error:
                self._set_cleanup_sticky(error)
                raise
            return
        self._registration_lineage.clear()
        if self._state == "closed":
            return
        try:
            self._release()
        except BaseException:
            self._state = "close_failed" if self._transports else "closed"
            raise
        self._state = "close_failed" if self._transports else "closed"
        if self._transports:
            raise ServiceRuntimeError("CLOSE_FAILED")

    def _summary(self) -> RegistrationSummary:
        return RegistrationSummary(self.bindings, self._degraded, tuple(self._degradation_codes))

    def _mark_failed(self) -> None:
        self._registration_lineage.clear()
        self._state = "failed"

    def _mark_maintenance_failed(self, code: str) -> None:
        # Keep the finite cause beside the coarse "failed" state so a caller can
        # tell an authoritative registry refusal from a local timeout or expiry.
        self._maintenance_code = code
        self._mark_failed()

    def _require_healthy(self) -> None:
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        now = self._monotonic_now()
        expired = [
            binding
            for binding in self._bindings
            if binding.active and binding.required and now >= binding.lease_deadline
        ]
        if expired:
            self._mark_maintenance_failed("REQUIRED_LEASE_EXPIRED")
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")
        for binding in tuple(self._bindings):
            if binding.active and not binding.required and now >= binding.lease_deadline:
                self._expire_binding(binding)

    def _startup_check_required_leases(self) -> None:
        if self._state != "starting":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        now = self._monotonic_now()
        if any(
            binding.active and binding.required and now >= binding.lease_deadline
            for binding in self._bindings
        ):
            self._mark_maintenance_failed("REQUIRED_LEASE_EXPIRED")
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")

    def _startup_maintain_if_due(self) -> None:
        if self._state != "starting" or self._startup_rollback:
            return
        self._startup_check_required_leases()
        if self._startup_maintaining:
            return
        now = self._monotonic_now()
        if any(binding.active and now >= binding.renewal_due_at for binding in self._bindings):
            self._maintain_startup()

    def _release_during_startup(self, first: int = 0) -> None:
        was_rollback = self._startup_rollback
        self._startup_rollback = True
        try:
            self._release_bindings(first)
        finally:
            self._startup_rollback = was_rollback

    def _unregister_during_startup(
        self, service_id, transport_ref, scope, registration_id, epoch, timeout
    ) -> bool:
        was_rollback = self._startup_rollback
        self._startup_rollback = True
        try:
            return self._unregister(
                service_id,
                transport_ref,
                scope,
                registration_id,
                epoch,
                timeout,
            )
        finally:
            self._startup_rollback = was_rollback

    def _startup_timeout(self, timeout: float) -> float:
        self._startup_check_required_leases()
        deadlines = [
            binding.lease_deadline
            for binding in self._bindings
            if binding.active and binding.required
        ]
        if not deadlines:
            return timeout
        remaining = min(deadlines) - self._monotonic_now()
        if remaining <= 0:
            self._mark_maintenance_failed("REQUIRED_LEASE_EXPIRED")
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")
        return min(timeout, remaining * self._STARTUP_RPC_TIMEOUT_FRACTION)

    def _expire_binding(self, binding: RegisteredBinding) -> None:
        if binding.required:
            self._mark_maintenance_failed("LEASE_EXPIRED")
            return
        self._degrade_binding(binding, "LEASE_EXPIRED")

    def _degrade_binding(self, binding: RegisteredBinding, code: str) -> None:
        if self.sticky_cleanup_enabled:
            self._strict_cleanup_entry()
            self._cleanup_in_progress = True
            try:
                self._strict_unregister_binding(binding)
                self._degraded += 1
                self._degradation_codes.append(code)
            except BaseException as error:
                self._set_cleanup_sticky(binding, error)
                raise
            finally:
                self._cleanup_in_progress = False
            return
        self._registration_lineage.pop(id(binding), None)
        self._degraded += 1
        self._degradation_codes.append(code)
        try:
            index = next(
                i
                for i, current in enumerate(self._bindings)
                if current.registration_id == binding.registration_id
            )
        except StopIteration:
            return
        self._registration_lineage.pop(id(self._bindings[index]), None)
        key = (binding.service_id, binding.transport_ref)
        acknowledged = False
        if binding.remote_lease_inactive and not self._require_unregister_ack:
            acknowledged = True
        elif key in self._transports:
            acknowledged = self._unregister_binding(binding)
        else:
            self._unregister_uncertainties[binding.registration_id] = None
        if acknowledged or key not in self._transports:
            del self._bindings[index]
        else:
            self._bindings[index] = replace(binding, active=False)

    @staticmethod
    def _error_code(error: Exception) -> str:
        return error.code if isinstance(error, ServiceRuntimeError) else "SERVICE_UNAVAILABLE"

    def _monotonic_now(self) -> float:
        value = self._monotonic_clock()
        self._strict_start_check()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ServiceRuntimeError("INVALID_MONOTONIC_CLOCK")
        return float(value)

    @classmethod
    def _renewal_deadline(
        cls, request_started: float, lease_remaining: float, heartbeat: float | None
    ) -> float:
        delay = lease_remaining * cls._RENEWAL_FRACTION
        if heartbeat is not None:
            delay = min(delay, heartbeat)
        return request_started + delay

    def _assert_owner_thread(self) -> None:
        if os.getpid() != self._owner_pid:
            raise ServiceRuntimeError("WRONG_TRANSPORT_PROCESS")
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceRuntimeError("WRONG_TRANSPORT_THREAD")

    def _scope(self, binding: Mapping[str, Any]) -> WireScope:
        scope = self._scope_resolver(binding["scope_ref"])
        if type(scope) is not WireScope:
            raise ServiceRuntimeError("INVALID_SCOPE")
        if binding.get("account_ref") is not None and scope.account_ref != binding["account_ref"]:
            raise ServiceRuntimeError("ACCOUNT_SCOPE_MISMATCH")
        if (
            binding.get("portfolio_ref") is not None
            and scope.portfolio_ref != binding["portfolio_ref"]
        ):
            raise ServiceRuntimeError("PORTFOLIO_SCOPE_MISMATCH")
        if self.identity.role is WireRole.STRATEGY_CLIENT:
            strategy_ref = self._config["registration"]["strategy_ref"]
            if scope.strategy_ref not in (None, strategy_ref) or scope.manual_book_ref is not None:
                raise ServiceRuntimeError("STRATEGY_SCOPE_MISMATCH")
            scope = replace(scope, strategy_ref=strategy_ref, run_id=self.run_id)
        return scope

    def _register(
        self,
        binding: Mapping[str, Any],
        *,
        timeout_ms: int | None,
        heartbeat_interval_seconds: int | None,
    ) -> None:
        service_id, transport_ref = binding["service_id"], binding["transport_ref"]
        scope = self._scope(binding)
        self._strict_start_check()
        peer = self._peer_resolver(service_id)
        self._strict_start_check()
        if type(peer) is not WireIdentity or peer.role is not WireRole.SERVICE:
            raise ServiceRuntimeError("INVALID_PEER")
        if service_id in self._peers and self._peers[service_id] != peer:
            raise ServiceRuntimeError("PEER_CHANGED")
        self._peers[service_id] = peer
        profiles = self._config["communication"]["profiles"]
        timeout = (timeout_ms or profiles[transport_ref]["options"]["request_timeout_ms"]) / 1000
        key = (service_id, transport_ref)
        if key not in self._transports:
            if type(self._factory) is ZmqTransportFactory:
                pending = _PendingZmqTransport()
                self._transports[key] = pending
                try:
                    self._transports[key] = self._factory(profiles[transport_ref], peer, _pending=pending)
                except BaseException:
                    if self._transports.get(key) is pending:
                        try:
                            pending.close()
                        except BaseException:
                            raise ServiceRuntimeStartupCleanupError(pending) from None
                        del self._transports[key]
                    raise
            else:
                try:
                    self._transports[key] = self._factory(profiles[transport_ref], peer)
                except ServiceRuntimeStartupCleanupError as error:
                    self._transports[key] = error.transport
                    raise
            self._strict_start_check()
        discovered = self._rpc(
            service_id, transport_ref, scope, "service.discover", {}, timeout=timeout
        )
        self._strict_start_check()
        # Version handshake (iteration 60, P1-3): both the protocol version and
        # the wire contract digest (envelope + typed registry) must match.
        if (
            discovered.get("protocol_version") != WIRE_PROTOCOL_VERSION
            or discovered.get("wire_contract") != wire_contract_digest()
        ):
            raise ServiceRuntimeError("PROTOCOL_VERSION_MISMATCH")
        # Typed contract negotiation (iteration 60.08, G5): a service that
        # declares another typed minor or registry digest is refused before
        # registration; there is no downgrade and no generic fallback. A
        # service without a declaration offers no typed negotiation; each typed
        # part it might return is still refused by its own registry digest.
        if "typed_contract" in discovered:
            from bt_api_base.wire_codec_v1 import (
                WireCodecError,
                check_typed_contract_declaration,
            )

            try:
                check_typed_contract_declaration(discovered["typed_contract"])
            except WireCodecError as error:
                raise ServiceRuntimeError(
                    "TYPED_CONTRACT_MISMATCH"
                    if error.code == "WIRE_TYPED_CONTRACT_MISMATCH"
                    else "TYPED_CONTRACT_INVALID"
                ) from None
        if discovered.get("service_id") != service_id or discovered.get("ready") is not True:
            raise ServiceRuntimeError("SERVICE_NOT_READY")
        capabilities = discovered.get("capabilities")
        if not isinstance(capabilities, (tuple, list)):
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        requested = self._requested_operations.get(binding["mount"])
        if binding["mount"] == "upstream.instrument_products" and requested != PORT_OPERATIONS["service.instrument_product_read"]:
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        required_ports = MOUNT_PORTS[binding["mount"]]
        if binding["mount"] == "application.monitor" and requested is not None:
            required_ports = tuple(
                port for port in required_ports if requested.intersection(PORT_OPERATIONS[port])
            )
            if not required_ports:
                # A control-only mount selects its control endpoint directly.
                # Its binding policy governs startup/renewal; query capability
                # is not a prerequisite. Mixed read/control mounts retain the
                # existing optional-control behavior below.
                required_ports = tuple(
                    port
                    for port in OPTIONAL_MOUNT_PORTS[binding["mount"]]
                    if requested.intersection(PORT_OPERATIONS[port])
                )
        for port in required_ports:
            self._register_port(
                binding,
                scope,
                port,
                capabilities,
                timeout=timeout,
                required=binding.get("required", True),
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        for port in OPTIONAL_MOUNT_PORTS.get(binding["mount"], ()):
            # Explicit application code requests control separately from reads.
            if (
                requested is not None
                and port not in required_ports
                and requested.intersection(PORT_OPERATIONS[port])
            ):
                try:
                    self._register_port(
                        binding,
                        scope,
                        port,
                        capabilities,
                        timeout=timeout,
                        required=False,
                        heartbeat_interval_seconds=heartbeat_interval_seconds,
                    )
                except Exception as error:
                    if isinstance(error, ServiceRuntimeStartupCleanupError):
                        raise
                    if self._state != "starting":
                        raise
                    self._degraded += 1
                    self._degradation_codes.append(
                        error.code
                        if isinstance(error, ServiceRuntimeError)
                        else "SERVICE_UNAVAILABLE"
                    )

    def _register_port(
        self,
        binding,
        scope,
        port,
        capabilities,
        *,
        timeout,
        required,
        heartbeat_interval_seconds,
    ):
        if not self.sticky_cleanup_enabled:
            return self._register_port_impl(
                binding, scope, port, capabilities, timeout=timeout, required=required,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        if self._registration_in_progress is not None:
            error = ServiceRuntimeError("CLOSE_FAILED")
            self._set_cleanup_sticky(self._registration_in_progress, error)
            raise error
        window: dict[str, Any] = {"attempted": False, "timing": None, "handle_recorded": False}
        self._registration_in_progress = window
        try:
            return self._register_port_impl(
                binding, scope, port, capabilities, timeout=timeout, required=required,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        except BaseException as error:
            timing = window["timing"]
            proven_not_sent = type(timing) is dict and (
                timing.get("request_not_sent") is True or timing.get("server_rejected") is True
            )
            # A handle that was once inserted may have disappeared during a
            # later replacement or a reentrant mutation. Only the exact
            # placeholder or final object still installed, or a completed
            # unregister, avoids treating the registration as unresolved.
            handle_present = window["handle_recorded"] and any(
                item is window.get("placeholder") or item is window.get("final_binding")
                for item in self._bindings
            )
            if (window["attempted"] and not proven_not_sent
                    and not handle_present and not window.get("released", False)):
                self._set_cleanup_sticky(binding, scope, port, window, error)
                if self._unknown_registration_count == window["unknown_count"]:
                    self._record_unknown_registration(window["registration_key"])
            raise
        finally:
            self._registration_in_progress = None

    def _register_port_impl(
        self,
        binding,
        scope,
        port,
        capabilities,
        *,
        timeout,
        required,
        heartbeat_interval_seconds,
    ):
        available = [
            c
            for c in capabilities
            if isinstance(c, Mapping)
            and c.get("instance_id") == binding["instance_id"]
            and c.get("port") == port
        ]
        if port == "service.market_source":
            market_descriptors = [
                capability
                for capability in capabilities
                if isinstance(capability, Mapping)
                and capability.get("port") == "service.market_source"
            ]
            if not market_descriptors:
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
            legacy_fields = frozenset({"instance_id", "port", "operations"})
            versioned_fields = legacy_fields | {"market_wire_version"}
            normalized_descriptors = []
            for capability in market_descriptors:
                fields = frozenset(capability)
                if fields not in (legacy_fields, versioned_fields):
                    raise ServiceRuntimeError("INVALID_MARKET_CAPABILITY")
                if (
                    type(capability.get("instance_id")) is not str
                    or type(capability.get("port")) is not str
                    or capability["port"] != "service.market_source"
                    or not isinstance(capability.get("operations"), (list, tuple))
                    or any(type(operation) is not str for operation in capability["operations"])
                ):
                    raise ServiceRuntimeError("INVALID_MARKET_CAPABILITY")
                remote_version = (
                    1
                    if fields == legacy_fields
                    else capability.get("market_wire_version")
                )
                if type(remote_version) is not int or remote_version not in (1, 2):
                    raise ServiceRuntimeError("INVALID_MARKET_CAPABILITY")
                normalized_descriptors.append((capability, remote_version))
            available = [
                (capability, version)
                for capability, version in normalized_descriptors
                if capability["instance_id"] == binding["instance_id"]
            ]
            if not available:
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
            if len(available) != 1:
                raise ServiceRuntimeError("INVALID_MARKET_CAPABILITY")
            market_capability, remote_market_wire_version = available[0]
            remote_operations = market_capability["operations"]
            profile = self._config["communication"]["profiles"].get(
                binding["transport_ref"]
            )
            options = profile.get("options") if isinstance(profile, Mapping) else None
            local_market_wire_version = (
                options.get("market_wire_version", 1) if isinstance(options, Mapping) else None
            )
            if (
                type(local_market_wire_version) is not int
                or local_market_wire_version not in (1, 2)
                or local_market_wire_version != remote_market_wire_version
            ):
                raise ServiceRuntimeError("MARKET_WIRE_VERSION_MISMATCH")
        else:
            if len(available) != 1 or not isinstance(
                available[0].get("operations"), (list, tuple)
            ):
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
            remote_operations = available[0]["operations"]
            if not all(isinstance(op, str) for op in remote_operations):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        operations = PORT_OPERATIONS[port].intersection(remote_operations)
        requested = self._requested_operations.get(binding["mount"])
        if binding["mount"] == "upstream.calendar" and requested != frozenset({"calendar.monitor.windows.v1"}):
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        if binding["mount"] == "store.calendar" and requested is not None and "calendar.monitor.windows.v1" in requested:
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        if requested is not None:
            wanted = requested.intersection(PORT_OPERATIONS[port])
            if not wanted <= operations:
                error = ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
                # This marker is emitted only after authenticated discovery of
                # one concrete capability, never for transport/auth timeouts.
                if (port == "service.telemetry_ingress"
                        and wanted == frozenset({"monitor.heartbeat"})
                        and "monitor.heartbeat" not in remote_operations):
                    error.confirmed_missing_monitor_heartbeat = (
                        binding["service_id"], binding["instance_id"],
                        self._peers.get(binding["service_id"]),
                    )
                raise error
            operations = wanted
        elif port == "service.calendar_read":
            # Existing day-only consumers do not implicitly acquire range reads.
            operations = operations.intersection({"calendar.query.v1"})
        elif port == "service.direct_write":
            operations = operations.difference(
                {"direct.risk_first_reserve.v2", "direct.parent.acceptance_order", "direct.events.read_after", "direct.events.progress", "direct.events.ack",
                 "direct.cancel", "direct.cancel_status", "direct.control.apply", "direct.control.resume",
                 "direct.strategy_position", "direct.strategy_close_capacity", "direct.strategy_financial", "direct.instrument_status",
                 "direct.delegated_command_status",
                 "direct.delegated_cancel", "direct.delegated_cancel_status",
                 "direct.delegated_reserve", "direct.delegated_dispatch",
                 "direct.delegate_actor_status", "direct.delegate_actor_cancel", "direct.delegate_actor_cancel_status", "direct.delegate_actor_release", "direct.delegate_actor_release_status",
                 "direct.delegate_events.read_after", "direct.delegate_events.progress", "direct.delegate_events.ack",
                 "direct.parent.register", "direct.parent.query", "direct.parent.cancel", "direct.parent.operation", "direct.parent.source", "direct.parent.register_v2", "direct.parent.operation_v2", "direct.parent.cancel_v2",
                 "direct.control.identity", "direct.control.status", "direct.control.targets", "direct.control.list", "direct.control.drain_target", "direct.control.reconcile"}
                | {"direct.control.resume_status", "direct.control.target_status"}
                | {"direct.control.cancel_scope", "direct.control.cancel_scope_status"}
                | _CONTROL_INTENT_OPERATIONS
            )
        elif port == "service.market_source":
            # New asynchronous bootstrap operations require explicit opt-in.
            operations = operations.difference(
                {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel", "market.clock_observation"}
            )
        elif port == "service.account_read":
            # New server capabilities must not silently expand registrations
            # for existing read-only clients and grants. Consumers opt in to
            # ACK/progress explicitly through requested_operations.
            operations = operations.intersection(_DEFAULT_ACCOUNT_READ_OPERATIONS)
        elif port == "service.order_query":
            # Account-book and group-leg views each need an explicit request
            # and grant. Existing order readers retain their prior set.
            operations = operations.difference(
                {
                    "execution.account_books.query",
                    "orders.query_group_leg",
                    "orders.query_group_cancel",
                }
            )
        elif port == "service.order_group_entry":
            # Group cancellation is an additive write capability. Keep the
            # pre-cancel submit/query registration for clients that do not
            # explicitly request operations.
            operations = operations.intersection(_DEFAULT_GROUP_ORDER_ENTRY_OPERATIONS)
        elif port == "service.order_entry":
            # Group-owner writes are available only to callers that explicitly
            # request them; adding a remote capability cannot expand ordinary
            # application or service registrations.
            operations = operations.difference(
                _GROUP_ORDER_ENTRY_ALL_OPERATIONS
                | _GROUP_COMPENSATION_ORDER_ENTRY_OPERATIONS
                | _GROUP_ROLL_EXIT_ORDER_ENTRY_OPERATIONS
                | _APPROVED_ORDER_ENTRY_OPERATIONS
                | _RECOVERY_CLOSE_ORDER_ENTRY_OPERATIONS
            )
        elif port == "service.telemetry_ingress":
            # Durable facts use a distinct event operation. Existing clients
            # keep the generic telemetry operation unless they explicitly opt
            # in to events.append through requested_operations.
            operations = operations.intersection(_DEFAULT_TELEMETRY_INGRESS_OPERATIONS)
        elif binding["mount"] == "broker.group_monitor":
            # Strategy group monitoring is an explicitly read-only projection.
            # Do not inherit the broader application monitor query surface.
            operations = operations.intersection(_GROUP_MONITOR_OPERATIONS)
        if not operations:
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        # Risk-only owners may expose submit without a cancel implementation.
        # Register only advertised operations; absent cancel never gets a route.
        if port == "service.order_entry" and "orders.submit" not in operations:
            explicit_group_owner = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                in {_GROUP_ORDER_ENTRY_OPERATIONS, _GROUP_ORDER_ENTRY_ALL_OPERATIONS}
                and operations == requested.intersection(PORT_OPERATIONS[port])
            )
            explicit_approved_target_only = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                == _APPROVED_ORDER_ENTRY_OPERATIONS
                and operations == _APPROVED_ORDER_ENTRY_OPERATIONS
            )
            explicit_group_compensation_only = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                == _GROUP_COMPENSATION_ORDER_ENTRY_OPERATIONS
                and operations == _GROUP_COMPENSATION_ORDER_ENTRY_OPERATIONS
            )
            explicit_group_roll_exit_only = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                == _GROUP_ROLL_EXIT_ORDER_ENTRY_OPERATIONS
                and operations == _GROUP_ROLL_EXIT_ORDER_ENTRY_OPERATIONS
            )
            explicit_recovery_close_only = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                == _RECOVERY_CLOSE_ORDER_ENTRY_OPERATIONS
                and operations == _RECOVERY_CLOSE_ORDER_ENTRY_OPERATIONS
            )
            if not (
                explicit_group_owner
                or explicit_approved_target_only
                or explicit_group_compensation_only
                or explicit_group_roll_exit_only
                or explicit_recovery_close_only
            ):
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        digest = hashlib.sha256(
            json.dumps(dict(binding), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        registration_material = (self.client_session_id + ":" + digest + ":" + port).encode()
        execution_upstream = (getattr(self, '_execution_upstream_lease_enabled', False) is True
                              and self.identity.role is WireRole.SERVICE
                              and binding['mount'] == 'upstream.direct_write' and port == 'service.direct_write'
                              and {'direct.parent.register', 'direct.parent.query'} <= operations)
        nonce = None
        if execution_upstream:
            if self._execution_upstream_registration_nonce is None and not self._execution_upstream_nonce_failed:
                try:
                    if (type(_EXECUTION_NONCE_BYTES) is not BuiltinFunctionType
                            or _EXECUTION_NONCE_BYTES.__module__ not in ('posix', 'nt')
                            or _EXECUTION_NONCE_BYTES.__name__ != 'urandom'):
                        raise ValueError
                    nonce = _EXECUTION_NONCE_BYTES(32)
                    if type(nonce) is not bytes or len(nonce) != 32:
                        raise ValueError
                    self._execution_upstream_registration_nonce = nonce
                except (OSError, ValueError):
                    self._execution_upstream_nonce_failed = True
            nonce = self._execution_upstream_registration_nonce
            if nonce is not None:
                registration_material += b':execution-upstream:' + nonce
        registration_key = hashlib.sha256(registration_material).hexdigest()
        payload = {
            "registration_key": registration_key,
            "client_session_id": self.client_session_id,
            "instance_id": binding["instance_id"],
            "port": port,
            "scope_ref": binding["scope_ref"],
            "operations": sorted(operations),
        }
        attempt_scope = scope
        for attempt in ("initial", "adopted"):
            timing: dict[str, Any] = {}
            if self.sticky_cleanup_enabled:
                self._registration_in_progress["attempted"] = False
                self._registration_in_progress.update(
                    timing=timing, handle_recorded=False,
                    registration_key=registration_key, unknown_count=self._unknown_registration_count,
                )
                self._registration_in_progress["attempted"] = True
            if execution_upstream:
                attempted = self._execution_upstream_attempted_keys
                first = nonce is not None and len(attempted) < 8192 and registration_key not in attempted
                if first:
                    attempted.add(registration_key)
                timing['execution_first_registration_attempt'] = first
            try:
                result, request_started = self._rpc(
                    binding["service_id"],
                    binding["transport_ref"],
                    attempt_scope,
                    "service.register",
                    payload,
                    timeout=timeout,
                    include_started_at=True,
                    timing=timing,
                )
                if self.sticky_cleanup_enabled and self._has_sticky_cleanup():
                    raise ServiceRuntimeError("CLOSE_FAILED")
            except ServiceRuntimeError as error:
                adopted_run_id = self._released_run_adoption_run_id(error, attempt_scope)
                if attempt == "initial" and adopted_run_id is not None:
                    # The exact scope's durable strategy-write claim is
                    # released (previous session unregistered, expired, or
                    # died with an earlier service epoch) and names one other
                    # logical run. Adopt that durable run instead of minting a
                    # competing one; a live claim never yields this signal, so
                    # two simultaneously-live runs stay impossible.
                    attempt_scope = replace(scope, run_id=adopted_run_id)
                    continue
                if (
                    timing.get("request_started") is not None
                    and not timing.get("server_rejected", False)
                    and not timing.get("request_not_sent", False)
                ):
                    self._record_unknown_registration(registration_key)
                    if self.sticky_cleanup_enabled:
                        self._set_cleanup_sticky(binding, attempt_scope, registration_key, error)
                raise
            except BaseException as error:
                if (
                    timing.get("request_started") is not None
                    and not timing.get("server_rejected", False)
                    and not timing.get("request_not_sent", False)
                ):
                    self._record_unknown_registration(registration_key)
                    if self.sticky_cleanup_enabled:
                        self._set_cleanup_sticky(binding, attempt_scope, registration_key, error)
                raise
            scope = attempt_scope
            break
        registration_id = None
        service_epoch = None
        placeholder = None
        try:
            for name in ("registration_id", "service_epoch"):
                if not isinstance(result.get(name), str) or not result[name]:
                    raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
            registration_id = result["registration_id"]
            service_epoch = result["service_epoch"]
            # Keep the exact remote cleanup identity before validating the
            # remaining receipt fields. Startup rollback can then unregister
            # even if validation or the caller is interrupted.
            placeholder = RegisteredBinding(
                mount=binding["mount"],
                service_id=binding["service_id"],
                instance_id=binding["instance_id"],
                scope_ref=binding["scope_ref"],
                scope=scope,
                registration_id=registration_id,
                service_epoch=service_epoch,
                operations=frozenset(operations),
                expires_at=0.0,
                policy_version="",
                requirements_version="",
                transport_ref=binding["transport_ref"],
                timeout=timeout,
                required=bool(required),
                active=False,
            )
            if self.sticky_cleanup_enabled:
                self._registration_in_progress["placeholder"] = placeholder
            placeholder_index = len(self._bindings)
            self._bindings.append(placeholder)
            if self.sticky_cleanup_enabled:
                self._registration_in_progress["handle_recorded"] = True
        except BaseException as error:
            if placeholder is None:
                # A successful remote registration without a locally retained
                # cleanup handle is explicitly uncertain, never silently lost.
                self._record_unknown_registration(registration_key)
                if self.sticky_cleanup_enabled:
                    self._set_cleanup_sticky(binding, scope, registration_key, error)
            else:
                try:
                    if not any(item is placeholder for item in self._bindings):
                        self._bindings.append(placeholder)
                except BaseException:
                    self._record_unknown_registration(registration_key)
                    if self.sticky_cleanup_enabled:
                        self._set_cleanup_sticky(placeholder, error)
                    raise
            raise
        try:
            for name in ("policy_version", "requirements_version"):
                if not isinstance(result.get(name), str) or not result[name]:
                    raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
            policy_metadata = self._registration_policy_metadata(result)
            granted = result.get("operations")
            if (
                not isinstance(granted, (list, tuple))
                or not all(isinstance(op, str) for op in granted)
                or set(granted) != operations
            ):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
            expiry = self._expiry(result)
            lease_remaining = self._lease_remaining(result)
            lease_deadline = request_started + lease_remaining
            if self._monotonic_now() >= lease_deadline:
                raise ServiceRuntimeError("LEASE_EXPIRED_DURING_REGISTRATION")
            final_binding = RegisteredBinding(
                mount=binding["mount"],
                service_id=binding["service_id"],
                instance_id=binding["instance_id"],
                scope_ref=binding["scope_ref"],
                scope=scope,
                registration_id=registration_id,
                service_epoch=service_epoch,
                operations=frozenset(operations),
                expires_at=expiry,
                policy_version=result["policy_version"],
                requirements_version=result["requirements_version"],
                transport_ref=binding["transport_ref"],
                timeout=timeout,
                lease_deadline=lease_deadline,
                renewal_due_at=self._renewal_deadline(
                    request_started, lease_remaining, heartbeat_interval_seconds
                ),
                required=bool(required),
                heartbeat_interval_seconds=float(heartbeat_interval_seconds)
                if heartbeat_interval_seconds is not None
                else None,
                access_policy_ref=policy_metadata[0],
                access_policy_version=policy_metadata[1],
                risk_policy_status=policy_metadata[2],
                risk_policy_ref=policy_metadata[3],
                risk_policy_version=policy_metadata[4],
                requirements_ref=policy_metadata[5],
            )
        except BaseException as original_error:
            if self.sticky_cleanup_enabled:
                self._strict_cleanup_entry(allow_registration=True)
                self._cleanup_in_progress = True
                try:
                    try:
                        self._strict_unregister_binding(placeholder)
                    except BaseException:
                        if not isinstance(original_error, Exception):
                            raise original_error
                        raise
                finally:
                    self._cleanup_in_progress = False
                raise
            try:
                cleanup_acknowledged = self._unregister_during_startup(
                    binding["service_id"],
                    binding["transport_ref"],
                    scope,
                    registration_id,
                    service_epoch,
                    timeout,
                )
            except BaseException:
                # Preserve uncertainty before allowing an interrupt to unwind;
                # the placeholder lets startup rollback retry the exact handle.
                self._unregister_uncertainties[registration_id] = None
                raise
            if not cleanup_acknowledged:
                self._unregister_uncertainties[registration_id] = None
            else:
                self._bindings = [item for item in self._bindings if item is not placeholder]
            raise
        if self.sticky_cleanup_enabled:
            self._registration_in_progress["final_binding"] = final_binding
        if (
            placeholder_index < len(self._bindings)
            and self._bindings[placeholder_index] is placeholder
        ):
            self._bindings[placeholder_index] = final_binding
        else:
            # This is defensive against an unexpected in-process mutation; the
            # known remote handle must remain represented for cleanup.
            self._bindings = [item for item in self._bindings if item is not placeholder]
            self._bindings.append(final_binding)
        # Bind this attempt's captured observation only after the final object
        # has passed validation and has actually been installed.
        observation = timing.get("registration_observation")
        if observation is not None:
            try:
                (transport_proof, observed_reply, observed_receipt, observed_result,
                 encoded_body, receipt_fields, observed_key, observed_scope,
                 observed_operations, observed_instance) = observation
                if (observed_result is result and observed_key == registration_key
                        and observed_reply.payload.get("result") is result
                        and encode_envelope(observed_reply) == encoded_body
                        and tuple(vars(observed_receipt).items()) == receipt_fields
                        and observed_scope == final_binding.scope
                        and observed_operations == final_binding.operations
                        and observed_instance == final_binding.instance_id
                        and self._registration_transport_is_current(transport_proof)
                        and any(item is final_binding for item in self._bindings)):
                    self._registration_lineage[id(final_binding)] = (
                        final_binding, tuple(vars(final_binding).items()), transport_proof,
                        observed_reply, observed_receipt, result, encoded_body, receipt_fields,
                        observed_key,
                    )
            except Exception:
                pass
        self._publish_execution_upstream_lease(final_binding, timing, lease_remaining)
        if self._state == "starting":
            # A successful register reply is retained before checking older
            # leases so startup rollback still has its exact cleanup handle.
            self._startup_check_required_leases()

    def _profile_max_message_bytes(self, transport_ref: str) -> int:
        profile = self._config["communication"]["profiles"].get(transport_ref)
        options = profile.get("options") if isinstance(profile, Mapping) else None
        value = options.get("max_message_bytes") if isinstance(options, Mapping) else None
        if type(value) is not int or not 1 <= value <= MAX_WIRE_MESSAGE_BYTES:
            raise ServiceRuntimeError("INVALID_ACCOUNT_TRANSPORT_OPTIONS")
        return value

    def _record_unknown_registration(self, registration_key: str) -> None:
        self._unknown_registration_count += 1
        self._last_unknown_registration_key = registration_key

    def _fold_unregister_uncertainty(self, registration_id: str) -> None:
        if registration_id in self._unregister_uncertainties:
            self._unregister_uncertainties.pop(registration_id, None)
            self._unresolved_unregister_count += 1
            self._last_unresolved_registration_id = registration_id
            self._unresolved_unregister_ids.append(registration_id)

    @staticmethod
    def _expiry(result: Mapping[str, Any]) -> float:
        value = result.get("expires_at")
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        return float(value)

    @staticmethod
    def _lease_remaining(result: Mapping[str, Any]) -> float:
        value = result.get("lease_remaining_seconds")
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ServiceRuntimeError("LEASE_TIMING_UNAVAILABLE")
        return float(value)

    @staticmethod
    def _valid_receipt_reference(value: object) -> bool:
        if type(value) is not str or not 1 <= len(value) <= 128:
            return False
        if not value[0].isascii() or not value[0].isalnum():
            return False
        return all(
            character.isascii() and (character.isalnum() or character in "._:-")
            for character in value
        )

    @classmethod
    def _registration_policy_metadata(
        cls, result: Mapping[str, Any]
    ) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None]:
        """Read the additive receipt schema without confusing legacy replies.

        Older service hosts return only ``policy_version`` and
        ``requirements_version``. That remains valid, but is represented as
        unknown metadata (all ``None``), never as explicit Risk N/A.
        """

        present = _REGISTRATION_POLICY_FIELDS.intersection(result)
        if not present:
            return None, None, None, None, None, None
        if present != _REGISTRATION_POLICY_FIELDS:
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")

        access_ref = result.get("access_policy_ref")
        access_version = result.get("access_policy_version")
        risk_status = result.get("risk_policy_status")
        risk_ref = result.get("risk_policy_ref")
        risk_version = result.get("risk_policy_version")
        requirements_ref = result.get("requirements_ref")
        policy_version = result.get("policy_version")

        if (
            not cls._valid_receipt_reference(access_ref)
            or type(access_version) is not str
            or not access_version
            or access_version != policy_version
            or not cls._valid_receipt_reference(requirements_ref)
        ):
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        if risk_status == "NOT_APPLICABLE":
            if risk_ref is not None or risk_version is not None:
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        elif risk_status == "APPLIED":
            if (
                not cls._valid_receipt_reference(risk_ref)
                or type(risk_version) is not str
                or len(risk_version) != 64
                or any(character not in "0123456789abcdef" for character in risk_version)
            ):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        else:
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        return (
            access_ref,
            access_version,
            risk_status,
            risk_ref,
            risk_version,
            requirements_ref,
        )

    @classmethod
    def _validate_renewal_receipt(
        cls, result: Mapping[str, Any], binding: RegisteredBinding
    ) -> None:
        if (
            result.get("registration_id") != binding.registration_id
            or result.get("service_epoch") != binding.service_epoch
        ):
            raise ServiceRuntimeError("RENEWAL_BINDING_MISMATCH")
        operations = result.get("operations")
        if (
            not isinstance(operations, (list, tuple))
            or not all(isinstance(operation, str) for operation in operations)
            or len(set(operations)) != len(operations)
            or frozenset(operations) != binding.operations
        ):
            raise ServiceRuntimeError("RENEWAL_BINDING_MISMATCH")
        for name, expected in (
            ("policy_version", binding.policy_version),
            ("requirements_version", binding.requirements_version),
        ):
            value = result.get(name)
            if not isinstance(value, str) or not value or value != expected:
                raise ServiceRuntimeError("RENEWAL_BINDING_MISMATCH")
        policy_metadata = cls._registration_policy_metadata(result)
        if policy_metadata != (
            binding.access_policy_ref,
            binding.access_policy_version,
            binding.risk_policy_status,
            binding.risk_policy_ref,
            binding.risk_policy_version,
            binding.requirements_ref,
        ):
            raise ServiceRuntimeError("RENEWAL_BINDING_MISMATCH")
        cls._expiry(result)
        cls._lease_remaining(result)

    def _bound_send_boundary(self, fence: _BoundSendFence, service_id: str, transport_ref: str,
                             scope: WireScope, operation: str, request: WireEnvelope,
                             timeout: float) -> tuple[object, WireIdentity, float, float]:
        """Recheck one local owner binding at the last controllable send boundary."""
        try:
            if (type(fence) is not _BoundSendFence or type(fence.binding) is not RegisteredBinding
                    or type(fence.peer) is not WireIdentity or type(fence.fingerprint) is not str
                    or type(fence.strict_facts) is not bool
                    or (fence.strict_facts and operation not in _ACCOUNT_FACTS_BOUND_OPERATIONS)
                    or self._monotonic_clock is not _BOUND_MONOTONIC or self._state != "registered"):
                raise ValueError
            binding = fence.binding
            def same_binding() -> bool:
                matches = [
                    b for b in self._bindings
                    if b.active and b.mount == fence.mount and operation in b.operations
                    and (fence.account_ref is None or b.scope.account_ref == fence.account_ref)
                    and (fence.portfolio_ref is None or b.scope.portfolio_ref == fence.portfolio_ref)
                    and (fence.scope_ref is None or b.scope_ref == fence.scope_ref)
                ]
                return (
                    self._state == "registered" and len(matches) == 1 and matches[0] is binding
                    and binding.active is True and binding.required is True
                    and binding.remote_lease_inactive is False
                    and service_id == binding.service_id and transport_ref == binding.transport_ref
                    and operation == fence.operation == request.header.operation
                    and scope is binding.scope and request.header.scope is not scope
                    and request.header.scope == binding.scope
                    and request.header.identity is not self.identity
                    and request.header.identity == self.identity
                    and _bound_binding_values(binding) == fence.binding_values
                    and _bound_runtime_values(self) == fence.runtime_values
                    and self._transports.get((service_id, transport_ref)) is fence.transport
                    and fence.transport is not None and self._peers.get(service_id) == fence.peer
                    and self._authenticated_registration_peers.get(
                        (service_id, transport_ref, binding.registration_id, binding.service_epoch)
                    ) == (fence.peer, fence.fingerprint)
                    and request.payload.get("registration_id") == binding.registration_id
                    and request.payload.get("service_epoch") == binding.service_epoch
                    and request.payload.get("instance_id") == binding.instance_id
                    and binding.lease_deadline == fence.lease_deadline
                )
            if (not same_binding() or type(fence.checked_at) not in (int, float)
                    or not math.isfinite(fence.checked_at)
                    or type(fence.lease_deadline) not in (int, float)
                    or not math.isfinite(fence.lease_deadline)
                    or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
                raise ValueError
            # This is the same native monotonic domain used for the bound lease.
            # Re-sample *after* envelope construction and routing checks.
            now = self._monotonic_now()
            if (type(now) not in (int, float) or not math.isfinite(now)
                    or now < fence.checked_at or now >= fence.lease_deadline):
                raise ValueError
            if not same_binding() or self._monotonic_clock is not _BOUND_MONOTONIC:
                raise ValueError
            send_now = _BOUND_MONOTONIC()
            if (type(send_now) not in (int, float) or not math.isfinite(send_now)
                    or send_now < now or send_now >= fence.lease_deadline):
                raise ValueError
            remaining_timeout = min(timeout, (fence.lease_deadline - send_now) * 0.8)
            if not math.isfinite(remaining_timeout) or remaining_timeout <= 0:
                raise ValueError
            return fence.transport, fence.peer, send_now, remaining_timeout
        except Exception:
            raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE") from None

    def _rpc(self, *args, **kwargs):
        self._assert_owner_thread()
        service_id = args[0] if args else kwargs.get('service_id')
        transport_ref = args[1] if len(args) > 1 else kwargs.get('transport_ref')
        operation = args[3] if len(args) > 3 else kwargs.get('operation')
        marker = kwargs.pop('_source_capture_marker', None)
        risk_first_marker = kwargs.pop('_risk_first_capture_marker', None)
        if (operation == 'direct.risk_first_reserve.v2'
                and (risk_first_marker is None or risk_first_marker is not self._risk_first_capture_marker
                     or kwargs.get('_require_issued_receipt') is not True)):
            raise ServiceRuntimeRequestNotSentError()
        if operation == 'direct.risk_first_reserve.v2':
            context = self._risk_first_send_context
            if type(context) is not tuple or len(context) != 4 or context[0] is not risk_first_marker:
                raise ServiceRuntimeRequestNotSentError()
            kwargs['_risk_first_context'] = context
        if (operation != 'direct.parent.source' or kwargs.get('_require_issued_receipt') is not True
                or marker is None or marker is not getattr(self, '_active_live_source_marker', None)):
            self._invalidate_live_parent_capabilities()
        try:
            return self._rpc_impl(*args, **kwargs)
        except BaseException:
            self._invalidate_live_parent_capabilities()
            key = (service_id, transport_ref)
            if not (self.sticky_cleanup_enabled and self._cleanup_in_progress):
                generations = getattr(self, '_registration_failure_generation', None)
                if type(generations) is dict:
                    generations[key] = generations.get(key, 0) + 1
                lineage = getattr(self, '_registration_lineage', None)
                if (type(lineage) is dict
                        and not (self.sticky_cleanup_enabled and operation == "service.register")):
                    self._registration_lineage = {
                        ident: proof for ident, proof in lineage.items()
                        if proof[2][0] != key
                    }
            raise

    def _rpc_impl(
        self,
        service_id,
        transport_ref,
        scope,
        operation,
        payload,
        *,
        control=True,
        timeout=None,
        include_started_at=False,
        started_at=None,
        timing=None,
        include_receive_receipt=False,
        _bound_send_fence: _BoundSendFence | None = None,
        bound_transport=None,
        bound_client=None,
        bound_socket_binding=None,
        bound_peer=None,
        bound_identity=None,
        deadline_monotonic=None,
        _require_issued_receipt=False,
        _risk_first_context=None,
        _override_transport=None,
    ):
        self._assert_owner_thread()
        if _override_transport is not None and (
            operation != "service.unregister"
            or _bound_send_fence is not None
            or bound_transport is not None
        ):
            raise ServiceRuntimeError("INVALID_OVERRIDE_TRANSPORT")
        if operation in ('service.register', 'service.renew'):
            self._execution_upstream_observation = None
        if _bound_send_fence is not None and bound_transport is not None:
            raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
        if (type(include_receive_receipt) is not bool or (include_receive_receipt and include_started_at)
                or type(_require_issued_receipt) is not bool
                or (_require_issued_receipt and (bound_transport is None or include_receive_receipt or include_started_at))):
            raise ServiceRuntimeError("INVALID_RECEIPT_REQUEST")
        if operation == 'service.register':
            for key in tuple(getattr(self, '_execution_upstream_clock_leases', {})):
                if key[:2] == (service_id, transport_ref):
                    self._execution_upstream_clock_leases.pop(key, None)
            verified = getattr(self, '_strict_registration_transports', {})
            for key in tuple(verified):
                if key[:2] == (service_id, transport_ref):
                    del verified[key]
            self._invalidate_live_parent_capabilities()
        profile = self._config["communication"]["profiles"][transport_ref]
        startup_rpc = self._state == "starting"
        rollback_rpc = self._startup_rollback
        configured_timeout = (
            timeout if timeout is not None else profile["options"]["request_timeout_ms"] / 1000
        )
        if rollback_rpc:
            if operation != "service.unregister":
                raise ServiceRuntimeError("STARTUP_ROLLBACK_IN_PROGRESS")
            effective_timeout = configured_timeout
        elif startup_rpc:
            try:
                self._startup_maintain_if_due()
                effective_timeout = self._startup_timeout(configured_timeout)
            except BaseException:
                if timing is not None:
                    timing["request_not_sent"] = True
                raise
        else:
            effective_timeout = configured_timeout
        # Bound headers must not retain the live binding/identity objects: they
        # can change after the last local check, including while clock sampling.
        # These detached frozen values remain claims, not authorization proofs.
        if _bound_send_fence is not None:
            try:
                request_identity = WireIdentity(**vars(self.identity))
                request_scope = WireScope(**vars(scope))
            except Exception:
                raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE") from None
        else:
            request_identity, request_scope = self.identity, scope
        request = WireEnvelope(
            WireHeader(
                protocol_version=WIRE_PROTOCOL_VERSION,
                kind=WireMessageKind.REQUEST,
                message_id=uuid.uuid4().hex,
                correlation_id=None,
                identity=(request_identity if bound_transport is None else bound_identity),
                scope=request_scope,
                operation=operation,
                schema_id="sdk.service.control" if control else "sdk.service.call",
                schema_version=1,
                sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            ),
            payload,
        )
        if startup_rpc and not rollback_rpc:
            try:
                # Recheck at the actual send boundary: envelope construction
                # must not consume the remaining lease or timeout margin.
                effective_timeout = self._startup_timeout(configured_timeout)
            except BaseException:
                if timing is not None:
                    timing["request_not_sent"] = True
                raise
        # No automatic retry: a timed-out write requires business reconciliation.
        # For an owner-bound call, the selected transport and peer must be the
        # same ones the owner authenticated, not a fresh lookup after checking.
        if _bound_send_fence is not None:
            if control or include_started_at or started_at is not None or timing is not None:
                raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
            transport, peer, request_started, effective_timeout = self._bound_send_boundary(
                _bound_send_fence, service_id, transport_ref, scope, operation, request, effective_timeout
            )
        else:
            if bound_transport is not None:
                if self._monotonic_clock is not _BOUND_MONOTONIC or not _bound_clock_current():
                    raise ServiceRuntimeRequestNotSentError()
            request_started = self._monotonic_now()
        if (timing is not None and getattr(self, '_execution_upstream_lease_enabled', False) is True
                and self.identity.role is WireRole.SERVICE
                and operation in ('service.register', 'service.renew')):
            try:
                timing['execution_sleep_started'] = _ExecutionSuspendClock.now(self._execution_upstream_clock)
            except (AttributeError, ValueError):
                timing['execution_sleep_started'] = None
        bound_started_ns = None
        if bound_transport is not None and (include_receive_receipt or _require_issued_receipt):
            bound_started_ns = _BOUND_MONOTONIC_NS()
            if not _bound_clock_current() or type(bound_started_ns) is not int:
                raise ServiceRuntimeRequestNotSentError()
        if started_at is not None:
            started_at.append(request_started)
        if timing is not None:
            timing["request_started"] = request_started
        wall_deadline = None
        try:
            if (_bound_send_fence is not None and (operation in {
                    "account.cancel_attempts.read_after", "account.cancel_attempts.ack"}
                    or (_bound_send_fence.strict_facts and operation in _ACCOUNT_FACTS_BOUND_OPERATIONS))):
                # A cancellation-stream receipt must be obtained and consumed
                # on the registration's original local socket. The transport
                # enforces its object lineage; this SDK owner verifies the
                # separately held registration, routing and lease facts.
                binding = _bound_send_fence.binding
                if not self._has_current_registration_observation(binding):
                    raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                proof = self._registration_lineage.get(id(binding))
                if type(proof) is not tuple or type(transport) is not _RequestNotSentAdapter:
                    raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                transport_proof = proof[2]
                client = transport._transport
                strict_registration = None
                if _bound_send_fence.strict_facts:
                    from bt_api_transport_zmq.service_v2 import _ServiceSocketBinding, _strict_client_binding
                    key = (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)
                    strict_registration = self._strict_registration_transports.get(key)
                    if (type(self._factory) is not ZmqTransportFactory or self._factory._require_factory_mode() is not True
                            or type(strict_registration) is not tuple or len(strict_registration) != 3
                            or self._authenticated_registration_transports.get(key) is not strict_registration
                            or strict_registration[0] is not transport or strict_registration[1] is not client
                            or type(strict_registration[2]) is not _ServiceSocketBinding
                            or strict_registration[2].strict_single_connection is not True
                            or client._strict_single_connection is not True
                            or _strict_client_binding(client) is not strict_registration[2]
                            or not client._socket_binding_current(strict_registration[2])):
                        raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                lineage = (client, transport_proof[3], transport_proof[4], transport_proof[5],
                           transport_proof[6], transport_proof[7], client._receiver)

                def verify_owner() -> None:
                    if (self._registration_lineage.get(id(binding)) is not proof
                            or not self._has_current_registration_observation(binding, _inflight=True)
                            or hashlib.sha256(client._server_key).hexdigest() != _bound_send_fence.fingerprint):
                        raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                    if (_bound_send_fence.strict_facts and
                            (self._strict_registration_transports.get(key) is not strict_registration
                             or self._authenticated_registration_transports.get(key) is not strict_registration
                             or self._factory._require_factory_mode() is not True
                             or client._strict_single_connection is not True
                             or _strict_client_binding(client) is not strict_registration[2]
                             or not client._socket_binding_current(strict_registration[2]))):
                        raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                    selected, selected_peer, _, _ = self._bound_send_boundary(
                        _bound_send_fence, service_id, transport_ref, scope, operation, request, effective_timeout
                    )
                    if selected is not transport or selected_peer != peer:
                        raise ServiceRuntimeError("BOUND_ROUTE_UNAVAILABLE")

                reply = transport._request_bound(
                    request, timeout=effective_timeout, lineage=lineage, verify_owner=verify_owner
                )
            elif _bound_send_fence is not None:
                reply = transport.request(request, timeout=effective_timeout)
            elif bound_transport is None:
                active_transport = (
                    _override_transport
                    if _override_transport is not None
                    else self._transports[(service_id, transport_ref)]
                )
                reply = active_transport.request(
                    request, timeout=effective_timeout
                )
            elif _require_issued_receipt:
                if operation == 'direct.risk_first_reserve.v2':
                    from bt_api_py.risk_gateway_first_reserve_client import RiskGatewayFirstReserveBridge
                    from bt_api_transport_zmq import service_v2 as zmq_service
                    if (type(_risk_first_context) is not tuple or len(_risk_first_context) != 4
                            or self._risk_first_send_context is not _risk_first_context
                            or _risk_first_context[0] is not self._risk_first_capture_marker
                            or type(_risk_first_context[1]) is not RiskGatewayFirstReserveBridge
                            or encode_wire_value(request.payload['arguments']) != _risk_first_context[3]):
                        raise ServiceRuntimeRequestNotSentError()
                    bridge, attempt = _risk_first_context[1:3]
                    # This short local check is after envelope projection and
                    # releases Registry/SQLite before entering the transport.
                    wall_deadline = bridge._check_attempt(attempt)
                    if (self._risk_first_send_context is not _risk_first_context
                            or encode_wire_value(request.payload['arguments']) != _risk_first_context[3]
                            or type(wall_deadline) is not int or wall_deadline <= 0
                            or time.time_ns is not _BOUND_RISK_WALL_NS
                            or zmq_service._BOUND_WALL_NS is not _BOUND_RISK_WALL_NS
                            or zmq_service._bound_wall_clock_current is not zmq_service._TRUSTED_BOUND_WALL_CLOCK_CHECK
                            or not zmq_service._bound_wall_clock_current() or not _bound_clock_current()):
                        raise ServiceRuntimeRequestNotSentError()
                    before = _BOUND_MONOTONIC()
                    wall_now = _BOUND_RISK_WALL_NS()
                    if type(wall_now) is not int or wall_now >= wall_deadline:
                        raise ServiceRuntimeRequestNotSentError()
                    risk_deadline = math.nextafter(before + (wall_deadline - wall_now) / 1_000_000_000,
                                                   -math.inf)
                    deadline_monotonic = min(deadline_monotonic, risk_deadline)
                    remaining = deadline_monotonic - _BOUND_MONOTONIC()
                    if not math.isfinite(remaining) or remaining <= 0:
                        raise ServiceRuntimeRequestNotSentError()
                    effective_timeout = min(effective_timeout, remaining)
                bound_options = {} if wall_deadline is None else {'_send_wall_deadline_ns': wall_deadline}
                reply = bound_transport.request_bound(request, timeout=effective_timeout,
                    deadline_monotonic=deadline_monotonic, transport=bound_client,
                    socket_binding=bound_socket_binding, _trusted_request=True, **bound_options)
            else:
                reply = bound_transport.request_bound(request, timeout=effective_timeout,
                    deadline_monotonic=deadline_monotonic, transport=bound_client,
                    socket_binding=bound_socket_binding)
        except ServiceRuntimeRequestNotSentError:
            if timing is not None:
                timing["request_not_sent"] = True
            raise
        except BaseException:
            if _require_issued_receipt:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation) from None
            raise

        def check_bound_clock_after_reply() -> None:
            wall_invalid = (operation == 'direct.risk_first_reserve.v2' and
                            (type(wall_deadline) is not int or time.time_ns is not _BOUND_RISK_WALL_NS
                             or _BOUND_RISK_WALL_NS() >= wall_deadline))
            if bound_transport is not None and (not _bound_clock_current() or wall_invalid):
                # The request was dispatched. Retain its uncertain identity
                # and retire the exact captured socket on this owner thread.
                bound_client._last_uncertain_message_id = request.header.message_id
                with suppress(Exception):
                    bound_client._retire_captured_socket(bound_socket_binding.socket)
                with suppress(Exception):
                    bound_client._socket_message_ids.clear()
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)

        check_bound_clock_after_reply()
        try:
            expected_peer = (peer if _bound_send_fence is not None else
                             self._peers[service_id] if bound_transport is None else bound_peer)
            validate_reply(reply, request, expected_peer=expected_peer)
        except Exception:
            if _require_issued_receipt:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation) from None
            raise
        check_bound_clock_after_reply()
        if bound_transport is not None and _BOUND_MONOTONIC() >= deadline_monotonic:
            raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
        receipt = None

        def bound_receipt_binding_current() -> bool:
            try:
                from bt_api_transport_zmq.service_v2 import ZmqServiceClient, _ServiceSocketBinding
                if (type(bound_transport) is not _RequestNotSentAdapter or type(bound_client) is not ZmqServiceClient
                        or type(bound_socket_binding) is not _ServiceSocketBinding
                        or bound_transport._transport is not bound_client
                        or self._transports.get((service_id, transport_ref)) is not bound_transport
                        or not bound_client._socket_binding_current(bound_socket_binding)):
                    return False
                registration_id = request.payload.get('registration_id')
                service_epoch = request.payload.get('service_epoch')
                instance_id = request.payload.get('instance_id')
                if not all(type(value) is str and value for value in (registration_id, service_epoch, instance_id)):
                    return False
                proof = self._authenticated_registration_transports.get(
                    (service_id, transport_ref, registration_id, service_epoch))
                if (type(proof) is not tuple or len(proof) != 3
                        or proof[0] is not bound_transport or proof[1] is not bound_client
                        or proof[2] is not bound_socket_binding):
                    return False
                matches = [binding for binding in self._bindings
                           if type(binding) is RegisteredBinding and binding.active is True
                           and binding.required is True and binding.service_id == service_id
                           and binding.instance_id == instance_id and binding.scope == request.header.scope
                           and binding.transport_ref == transport_ref and binding.registration_id == registration_id
                           and binding.service_epoch == service_epoch and operation in binding.operations
                           and binding.lease_deadline > _BOUND_MONOTONIC()]
                return (len(matches) == 1 and self.identity == bound_identity
                        and self.authenticated_peer_for_binding(matches[0]) == (
                            bound_peer, hashlib.sha256(bound_client._server_key).hexdigest()))
            except (ImportError, AttributeError, KeyError, TypeError, ValueError):
                return False

        if include_receive_receipt or _require_issued_receipt:
            try:
                from bt_api_transport_zmq.service_v2 import ServiceReplyReceipt
                if bound_transport is not None:
                    candidate = (bound_client.last_reply_receipt
                                 if bound_receipt_binding_current() else None)
                else:
                    candidate = getattr(
                        transport if _bound_send_fence is not None
                        else self._transports[(service_id, transport_ref)],
                        "last_reply_receipt", None,
                    )
                if (type(candidate) is ServiceReplyReceipt
                        and candidate.request_message_id == request.header.message_id
                        and candidate.reply_message_id == reply.header.message_id
                        and candidate.correlation_id == request.header.message_id == reply.header.correlation_id
                        and candidate.operation == operation == reply.header.operation
                        and candidate.pid == os.getpid() and candidate.thread_id == threading.get_ident()
                        and type(candidate.received_wall_ns) is int and candidate.received_wall_ns > 0
                        and type(candidate.received_monotonic_ns) is int
                        and 0 < candidate.received_monotonic_ns
                        and (bound_transport is None or
                             (getattr(candidate, '_socket_binding', None) is bound_socket_binding
                              and bound_receipt_binding_current()))
                        and (_bound_receipt_time_valid(candidate.received_monotonic_ns, bound_started_ns,
                                                       deadline_monotonic) if bound_transport is not None
                             else candidate.received_monotonic_ns <= time.monotonic_ns())):
                    receipt = candidate
            except (ImportError, RuntimeError, AttributeError):
                receipt = None
        if _require_issued_receipt:
            try:
                from bt_api_transport_zmq.service_v2 import _consume_strict_reply_receipt
                if receipt is None or not _consume_strict_reply_receipt(
                        bound_client, receipt, request, reply, bound_socket_binding, deadline_monotonic):
                    raise ValueError
            except Exception:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation) from None
        body = reply.payload
        if body.get("ok") is not True:
            if timing is not None and self._is_definitive_control_rejection(operation, body):
                timing["server_rejected"] = True
            code = body.get("code")
            if not control and (
                not isinstance(code, str)
                or code not in SERVICE_ERROR_CODES
                or code == "HANDLER_FAILED"
            ):
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
            failure = ServiceRuntimeError(
                code
                if isinstance(code, str) and code in SERVICE_ERROR_CODES
                else "REMOTE_REQUEST_REJECTED"
            )
            details = body.get("result")
            if isinstance(details, Mapping) and details:
                failure.result = dict(details)
            if failure.code == code:
                failure.service_reply_code = code
            check_bound_clock_after_reply()
            if bound_transport is not None and _BOUND_MONOTONIC() >= deadline_monotonic:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
            raise failure
        result = body.get("result")
        if not isinstance(result, Mapping):
            if not control:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        if operation == "service.renew" and timing is not None:
            # Capture only the exact authenticated transport's validated
            # renewal response; this is not a replacement registration grant.
            try:
                from bt_api_transport_zmq.service_v2 import ZmqServiceClient
                adapter = self._transports[(service_id, transport_ref)]
                client = adapter._transport if type(adapter) is _RequestNotSentAdapter else None
                if type(client) is ZmqServiceClient:
                    candidate = client.last_reply_receipt
                    server_key, client_key = client._fixed_curve_keys
                    if client._is_current_renewal_observation(
                            reply, candidate, server_public_key=server_key, client_public_key=client_key):
                        timing["renewal_observation"] = (
                            ((service_id, transport_ref), adapter, client, client._socket,
                             client._context, client._socket_generation, server_key, client_key,
                             self._registration_failure_generation.get((service_id, transport_ref), 0)),
                            reply, candidate, result, encode_envelope(reply), tuple(vars(candidate).items()),
                            request.payload.get("registration_id"), request.payload.get("service_epoch"),
                        )
            except Exception:
                pass
        if operation == "service.register" and timing is not None:
            # The backend observation expires at the next request. Verify it
            # now while this exact reply and receipt are still current.
            try:
                from bt_api_transport_zmq.service_v2 import ZmqServiceClient
                adapter = self._transports[(service_id, transport_ref)]
                client = adapter._transport if type(adapter) is _RequestNotSentAdapter else None
                if type(client) is ZmqServiceClient:
                    candidate = client.last_reply_receipt
                    server_key, client_key = client._fixed_curve_keys
                    if (type(request.payload.get("registration_key")) is str
                            and client.is_current_registration_observation(
                                reply, candidate, server_public_key=server_key,
                                client_public_key=client_key)):
                        timing["registration_observation"] = (
                            ((service_id, transport_ref), adapter, client, client._socket,
                             client._context, client._socket_generation, server_key, client_key,
                             self._registration_failure_generation.get((service_id, transport_ref), 0)),
                            reply, candidate, result,
                            encode_envelope(reply),
                            tuple(vars(candidate).items()), request.payload["registration_key"],
                            request.header.scope, frozenset(request.payload["operations"]),
                            request.payload["instance_id"],
                        )
            except Exception:
                pass
        if operation == 'service.register':
            # Evidence is published only after an exact ZMQ/CURVE transport
            # validated this successful register reply and its correlation.
            try:
                from bt_api_transport_zmq.service_v2 import (
                    ServiceReplyReceipt,
                    ZmqServiceClient,
                    _ServiceSocketBinding,
                )
                transport = self._transports[(service_id, transport_ref)]
                candidate = transport.last_reply_receipt if type(transport) is _RequestNotSentAdapter else None
                server = transport._transport if type(transport) is _RequestNotSentAdapter else None
                socket_binding = getattr(candidate, '_socket_binding', None) if type(candidate) is ServiceReplyReceipt else None
                if (type(server) is ZmqServiceClient and type(candidate) is ServiceReplyReceipt
                        and candidate.request_message_id == request.header.message_id
                        and candidate.reply_message_id == reply.header.message_id
                        and candidate.correlation_id == request.header.message_id == reply.header.correlation_id
                        and candidate.operation == operation == reply.header.operation
                        and candidate.pid == os.getpid() and candidate.thread_id == threading.get_ident()
                        and type(socket_binding) is _ServiceSocketBinding
                        and type(socket_binding.server_key) is bytes and len(socket_binding.server_key) == 40
                        and socket_binding.peer == self._peers[service_id]
                        and type(result.get('registration_id')) is str and type(result.get('service_epoch')) is str):
                    proof_key = (service_id, transport_ref, result['registration_id'], result['service_epoch'])
                    existing_proof = self._authenticated_registration_transports.get(proof_key)
                    shared_proof = (existing_proof if type(existing_proof) is tuple and len(existing_proof) == 3
                        and existing_proof[0] is transport and existing_proof[1] is server
                        and existing_proof[2] is socket_binding else (transport, server, socket_binding))
                    try:
                        from bt_api_transport_zmq.service_v2 import (
                            _consume_strict_reply_receipt, _strict_client_binding,
                        )
                        if (_strict_client_binding(server) is socket_binding
                                and _consume_strict_reply_receipt(server, candidate, request, reply,
                                                                  socket_binding, None)):
                            self._strict_registration_transports[proof_key] = shared_proof
                    except (ImportError, RuntimeError, AttributeError, KeyError, TypeError, ValueError):
                        pass
                    self._authenticated_registration_peers[proof_key] = (
                        socket_binding.peer, hashlib.sha256(socket_binding.server_key).hexdigest())
                    if proof_key not in self._authenticated_registration_transports:
                        self._authenticated_registration_transports[proof_key] = shared_proof
                    elif (self._authenticated_registration_transports[proof_key] is None
                          or self._authenticated_registration_transports[proof_key][0] is not transport
                          or self._authenticated_registration_transports[proof_key][1] is not server
                          or self._authenticated_registration_transports[proof_key][2] is not socket_binding):
                        self._authenticated_registration_transports[proof_key] = None
                    if (timing is not None and self._strict_registration_transports.get(proof_key) is shared_proof
                            and self._authenticated_registration_transports.get(proof_key) is shared_proof):
                        observation = (object(), operation, request, reply, shared_proof,
                                       timing.get('execution_sleep_started'))
                        self._execution_upstream_observation = observation
                        timing['execution_control_observation'] = observation
            except (ImportError, RuntimeError, AttributeError, KeyError):
                pass
        elif operation == 'service.renew' and timing is not None:
            try:
                from bt_api_transport_zmq.service_v2 import _consume_strict_reply_receipt
                key = (service_id, transport_ref, payload.get('registration_id'), payload.get('service_epoch'))
                proof = self._strict_registration_transports.get(key)
                if (type(proof) is tuple and len(proof) == 3
                        and self._authenticated_registration_transports.get(key) is proof
                        and self._transports.get((service_id, transport_ref)) is proof[0]
                        and proof[0]._transport is proof[1]
                        and _consume_strict_reply_receipt(proof[1], proof[0].last_reply_receipt,
                                                          request, reply, proof[2], None)):
                    observation = (object(), operation, request, reply, proof,
                                   timing.get('execution_sleep_started'))
                    self._execution_upstream_observation = observation
                    timing['execution_control_observation'] = observation
            except (ImportError, RuntimeError, AttributeError, KeyError, TypeError, ValueError):
                pass
        if startup_rpc and not rollback_rpc and operation != "service.register":
            self._startup_check_required_leases()
        check_bound_clock_after_reply()
        if bound_transport is not None and _BOUND_MONOTONIC() >= deadline_monotonic:
            raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
        if include_started_at:
            return result, request_started
        if _require_issued_receipt:
            return result, receipt
        if include_receive_receipt:
            if bound_transport is not None and receipt is not None and not bound_receipt_binding_current():
                receipt = None
            return result, receipt
        return result

    @staticmethod
    def _is_definitive_control_rejection(operation: str, body: Mapping[str, Any]) -> bool:
        """Accept only exact, known pre-side-effect rejection responses."""

        if body.get("ok") is not False:
            return False
        code = body.get("code")
        if type(code) is not str:
            return False
        if operation == "service.register":
            return code in {
                "CAPACITY",
                "CAPABILITY_UNAVAILABLE",
                "CLOSED",
                "DENIED",
                "DUPLICATE_ACTIVE_RUN",
                "POLICY_REQUIREMENT_UNMET",
                "SCOPE_DENIED",
                "UNKNOWN_OPERATION",
            }
        if operation == "service.renew":
            return code in {
                "DENIED",
                "NOT_REGISTERED",
                "POLICY_REQUIREMENT_UNMET",
                "SCOPE_DENIED",
                "STALE_EPOCH",
            }
        if operation == "service.unregister":
            return code in {"DENIED", "NOT_REGISTERED", "SCOPE_DENIED", "STALE_EPOCH"}
        return code in {
            "CAPACITY",
            "CAPABILITY_UNAVAILABLE",
            "CLOSED",
            "CONFLICT",
            "DENIED",
            "DUPLICATE_ACTIVE_RUN",
            "NOT_REGISTERED",
            "POLICY_REQUIREMENT_UNMET",
            "SCOPE_DENIED",
            "STALE_EPOCH",
            "UNKNOWN_OPERATION",
        }

    def _released_run_adoption_run_id(
        self, error: ServiceRuntimeError, scope: WireScope
    ) -> str | None:
        """Return the released durable run this registration may adopt.

        Only a strategy client, only an exact DUPLICATE_ACTIVE_RUN rejection,
        and only a server-proved released claim (the registry reports the
        durable run id exclusively after the previous session unregistered,
        expired, or lost its epoch) may adopt another logical run.
        """

        if (
            error.code != "DUPLICATE_ACTIVE_RUN"
            or self.identity.role is not WireRole.STRATEGY_CLIENT
        ):
            return None
        run_id = error.result.get("strategy_write_run_id")
        if type(run_id) is not str or not run_id or run_id == scope.run_id:
            return None
        return run_id

    def _unregister_binding(self, binding: RegisteredBinding) -> bool:
        if binding.remote_lease_inactive and not self._require_unregister_ack and not self.sticky_cleanup_enabled:
            self._unregister_uncertainties.pop(binding.registration_id, None)
            return True
        unregister = (
            self._unregister_during_startup if self._state == "starting" else self._unregister
        )
        return unregister(
            binding.service_id,
            binding.transport_ref,
            binding.scope,
            binding.registration_id,
            binding.service_epoch,
            binding.timeout,
        )

    def _strict_start_check(self) -> None:
        if (self.sticky_cleanup_enabled and self._start_in_progress
                and self._has_sticky_cleanup()):
            raise ServiceRuntimeError("CLOSE_FAILED")

    def _strict_public_lifecycle_entry(self) -> None:
        if self.sticky_cleanup_enabled and self._start_in_progress:
            error = ServiceRuntimeError("CLOSE_FAILED")
            self._set_cleanup_sticky("startup_in_progress", error)
            raise error
        self._strict_cleanup_entry()

    def _strict_cleanup_entry(self, *, allow_registration: bool = False) -> None:
        if (self.sticky_cleanup_enabled and self._registration_in_progress is not None
                and not allow_registration):
            error = ServiceRuntimeError("CLOSE_FAILED")
            self._set_cleanup_sticky(self._registration_in_progress, error)
            raise error
        if self.sticky_cleanup_enabled and (self._has_sticky_cleanup() or self._cleanup_in_progress):
            raise ServiceRuntimeError("CLOSE_FAILED")

    def _strict_business_entry(self) -> None:
        if not self.sticky_cleanup_enabled:
            return
        if self._has_sticky_cleanup():
            raise ServiceRuntimeError("CLOSE_FAILED")
        if self._cleanup_in_progress:
            error = ServiceRuntimeError("CLOSE_FAILED")
            self._set_cleanup_sticky(error)
            raise error
        # The guard itself is external code and may synchronously reenter.
        self._cleanup_in_progress = True
        try:
            self._strict_guard()
        finally:
            self._cleanup_in_progress = False

    def _set_cleanup_sticky(self, *evidence: Any) -> None:
        # A single asynchronous exception between setting the flag and
        # installing the root must still run the rooting attempt. Python cannot
        # guarantee retention if rooting itself is interrupted or fails.
        try:
            self._cleanup_sticky = True
        finally:
            with _RAW_STICKY_CLEANUP_ROOTS_LOCK:
                _RAW_STICKY_CLEANUP_ROOTS.setdefault(id(self), self)
        # A signal can synchronously reenter even while the RLock is held.
        # Elect one immutable evidence snapshot; idempotent assignments make
        # a resumed or reentrant initializer preserve the same first snapshot.
        candidate = (object(), self._cleanup_failures,
                     (evidence, tuple(self._bindings), tuple(self._transports.items()),
                      tuple(self._registration_lineage.items())))
        elected = self._cleanup_latch.setdefault("owner", candidate)
        if elected is not candidate:
            self._cleanup_reentrant_evidence.append(evidence)
        self._state = "close_failed"
        self._cleanup_failures = max(self._cleanup_failures, elected[1] + 1)
        self._cleanup_evidence = elected[2]
        notification = object()
        if self._cleanup_latch.setdefault("notification", notification) is notification:
            try:
                self._on_cleanup_sticky()
            except BaseException:
                # Notification cannot replace the error that caused uncertainty.
                pass

    def _strict_resource_snapshot(self) -> tuple[Any, ...]:
        return (tuple(self._bindings), tuple(self._transports.items()),
                dict(self._registration_failure_generation))

    def _strict_verify_resources(self, snapshot: tuple[Any, ...]) -> None:
        bindings, transports, generations = snapshot
        unchanged = (
            len(bindings) == len(self._bindings)
            and all(before is after for before, after in zip(bindings, self._bindings))
            and len(transports) == len(self._transports)
            and all(key in self._transports and self._transports[key] is transport
                    for key, transport in transports)
            and generations == self._registration_failure_generation
        )
        if not unchanged:
            error = ServiceRuntimeError("CLOSE_FAILED")
            self._set_cleanup_sticky(snapshot, error)
            raise error

    def _strict_guard(self, *evidence: Any) -> None:
        snapshot = self._strict_resource_snapshot()
        try:
            safe = self._cleanup_unknown()
            self._strict_verify_resources(snapshot)
            if safe is not False or self._has_sticky_cleanup():
                raise ServiceRuntimeError("CLOSE_FAILED")
        except BaseException as error:
            self._set_cleanup_sticky(*evidence, snapshot, error)
            raise

    def _strict_unregister_binding(self, binding: RegisteredBinding) -> None:
        key = (binding.service_id, binding.transport_ref)
        transport = self._transports.get(key)
        self._strict_guard(binding, transport)
        snapshot = self._strict_resource_snapshot()
        try:
            if transport is None or self._unregister_binding(binding) is not True:
                raise ServiceRuntimeError("CLOSE_FAILED")
            self._strict_verify_resources(snapshot)
            self._strict_guard(binding, transport)
            self._bindings = [item for item in self._bindings if item is not binding]
            self._registration_lineage.pop(id(binding), None)
            if (self._registration_in_progress is not None
                    and self._registration_in_progress.get("placeholder") is binding):
                self._registration_in_progress["released"] = True
        except BaseException as error:
            self._set_cleanup_sticky(binding, transport, snapshot, error)
            self._unregister_uncertainties[binding.registration_id] = None
            raise

    def _strict_release_bindings(self, first: int) -> None:
        for binding in tuple(self._bindings[first:]):
            self._strict_unregister_binding(binding)

    def _ephemeral_unregister_transport_for_retired_strict(
        self, service_id: str, transport_ref: str, *, allow_disconnected_strict: bool = False
    ) -> Any | None:
        """Create a one-shot unregister-only transport when a strict socket cannot send.

        ``strict_single_connection=True`` intentionally sets ``RECONNECT_IVL=-1``
        and refuses opening a second socket on the original client after
        retirement or peer restart. Business RPCs and renewals must still fail on
        that original client, but ``service.unregister`` is an idempotent
        revocation that requires an authenticated server acknowledgment so a
        transient timeout or Gateway restart cannot permanently trap
        ``require_unregister_ack=True`` cleanup. The caller must always close the
        returned transport immediately without mutating ``self._transports``.
        """
        existing = self._transports.get((service_id, transport_ref))
        if type(existing) is not _RequestNotSentAdapter:
            return None
        client = existing._transport
        if (
            getattr(client, "_strict_single_connection", False) is not True
            or getattr(client, "_strict_socket_opened", False) is not True
            or (
                not allow_disconnected_strict
                and getattr(client, "_socket", None) is not None
                and getattr(client, "remaining_socket_message_ids", 1) != 0
            )
            or getattr(client, "_closed", True) is True
        ):
            return None
        if type(self._factory) is not ZmqTransportFactory:
            return None
        if self._factory._require_factory_mode() is not True:
            return None
        profile = self._config["communication"]["profiles"][transport_ref]
        peer = self._peers[service_id]
        return self._factory(profile, peer)

    def _unregister(
        self,
        service_id,
        transport_ref,
        scope,
        registration_id,
        epoch,
        timeout,
    ):
        timing: dict[str, Any] = {}
        ephemeral_transport = None
        try:
            ephemeral_transport = self._ephemeral_unregister_transport_for_retired_strict(
                service_id, transport_ref
            )
            rpc_kwargs: dict[str, Any] = {"timeout": timeout, "timing": timing}
            if ephemeral_transport is not None:
                rpc_kwargs["_override_transport"] = ephemeral_transport
            try:
                receipt = self._rpc(
                    service_id,
                    transport_ref,
                    scope,
                    "service.unregister",
                    {"registration_id": registration_id, "service_epoch": epoch},
                    **rpc_kwargs,
                )
            except ServiceRuntimeRequestNotSentError:
                if ephemeral_transport is not None:
                    raise
                # A strict_single_connection socket (RECONNECT_IVL=-1) cannot
                # reconnect after peer restart, so POLLOUT times out with
                # RequestNotSentError without retiring _socket. Retry the
                # idempotent unregister once over a fresh one-shot transport.
                ephemeral_transport = self._ephemeral_unregister_transport_for_retired_strict(
                    service_id, transport_ref, allow_disconnected_strict=True
                )
                if ephemeral_transport is None:
                    raise
                receipt = self._rpc(
                    service_id,
                    transport_ref,
                    scope,
                    "service.unregister",
                    {"registration_id": registration_id, "service_epoch": epoch},
                    timeout=timeout,
                    timing=timing,
                    _override_transport=ephemeral_transport,
                )
            if (
                receipt.get("unregistered") is not True
                or receipt.get("registration_id") != registration_id
            ):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
            self._unregister_uncertainties.pop(registration_id, None)
            return True
        except ServiceRuntimeError as error:
            if self.sticky_cleanup_enabled:
                raise
            if not self._require_unregister_ack and timing.get("server_rejected") is True and error.code in {
                "NOT_REGISTERED",
                "STALE_EPOCH",
            }:
                self._unregister_uncertainties.pop(registration_id, None)
                return True
            self._cleanup_failures += 1
            self._unregister_uncertainties[registration_id] = None
            return False
        except Exception:
            if self.sticky_cleanup_enabled:
                raise
            self._cleanup_failures += 1
            self._unregister_uncertainties[registration_id] = None
            return False
        finally:
            if ephemeral_transport is not None:
                ephemeral_transport.close()

    def _release_bindings(self, first=0, *, capture_base_exceptions=False) -> None:
        if self.sticky_cleanup_enabled:
            self._strict_cleanup_entry()
            self._cleanup_in_progress = True
            try:
                self._strict_guard()
                self._strict_release_bindings(first)
                self._strict_guard()
            finally:
                self._cleanup_in_progress = False
            return
        for binding in self._bindings[first:]:
            self._registration_lineage.pop(id(binding), None)
        retained = self._bindings[:first]
        for binding in self._bindings[first:]:
            key = (binding.service_id, binding.transport_ref)
            if binding.remote_lease_inactive and not self._require_unregister_ack:
                self._unregister_uncertainties.pop(binding.registration_id, None)
                continue
            if key not in self._transports:
                self._unregister_uncertainties[binding.registration_id] = None
                self._fold_unregister_uncertainty(binding.registration_id)
                continue
            try:
                acknowledged = self._unregister_binding(binding)
            except BaseException:
                if not capture_base_exceptions:
                    raise
                self._cleanup_failures += 1
                self._unregister_uncertainties[binding.registration_id] = None
                acknowledged = False
            if acknowledged:
                continue
            retained.append(replace(binding, active=False))
        self._bindings = retained

    def _cleanup_closed_zmq_proofs(self, client: Any, captured_bindings: tuple[Any, ...]) -> None:
        """Drop only evidence of this confirmed-closed client when unshared."""
        try:
            from bt_api_transport_zmq import service_v2 as zmq_service
            ZmqServiceClient = zmq_service.ZmqServiceClient
            _ServiceSocketBinding = zmq_service._ServiceSocketBinding
            startup_owner_type = getattr(zmq_service, '_ServiceClientStartupOwner', None)
        except (ImportError, AttributeError):
            return

        if type(client) is not ZmqServiceClient or getattr(client, '_closed', False) is not True:
            return

        def owns_client(transport: Any) -> bool:
            if transport is client:
                return True
            if type(transport) is _RequestNotSentAdapter:
                return transport._transport is client
            if type(transport) is _PendingZmqTransport:
                held = transport._transport
                if held is client or (type(held) is _RequestNotSentAdapter and held._transport is client):
                    return True
                return (startup_owner_type is not None and type(held) is startup_owner_type
                        and held._client is client)
            return False

        if any(owns_client(transport) for transport in self._transports.values()):
            return
        binding_candidates = list(captured_bindings)
        removed_keys = set()
        strict_proofs = getattr(self, '_strict_registration_transports', {})
        authenticated_proofs = getattr(self, '_authenticated_registration_transports', {})
        for proofs in (strict_proofs, authenticated_proofs):
            for proof_key, proof in tuple(proofs.items()):
                if (type(proof) is tuple and len(proof) == 3 and proof[1] is client
                        and type(proof[0]) is _RequestNotSentAdapter and getattr(proof[0], '_transport', None) is client
                        and type(proof[2]) is _ServiceSocketBinding):
                    binding_candidates.append(proof[2])
                    del proofs[proof_key]
                    removed_keys.add(proof_key)
        for proof_key in removed_keys:
            if proof_key not in strict_proofs and proof_key not in authenticated_proofs:
                getattr(self, '_authenticated_registration_peers', {}).pop(proof_key, None)
        # A binding can still be held by a different proof/client. Do not erase
        # its high-water state until no remaining registration refers to it.
        remaining = tuple(strict_proofs.values()) + tuple(authenticated_proofs.values())
        for binding in binding_candidates:
            if (type(binding) is not _ServiceSocketBinding
                    or any(type(proof) is tuple and len(proof) == 3 and proof[2] is binding for proof in remaining)
                    or any(type(transport) is _RequestNotSentAdapter
                           and getattr(transport._transport, '_socket_binding', None) is binding
                           for transport in self._transports.values())):
                continue
            for revisions in (getattr(self, '_live_parent_revisions', {}),
                              getattr(self, '_live_parent_source_identities', {})):
                for source_key in tuple(revisions):
                    if type(source_key) is tuple and source_key and source_key[0] is binding:
                        del revisions[source_key]

    def _release(self, *, capture_base_exceptions=False) -> None:
        getattr(self, '_execution_upstream_clock_leases', {}).clear()
        self._execution_upstream_observation = None
        self._invalidate_live_parent_capabilities()
        if self.sticky_cleanup_enabled:
            self._strict_cleanup_entry()
            self._cleanup_in_progress = True
            try:
                self._strict_guard()
                self._strict_release_bindings(0)
                if self._bindings:
                    error = ServiceRuntimeError("CLOSE_FAILED")
                    self._set_cleanup_sticky(error)
                    raise error
                for key, transport in tuple(self._transports.items()):
                    self._strict_guard(transport)
                    snapshot = self._strict_resource_snapshot()
                    try:
                        transport.close()
                        self._strict_verify_resources(snapshot)
                        self._strict_guard(transport)
                        del self._transports[key]
                    except BaseException as error:
                        self._set_cleanup_sticky(transport, snapshot, error)
                        raise
                self._strict_guard()
                if self._bindings or self._transports:
                    error = ServiceRuntimeError("CLOSE_FAILED")
                    self._set_cleanup_sticky(error)
                    raise error
                self._registration_lineage.clear()
            except BaseException as error:
                self._set_cleanup_sticky(error)
                raise
            finally:
                self._cleanup_in_progress = False
            return
        self._registration_lineage.clear()
        self._release_bindings(capture_base_exceptions=capture_base_exceptions)
        for key, transport in tuple(self._transports.items()):
            client = None
            captured_bindings: tuple[Any, ...] = ()
            if type(transport) is _RequestNotSentAdapter:
                try:
                    from bt_api_transport_zmq import service_v2 as zmq_service
                    candidate = transport._transport
                    if type(candidate) is zmq_service.ZmqServiceClient:
                        client = candidate
                        strict_lookup = getattr(zmq_service, '_strict_client_binding', None)
                        if callable(strict_lookup):
                            captured_bindings = (strict_lookup(client),)
                except (ImportError, AttributeError):
                    pass
            try:
                transport.close()
            except Exception:
                self._cleanup_failures += 1
            except BaseException:
                if not capture_base_exceptions:
                    raise
                self._cleanup_failures += 1
            else:
                if self._transports.get(key) is transport:
                    del self._transports[key]
                if client is not None and transport._transport is client:
                    self._cleanup_closed_zmq_proofs(client, captured_bindings)
        # A registration whose transport closed after an unacknowledged
        # unregister cannot be retried locally. Keep the uncertainty visible;
        # the server manages its own lease, but its expiry is not observed here.
        retryable_ids = {
            binding.registration_id
            for binding in self._bindings
            if (binding.service_id, binding.transport_ref) in self._transports
        }
        for registration_id in tuple(self._unregister_uncertainties):
            if registration_id not in retryable_ids:
                self._fold_unregister_uncertainty(registration_id)
        self._bindings = [
            binding
            for binding in self._bindings
            if (binding.service_id, binding.transport_ref) in self._transports
        ]
