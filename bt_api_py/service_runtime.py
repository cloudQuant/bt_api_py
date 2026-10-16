"""SDK service proxies: explicit registration, bounded RPC, no direct fallback.

This module imports neither a domain component nor a transport backend. A
successful start establishes registrations; consumers must still restore their
data baselines before declaring a strategy ready to trade.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import (
    MAX_WIRE_MESSAGE_BYTES,
    RequestReplyTransport,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    validate_reply,
)

from bt_api_py.service_config import ClientConfig, ServiceConfig
from bt_api_py.service_deployment import MOUNT_PORTS, OPTIONAL_MOUNT_PORTS
from bt_api_py.service_registry import SERVICE_ERROR_CODES

# Code-owned action lists. Deployments may expose a subset; configuration and
# remote discovery cannot introduce new actions into a proxy.
PORT_OPERATIONS = MappingProxyType(
    {
        "service.market_source": frozenset(
            {
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
                "market.history",
            }
        ),
        "service.account_read": frozenset(
            {
                "account.snapshot",
                "account.head",
                "account.read_after",
                "account.ack",
                "account.progress",
                "account.configuration",
                "account.server_time_probe",
            }
        ),
        "service.order_entry": frozenset(
            {
                "orders.submit",
                "orders.submit_approved_target",
                "orders.cancel",
                "orders.submit_group_leg",
                "orders.resolve_group_leg",
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
                "execution.account_books.query",
            }
        ),
        "service.order_group_entry": frozenset({"groups.submit", "groups.query", "groups.cancel"}),
        "service.risk_query": frozenset(
            {"risk.snapshot", "risk.head", "risk.read_after", "risk.ack"}
        ),
        "service.risk_analysis": frozenset(
            {"analysis.submit", "analysis.query", "analysis.cancel", "analysis.input_upload"}
        ),
        "service.risk_policy": frozenset(
            {
                "policy.read", "policy.draft", "policy.approve", "policy.publish", "policy.receipt",
                "policy.budget.propose", "policy.budget.approve", "policy.budget.publish_member",
                "policy.budget.confirm_member", "policy.budget.reject_member",
                "policy.budget.mark_unknown", "policy.budget.retract", "policy.budget.read",
            }
        ),
        "service.telemetry_ingress": frozenset({"telemetry.append", "events.append"}),
        "service.monitor_query": frozenset(
            {"monitor.query", "monitor.read_after", "telemetry.snapshot"}
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
_APPROVED_ORDER_ENTRY_OPERATIONS = frozenset({"orders.submit_approved_target"})
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


class ZmqTransportFactory:
    """Code-registered ZMQ factory; resolves only explicitly provided key refs."""

    def __init__(self, security_profiles: Mapping[str, ClientCurveKeys]) -> None:
        self._profiles = dict(security_profiles)

    def __call__(
        self, profile: Mapping[str, Any], expected_peer: WireIdentity
    ) -> RequestReplyTransport:
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
        )

        try:
            client = ZmqServiceClient(
                endpoint,
                expected_peer,
                credentials=CurveClientCredentials(
                    keys.server_key, keys.public_key, keys.secret_key
                ),
                high_water_mark=profile["options"]["high_water_mark"],
                max_message_bytes=profile["options"]["max_message_bytes"],
            )
        except ServiceTransportStartupCleanupError as error:
            raise ServiceRuntimeStartupCleanupError(error.transport) from None
        return _RequestNotSentAdapter(client, RequestNotSentError)


class _RequestNotSentAdapter:
    """Translate one exact backend contract without exposing it to the SDK."""

    def __init__(self, transport: RequestReplyTransport, error_type: type[BaseException]):
        self._transport = transport
        self._error_type = error_type

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        try:
            return self._transport.request(request, timeout=timeout)
        except self._error_type:
            raise ServiceRuntimeRequestNotSentError() from None

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
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(config) not in (ClientConfig, ServiceConfig):
            raise ServiceRuntimeError("INVALID_CONFIG")
        self._config = config.to_public_dict()
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
        self._monotonic_clock = monotonic_clock
        self._owner_thread_id = threading.get_ident()
        self._requested_operations = dict(requested_operations or {})
        for mount, operations in self._requested_operations.items():
            if mount not in MOUNT_PORTS or type(operations) is not frozenset or not operations:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            if mount == "broker.group_monitor" and not operations <= _GROUP_MONITOR_OPERATIONS:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
            ports = MOUNT_PORTS[mount] + OPTIONAL_MOUNT_PORTS.get(mount, ())
            allowed = frozenset().union(*(PORT_OPERATIONS[p] for p in ports))
            if not operations <= allowed:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
        self._transports: dict[tuple[str, str], RequestReplyTransport] = {}
        self._peers: dict[str, WireIdentity] = {}
        self._bindings: list[RegisteredBinding] = []
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
        self._unknown_registration_count = 0
        self._last_unknown_registration_key: str | None = None

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
    def last_unresolved_registration_id(self) -> str | None:
        """Most recent registration ID no longer locally retryable, if any."""

        return self._last_unresolved_registration_id

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
        if self._state != "registered":
            return False
        now = self._monotonic_now()
        return not any(
            binding.active and binding.required and now >= binding.lease_deadline
            for binding in self._bindings
        )

    @property
    def bindings(self) -> tuple[RegisteredBinding, ...]:
        return tuple(self._bindings)

    def market_binding_view(
        self, mount: str, *, required_operations: frozenset[str]
    ) -> ServiceMarketBindingView:
        """Return a trusted frozen profile view for one registered market mount."""

        self._assert_owner_thread()
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
        if self._state == "registered":
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        if self._state != "new":
            raise ServiceRuntimeError("SESSION_NOT_NEW")
        self._state = "starting"
        try:
            for client in self._config.get("clients", {}).values():
                for binding in client["settings"]["bindings"]:
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
                            raise ServiceRuntimeError("REQUIRED_REGISTRATION_FAILED") from None
                        self._release_during_startup(first_binding)
                        self._degraded += 1
                        self._degradation_codes.append(
                            error.code
                            if isinstance(error, ServiceRuntimeError)
                            else "SERVICE_UNAVAILABLE"
                        )
            self._startup_maintain_if_due()
            self._startup_check_required_leases()
            self._state = "registered"
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        except BaseException:
            # Startup can be interrupted after a transport or registration has
            # been acquired. Try every local close, and retain any transport
            # whose owner-thread cleanup did not complete.
            self._startup_rollback = True
            try:
                self._release(capture_base_exceptions=True)
            finally:
                self._startup_rollback = False
            self._state = "close_failed" if self._transports else "failed"
            if self._transports:
                raise ServiceRuntimeError("CLOSE_FAILED") from None
            raise

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
    ) -> Mapping[str, Any]:
        self._assert_owner_thread()
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
        if operation not in binding.operations:
            raise ServiceRuntimeError("ACTION_NOT_GRANTED")
        reply_limit = max_reply_message_bytes
        tracked_account_read = (
            operation == "account.read_after"
            and isinstance(arguments, Mapping)
            and arguments.get("track_delivery") is True
        )
        if reply_limit is not None or tracked_account_read:
            profile_limit = self._profile_max_message_bytes(binding.transport_ref)
            reply_limit = profile_limit if reply_limit is None else min(profile_limit, reply_limit)
        remaining = binding.lease_deadline - self._monotonic_now()
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
        return self._rpc(
            binding.service_id,
            binding.transport_ref,
            binding.scope,
            operation,
            payload,
            control=False,
            timeout=min(binding.timeout, remaining * 0.8),
        )

    def renew(self) -> RegistrationSummary:
        """Force a renewal of every currently active binding on this thread."""

        return self._maintain(force=True)

    def maintain(self) -> RegistrationSummary:
        """Renew due leases synchronously on the owning transport thread."""

        return self._maintain(force=False)

    def next_maintenance_delay(self) -> float | None:
        """Seconds until the next active binding should be maintained, if any."""

        self._assert_owner_thread()
        if self._state != "registered":
            return None
        deadlines = [binding.renewal_due_at for binding in self._bindings if binding.active]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - self._monotonic_now())

    def _maintain(self, *, force: bool) -> RegistrationSummary:
        self._assert_owner_thread()
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
                    self._state = "failed"
                    raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")
                self._expire_binding(binding)
                continue
            if not force and now < binding.renewal_due_at:
                index += 1
                continue
            renewal_timeout = min(binding.timeout, remaining * 0.5)
            timing: dict[str, Any] = {}
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
                self._bindings[index] = replace(
                    binding,
                    remote_lease_inactive=definitely_inactive,
                )
                if definitely_inactive:
                    self._unregister_uncertainties.pop(binding.registration_id, None)
                binding = self._bindings[index]
                if binding.required:
                    if (
                        not force
                        and not server_rejected
                        and isinstance(error, TimeoutError)
                        and self._monotonic_now() < binding.lease_deadline
                    ):
                        # The renewal outcome is indeterminate (the transport
                        # timed out) while the current lease is still
                        # unexpired. Keep the binding due and retry on the
                        # next maintenance pass instead of failing the whole
                        # session; the lease deadline stays the hard boundary
                        # (a lease that never renews still fails with
                        # REQUIRED_LEASE_EXPIRED). Explicit hub rejections and
                        # forced renewals keep failing fast.
                        break
                    self._state = "failed"
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
                    self._state = "failed"
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
            self._bindings[index] = renewed
            if self._monotonic_now() >= new_deadline:
                if binding.required:
                    self._state = "failed"
                    raise ServiceRuntimeError("LEASE_EXPIRED_DURING_RENEWAL")
                self._expire_binding(renewed)
                continue
            index += 1
        if startup:
            self._startup_check_required_leases()
        else:
            self._require_healthy()
        return self._summary()

    def recover(self) -> RegistrationSummary:
        """Start a fresh registration session without replaying business calls."""

        self._assert_owner_thread()
        if self._state not in {"registered", "failed", "close_failed"}:
            raise ServiceRuntimeError("SESSION_NOT_RECOVERABLE")
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

    def close(self) -> None:
        self._assert_owner_thread()
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
            self._state = "failed"
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
            self._state = "failed"
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
            self._state = "failed"
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")
        return min(timeout, remaining * self._STARTUP_RPC_TIMEOUT_FRACTION)

    def _expire_binding(self, binding: RegisteredBinding) -> None:
        if binding.required:
            self._state = "failed"
            return
        self._degrade_binding(binding, "LEASE_EXPIRED")

    def _degrade_binding(self, binding: RegisteredBinding, code: str) -> None:
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
        key = (binding.service_id, binding.transport_ref)
        acknowledged = False
        if binding.remote_lease_inactive:
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
        peer = self._peer_resolver(service_id)
        if type(peer) is not WireIdentity or peer.role is not WireRole.SERVICE:
            raise ServiceRuntimeError("INVALID_PEER")
        if service_id in self._peers and self._peers[service_id] != peer:
            raise ServiceRuntimeError("PEER_CHANGED")
        self._peers[service_id] = peer
        profiles = self._config["communication"]["profiles"]
        timeout = (timeout_ms or profiles[transport_ref]["options"]["request_timeout_ms"]) / 1000
        key = (service_id, transport_ref)
        if key not in self._transports:
            try:
                self._transports[key] = self._factory(profiles[transport_ref], peer)
            except ServiceRuntimeStartupCleanupError as error:
                self._transports[key] = error.transport
                raise
        discovered = self._rpc(
            service_id, transport_ref, scope, "service.discover", {}, timeout=timeout
        )
        if discovered.get("protocol_version") != 2:
            raise ServiceRuntimeError("PROTOCOL_VERSION_MISMATCH")
        if discovered.get("service_id") != service_id or discovered.get("ready") is not True:
            raise ServiceRuntimeError("SERVICE_NOT_READY")
        capabilities = discovered.get("capabilities")
        if not isinstance(capabilities, (tuple, list)):
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        requested = self._requested_operations.get(binding["mount"])
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
        if requested is not None:
            wanted = requested.intersection(PORT_OPERATIONS[port])
            if not wanted <= operations:
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
            operations = wanted
        elif port == "service.account_read":
            # New server capabilities must not silently expand registrations
            # for existing read-only clients and grants. Consumers opt in to
            # ACK/progress explicitly through requested_operations.
            operations = operations.intersection(_DEFAULT_ACCOUNT_READ_OPERATIONS)
        elif port == "service.order_query":
            # Account-book and group-leg views each need an explicit request
            # and grant. Existing order readers retain their prior set.
            operations = operations.difference(
                {"execution.account_books.query", "orders.query_group_leg"}
            )
        elif port == "service.order_entry":
            # Group-owner writes are available only to callers that explicitly
            # request them; adding a remote capability cannot expand ordinary
            # application or service registrations.
            operations = operations.difference(
                _GROUP_ORDER_ENTRY_OPERATIONS | _APPROVED_ORDER_ENTRY_OPERATIONS
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
                == _GROUP_ORDER_ENTRY_OPERATIONS
                and operations == _GROUP_ORDER_ENTRY_OPERATIONS
            )
            explicit_approved_target_only = (
                requested is not None
                and requested.intersection(PORT_OPERATIONS[port])
                == _APPROVED_ORDER_ENTRY_OPERATIONS
                and operations == _APPROVED_ORDER_ENTRY_OPERATIONS
            )
            if not (explicit_group_owner or explicit_approved_target_only):
                raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        digest = hashlib.sha256(
            json.dumps(dict(binding), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        registration_key = hashlib.sha256(
            (self.client_session_id + ":" + digest + ":" + port).encode()
        ).hexdigest()
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
                raise
            except BaseException:
                if (
                    timing.get("request_started") is not None
                    and not timing.get("server_rejected", False)
                    and not timing.get("request_not_sent", False)
                ):
                    self._record_unknown_registration(registration_key)
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
            placeholder_index = len(self._bindings)
            self._bindings.append(placeholder)
        except BaseException:
            if placeholder is None:
                # A successful remote registration without a locally retained
                # cleanup handle is explicitly uncertain, never silently lost.
                self._record_unknown_registration(registration_key)
            else:
                try:
                    if not any(item is placeholder for item in self._bindings):
                        self._bindings.append(placeholder)
                except BaseException:
                    self._record_unknown_registration(registration_key)
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
        except BaseException:
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

    def _rpc(
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
    ):
        self._assert_owner_thread()
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
        request = WireEnvelope(
            WireHeader(
                protocol_version=2,
                kind=WireMessageKind.REQUEST,
                message_id=uuid.uuid4().hex,
                correlation_id=None,
                identity=self.identity,
                scope=scope,
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
        request_started = self._monotonic_now()
        if started_at is not None:
            started_at.append(request_started)
        if timing is not None:
            timing["request_started"] = request_started
        try:
            reply = self._transports[(service_id, transport_ref)].request(
                request, timeout=effective_timeout
            )
        except ServiceRuntimeRequestNotSentError:
            if timing is not None:
                timing["request_not_sent"] = True
            raise
        validate_reply(reply, request, expected_peer=self._peers[service_id])
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
            raise failure
        result = body.get("result")
        if not isinstance(result, Mapping):
            if not control:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        if startup_rpc and not rollback_rpc and operation != "service.register":
            self._startup_check_required_leases()
        if include_started_at:
            return result, request_started
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
        if binding.remote_lease_inactive:
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
        try:
            receipt = self._rpc(
                service_id,
                transport_ref,
                scope,
                "service.unregister",
                {"registration_id": registration_id, "service_epoch": epoch},
                timeout=timeout,
                timing=timing,
            )
            if (
                receipt.get("unregistered") is not True
                or receipt.get("registration_id") != registration_id
            ):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
            self._unregister_uncertainties.pop(registration_id, None)
            return True
        except ServiceRuntimeError as error:
            if timing.get("server_rejected") is True and error.code in {
                "NOT_REGISTERED",
                "STALE_EPOCH",
            }:
                self._unregister_uncertainties.pop(registration_id, None)
                return True
            self._cleanup_failures += 1
            self._unregister_uncertainties[registration_id] = None
            return False
        except Exception:
            self._cleanup_failures += 1
            self._unregister_uncertainties[registration_id] = None
            return False

    def _release_bindings(self, first=0, *, capture_base_exceptions=False) -> None:
        retained = self._bindings[:first]
        for binding in self._bindings[first:]:
            key = (binding.service_id, binding.transport_ref)
            if binding.remote_lease_inactive:
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

    def _release(self, *, capture_base_exceptions=False) -> None:
        self._release_bindings(capture_base_exceptions=capture_base_exceptions)
        for key, transport in tuple(self._transports.items()):
            try:
                transport.close()
            except Exception:
                self._cleanup_failures += 1
            except BaseException:
                if not capture_base_exceptions:
                    raise
                self._cleanup_failures += 1
            else:
                del self._transports[key]
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
