"""Explicit strategy-side composition of one Gateway client session.

A healthy runtime reports transport and registration health only. It does not
establish account readiness, execution reconciliation, or permission to trade.
The caller supplies the durable host and separately resolved CURVE keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_direct_client import (
    GatewayCancelHostPort,
    GatewayClientHostPort,
    GatewayClientRoute,
    GatewayDirectClient,
)
from bt_api_py.gateway_execution_stream_client import GatewayExecutionStreamClient
from bt_api_py.service_config import ClientConfig, parse_client_config
from bt_api_py.service_runtime import (
    PORT_OPERATIONS,
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import RuntimeSnapshot, ThreadedServiceClientRuntime


class GatewayStrategyClientSession:
    """Bind one parsed Gateway route to one non-restartable runtime handle."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        binding: GatewayCommandBinding,
        route: GatewayClientRoute,
        peer: WireIdentity,
        security_profiles: Mapping[str, ClientCurveKeys],
        host: GatewayClientHostPort,
        cancel_host: GatewayCancelHostPort | None = None,
        requested_operations: frozenset[str],
        queue_size: int = 32,
    ) -> None:
        if type(config) is not ClientConfig:
            raise ValueError("INVALID_CONFIG")
        try:
            # Reparse a detached public tree; no caller-owned config container is
            # captured by the owner factory.
            config_copy = parse_client_config(config.to_public_dict())
            if config_copy.process_role != "strategy_client":
                raise ValueError
            if set(config_copy.clients) != {"gateway"}:
                raise ValueError
            bindings = config_copy.clients["gateway"]["settings"]["bindings"]
            if len(bindings) != 1:
                raise ValueError
            configured = bindings[0]
            profiles = config_copy.communication["profiles"]
            if set(profiles) != {configured["transport_ref"]}:
                raise ValueError
            profile = profiles[configured["transport_ref"]]
            if profile["backend"] != "zmq" or set(profile["endpoints"]) != {"rpc_connect"}:
                raise ValueError
            if type(binding) is not GatewayCommandBinding or type(route) is not GatewayClientRoute:
                raise ValueError
            if type(route.scope) is not WireScope or type(route.identity) is not WireIdentity:
                raise ValueError
            # Reconstruct immutable neutral values to avoid retaining supplied
            # instances that could be modified using object.__setattr__.
            bound = GatewayCommandBinding(
                binding.journal_id, binding.account_scope, binding.strategy_scope, binding.principal
            )
            scope = WireScope(
                route.scope.execution_domain_ref,
                account_ref=route.scope.account_ref,
                account_refs=route.scope.account_refs,
                strategy_ref=route.scope.strategy_ref,
                run_id=route.scope.run_id,
                manual_book_ref=route.scope.manual_book_ref,
                portfolio_ref=route.scope.portfolio_ref,
                group_id=route.scope.group_id,
                leg_id=route.scope.leg_id,
            )
            identity = WireIdentity(
                route.identity.role,
                route.identity.principal_ref,
                route.identity.actor_ref,
                route.identity.delegate_ref,
            )
            route_copy = GatewayClientRoute(
                route.mount, route.service_id, route.instance_id, route.scope_ref, scope, identity
            )
            if type(peer) is not WireIdentity:
                raise ValueError
            peer_copy = WireIdentity(
                peer.role, peer.principal_ref, peer.actor_ref, peer.delegate_ref
            )
            if (
                configured["mount"] != "broker.direct_write"
                or configured["required"] is not True
                or configured["service_id"] != route_copy.service_id
                or configured["instance_id"] != route_copy.instance_id
                or configured["scope_ref"] != route_copy.scope_ref
                or configured["account_ref"] != bound.account_scope
                or config_copy.registration["strategy_ref"] != bound.strategy_scope
                or identity.delegate_ref is not None
                or peer_copy.role is not WireRole.SERVICE
                or peer_copy.actor_ref != route_copy.service_id
                or peer_copy.delegate_ref is not None
                or len(scope.run_id) > 128
            ):
                raise ValueError
            if not isinstance(security_profiles, Mapping):
                raise ValueError
            security_ref = profile["security_profile_ref"]
            if set(security_profiles) != {security_ref}:
                raise ValueError
            keys = security_profiles[security_ref]
            if type(keys) is not ClientCurveKeys or any(
                type(value) is not bytes or len(value) != 40
                for value in (keys.server_key, keys.public_key, keys.secret_key)
            ):
                raise ValueError
            frozen_security = MappingProxyType(
                {security_ref: ClientCurveKeys(keys.server_key, keys.public_key, keys.secret_key)}
            )
        except Exception:
            raise ValueError("INVALID_CLIENT_CONFIGURATION") from None

        allowed = PORT_OPERATIONS["service.direct_write"]
        if (
            type(requested_operations) is not frozenset
            or not requested_operations
            or not requested_operations <= allowed
            or (
                requested_operations & {"direct.reserve", "direct.release", "direct.dispatch"}
                and "direct.command_status" not in requested_operations
            )
            or (
                "direct.cancel" in requested_operations
                and "direct.cancel_status" not in requested_operations
            )
            or (
                requested_operations & {"direct.cancel", "direct.cancel_status"}
                and cancel_host is None
            )
        ):
            raise ValueError("INVALID_REQUESTED_OPERATIONS")

        base_scope = WireScope(scope.execution_domain_ref, account_ref=scope.account_ref)
        runtime_identity = replace(identity)

        def scope_resolver(scope_ref: str) -> WireScope:
            if scope_ref != route_copy.scope_ref:
                raise ValueError("INVALID_SCOPE")
            return base_scope

        def peer_resolver(service_id: str) -> WireIdentity:
            if service_id != route_copy.service_id:
                raise ValueError("INVALID_PEER")
            return peer_copy

        def runtime_factory() -> ServiceClientRuntime:
            return ServiceClientRuntime(
                config_copy,
                identity=runtime_identity,
                scope_resolver=scope_resolver,
                peer_resolver=peer_resolver,
                transport_factory=ZmqTransportFactory(frozen_security),
                run_id=scope.run_id,
                requested_operations={route_copy.mount: requested_operations},
            )

        runtime = ThreadedServiceClientRuntime(runtime_factory, queue_size=queue_size)
        # Both clients validate the exact neutral binding, route and host before
        # the runtime can be started.
        command_client = GatewayDirectClient(
            runtime, binding=bound, route=route_copy, host=host, cancel_host=cancel_host
        )
        execution_stream_client = GatewayExecutionStreamClient(
            runtime, binding=bound, route=route_copy
        )
        self._runtime = runtime
        self._command_client = command_client
        self._execution_stream_client = execution_stream_client
        self._binding = bound
        self._route = route_copy

    @property
    def runtime(self) -> ThreadedServiceClientRuntime:
        return self._runtime

    @property
    def command_client(self) -> GatewayDirectClient:
        return self._command_client

    @property
    def execution_stream_client(self) -> GatewayExecutionStreamClient:
        return self._execution_stream_client

    @property
    def binding(self) -> GatewayCommandBinding:
        return replace(self._binding)

    @property
    def route(self) -> GatewayClientRoute:
        return replace(
            self._route,
            scope=replace(self._route.scope),
            identity=replace(self._route.identity),
        )

    def start(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        return self._runtime.start(timeout_seconds=timeout_seconds)

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        return self._runtime.close(timeout_seconds=timeout_seconds)

    def status(self) -> RuntimeSnapshot:
        return self._runtime.status()
