"""SDK service proxies: explicit registration, bounded RPC, no direct fallback.

This module imports neither a domain component nor a transport backend. A
successful start establishes registrations; consumers must still restore their
data baselines before declaring a strategy ready to trade.
"""

from __future__ import annotations

import hashlib
import json
import math
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
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient

        return ZmqServiceClient(
            endpoint,
            expected_peer,
            credentials=CurveClientCredentials(keys.server_key, keys.public_key, keys.secret_key),
            high_water_mark=profile["options"]["high_water_mark"],
            max_message_bytes=profile["options"]["max_message_bytes"],
        )


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

    @property
    def state(self) -> str:
        return self._state

    @property
    def cleanup_failures(self) -> int:
        return self._cleanup_failures

    @property
    def bindings(self) -> tuple[RegisteredBinding, ...]:
        return tuple(self._bindings)

    def start(self) -> RegistrationSummary:
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
                        timeout_ms = client["settings"]["options"].get("request_timeout_ms")
                        self._register(binding, timeout_ms=timeout_ms)
                    except Exception as error:
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
        except Exception:
            self._release()
            self._state = "failed"
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
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        matches = [
            b
            for b in self._bindings
            if b.mount == mount
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
            timeout=binding.timeout,
        )

    def renew(self) -> RegistrationSummary:
        if self._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        try:
            for index, binding in enumerate(self._bindings):
                receipt = self._rpc(
                    binding.service_id,
                    binding.transport_ref,
                    binding.scope,
                    "service.renew",
                    {
                        "registration_id": binding.registration_id,
                        "service_epoch": binding.service_epoch,
                    },
                    timeout=binding.timeout,
                )
                expiry = self._expiry(receipt)
                self._bindings[index] = replace(binding, expires_at=expiry)
        except Exception:
            # A caller must explicitly recover the session. Never continue
            # issuing orders using only a remembered successful registration.
            self._state = "failed"
            raise
        return RegistrationSummary(self.bindings, self._degraded, tuple(self._degradation_codes))

    def close(self) -> None:
        if self._state == "closed":
            return
        self._release()
        self._state = "closed"

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

    def _register(self, binding: Mapping[str, Any], *, timeout_ms: int | None) -> None:
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
            self._transports[key] = self._factory(profiles[transport_ref], peer)
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
            self._register_port(binding, scope, port, capabilities, timeout=timeout)
        for port in OPTIONAL_MOUNT_PORTS.get(binding["mount"], ()):
            # Explicit application code requests control separately from reads.
            requested = self._requested_operations.get(binding["mount"], frozenset())
            if requested.intersection(PORT_OPERATIONS[port]):
                try:
                    self._register_port(binding, scope, port, capabilities, timeout=timeout)
                except Exception as error:
                    self._degraded += 1
                    self._degradation_codes.append(
                        error.code
                        if isinstance(error, ServiceRuntimeError)
                        else "SERVICE_UNAVAILABLE"
                    )

    def _register_port(self, binding, scope, port, capabilities, *, timeout):
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
        result = self._rpc(
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
        )
        for name in ("registration_id", "service_epoch"):
            if not isinstance(result.get(name), str) or not result[name]:
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        # Once a usable cleanup handle arrives, retain responsibility for it
        # even if the rest of the remote success receipt is malformed. Without
        # an ID/epoch (or after a lost reply), server-side lease expiry applies.
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
        except Exception:
            self._unregister(
                binding["service_id"],
                binding["transport_ref"],
                scope,
                result["registration_id"],
                result["service_epoch"],
                timeout,
            )
            raise
        self._bindings.append(
            RegisteredBinding(
                binding["mount"],
                binding["service_id"],
                binding["instance_id"],
                binding["scope_ref"],
                scope,
                result["registration_id"],
                result["service_epoch"],
                frozenset(operations),
                expiry,
                result["policy_version"],
                result["requirements_version"],
                binding["transport_ref"],
                timeout,
            )
        )

    @staticmethod
    def _expiry(result: Mapping[str, Any]) -> float:
        value = result.get("expires_at")
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        return float(value)

    def _rpc(
        self, service_id, transport_ref, scope, operation, payload, *, control=True, timeout=None
    ):
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
        reply = self._transports[(service_id, transport_ref)].request(
            request,
            timeout=timeout
            if timeout is not None
            else profile["options"]["request_timeout_ms"] / 1000,
        )
        validate_reply(reply, request, expected_peer=self._peers[service_id])
        body = reply.payload
        if body.get("ok") is not True:
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
        return result

    def _unregister(self, service_id, transport_ref, scope, registration_id, epoch, timeout):
        try:
            receipt = self._rpc(
                service_id,
                transport_ref,
                scope,
                "service.unregister",
                {"registration_id": registration_id, "service_epoch": epoch},
                timeout=timeout,
            )
            if (
                receipt.get("unregistered") is not True
                or receipt.get("registration_id") != registration_id
            ):
                raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
        except Exception:
            self._cleanup_failures += 1

    def _release_bindings(self, first=0) -> None:
        for binding in reversed(self._bindings[first:]):
            self._unregister(
                binding.service_id,
                binding.transport_ref,
                binding.scope,
                binding.registration_id,
                binding.service_epoch,
                binding.timeout,
            )
        del self._bindings[first:]

    def _release(self) -> None:
        self._release_bindings()
        for transport in self._transports.values():
            try:
                transport.close()
            except Exception:
                self._cleanup_failures += 1
        self._transports.clear()
