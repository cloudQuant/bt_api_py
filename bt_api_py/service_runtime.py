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
            }
        ),
        "service.account_read": frozenset(
            {"account.snapshot", "account.head", "account.read_after", "account.ack"}
        ),
        "service.order_entry": frozenset({"orders.submit", "orders.cancel"}),
        "service.order_query": frozenset(
            {"orders.query", "orders.snapshot", "orders.head", "orders.read_after", "orders.ack"}
        ),
        "service.order_group_entry": frozenset({"groups.submit", "groups.query", "groups.cancel"}),
        "service.risk_query": frozenset(
            {"risk.snapshot", "risk.head", "risk.read_after", "risk.ack"}
        ),
        "service.risk_analysis": frozenset(
            {"analysis.submit", "analysis.query", "analysis.cancel", "analysis.input_upload"}
        ),
        "service.risk_policy": frozenset(
            {"policy.read", "policy.draft", "policy.approve", "policy.publish", "policy.receipt"}
        ),
        "service.telemetry_ingress": frozenset({"telemetry.append"}),
        "service.monitor_query": frozenset({"monitor.query", "monitor.read_after"}),
        "service.monitor_control": frozenset({"monitor.acknowledge", "monitor.control"}),
        "service.owner_control": frozenset(
            {"owner.session", "owner.stop_new", "owner.cancel", "owner.reduce"}
        ),
        "service.group_reservations": frozenset(
            {"reservations.reserve", "reservations.query", "reservations.release"}
        ),
    }
)


class ServiceRuntimeError(RuntimeError):
    """Finite diagnostics, with no configuration values or remote error text."""

    def __init__(self, code: str) -> None:
        self.code = code
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
            ports = MOUNT_PORTS[mount] + OPTIONAL_MOUNT_PORTS.get(mount, ())
            allowed = frozenset().union(*(PORT_OPERATIONS[p] for p in ports))
            if not operations <= allowed:
                raise ServiceRuntimeError("INVALID_ACTION_REQUEST")
        self._transports: dict[tuple[str, str], RequestReplyTransport] = {}
        self._peers: dict[str, WireIdentity] = {}
        self._bindings: list[RegisteredBinding] = []
        self._state = "new"
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
        if not isinstance(endpoint, str) or not endpoint:
            raise ServiceRuntimeError("MARKET_ENDPOINT_REQUIRED")
        if not isinstance(security_ref, str) or not security_ref:
            raise ServiceRuntimeError("SECURITY_PROFILE_UNAVAILABLE")
        if type(high_water_mark) is not int or high_water_mark <= 0:
            raise ServiceRuntimeError("INVALID_MARKET_TRANSPORT_OPTIONS")
        if type(max_message_bytes) is not int or max_message_bytes <= 0:
            raise ServiceRuntimeError("INVALID_MARKET_TRANSPORT_OPTIONS")
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
        )

    def start(self) -> RegistrationSummary:
        self._assert_owner_thread()
        if self._state == "registered":
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        if self._state != "new":
            raise ServiceRuntimeError("SESSION_NOT_NEW")
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
                    except Exception as error:
                        if isinstance(error, ServiceRuntimeStartupCleanupError):
                            raise
                        if binding.get("required", True):
                            if isinstance(error, ServiceRuntimeError):
                                raise
                            raise ServiceRuntimeError("REQUIRED_REGISTRATION_FAILED") from None
                        self._release_bindings(first_binding)
                        self._degraded += 1
                        self._degradation_codes.append(
                            error.code
                            if isinstance(error, ServiceRuntimeError)
                            else "SERVICE_UNAVAILABLE"
                        )
            self._state = "registered"
            return RegistrationSummary(
                self.bindings, self._degraded, tuple(self._degradation_codes)
            )
        except BaseException:
            # Startup can be interrupted after a transport or registration has
            # been acquired. Try every local close, and retain any transport
            # whose owner-thread cleanup did not complete.
            self._release(capture_base_exceptions=True)
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
    ) -> Mapping[str, Any]:
        self._assert_owner_thread()
        self._require_healthy()
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
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
        remaining = binding.lease_deadline - self._monotonic_now()
        if remaining <= 0:
            self._expire_binding(binding)
            self._require_healthy()
            raise ServiceRuntimeError("LEASE_EXPIRED")
        return self._rpc(
            binding.service_id,
            binding.transport_ref,
            binding.scope,
            operation,
            {
                "registration_id": binding.registration_id,
                "service_epoch": binding.service_epoch,
                "instance_id": binding.instance_id,
                "arguments": dict(arguments),
            },
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
        for port in MOUNT_PORTS[binding["mount"]]:
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
            requested = self._requested_operations.get(binding["mount"], frozenset())
            if requested.intersection(PORT_OPERATIONS[port]):
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
        if len(available) != 1 or not isinstance(available[0].get("operations"), (list, tuple)):
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
        if not operations:
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        if port == "service.order_entry" and not {"orders.submit", "orders.cancel"} <= operations:
            raise ServiceRuntimeError("CAPABILITY_UNAVAILABLE")
        digest = hashlib.sha256(
            json.dumps(dict(binding), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        registration_key = hashlib.sha256(
            (self.client_session_id + ":" + digest + ":" + port).encode()
        ).hexdigest()
        timing: dict[str, Any] = {}
        try:
            result, request_started = self._rpc(
                binding["service_id"],
                binding["transport_ref"],
                scope,
                "service.register",
                {
                    "registration_key": registration_key,
                    "client_session_id": self.client_session_id,
                    "instance_id": binding["instance_id"],
                    "port": port,
                    "scope_ref": binding["scope_ref"],
                    "operations": sorted(operations),
                },
                timeout=timeout,
                include_started_at=True,
                timing=timing,
            )
        except BaseException:
            if (
                timing.get("request_started") is not None
                and not timing.get("server_rejected", False)
                and not timing.get("request_not_sent", False)
            ):
                self._record_unknown_registration(registration_key)
            raise
        for name in ("registration_id", "service_epoch"):
            if not isinstance(result.get(name), str) or not result[name]:
                self._record_unknown_registration(registration_key)
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        # Once a usable cleanup handle arrives, retain responsibility for it
        # even if the rest of the success receipt is malformed.
        try:
            for name in ("policy_version", "requirements_version"):
                if not isinstance(result.get(name), str) or not result[name]:
                    raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
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
        except Exception:
            cleanup_acknowledged = self._unregister(
                binding["service_id"],
                binding["transport_ref"],
                scope,
                result["registration_id"],
                result["service_epoch"],
                timeout,
            )
            if not cleanup_acknowledged:
                self._fold_unregister_uncertainty(result["registration_id"])
            raise
        self._bindings.append(
            RegisteredBinding(
                mount=binding["mount"],
                service_id=binding["service_id"],
                instance_id=binding["instance_id"],
                scope_ref=binding["scope_ref"],
                scope=scope,
                registration_id=result["registration_id"],
                service_epoch=result["service_epoch"],
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
            )
        )

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
        # No automatic retry: a timed-out write requires business reconciliation.
        request_started = self._monotonic_now()
        if started_at is not None:
            started_at.append(request_started)
        if timing is not None:
            timing["request_started"] = request_started
        try:
            reply = self._transports[(service_id, transport_ref)].request(
                request,
                timeout=timeout
                if timeout is not None
                else profile["options"]["request_timeout_ms"] / 1000,
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
            raise ServiceRuntimeError(
                code
                if isinstance(code, str) and code in SERVICE_ERROR_CODES
                else "REMOTE_REQUEST_REJECTED"
            )
        result = body.get("result")
        if not isinstance(result, Mapping):
            if not control:
                raise ServiceCallOutcomeUnknownError(request.header.message_id, operation)
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
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

    def _unregister_binding(self, binding: RegisteredBinding) -> bool:
        if binding.remote_lease_inactive:
            self._unregister_uncertainties.pop(binding.registration_id, None)
            return True
        return self._unregister(
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
