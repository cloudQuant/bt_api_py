"""Explicit strategy-side composition of one Gateway client session.

A healthy runtime reports transport and registration health only. It does not
establish account readiness, execution reconciliation, or permission to trade.
The caller supplies the durable host and separately resolved CURVE keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

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
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    RuntimeSnapshot,
    ThreadedServiceClientRuntime,
)

if TYPE_CHECKING:
    from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
    from bt_api_py.gateway_strategy_instrument import GatewayInstrumentClientSettings
    from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings


class _StrategyOwnerExtension:
    """Compose selected resources on the single runtime owner thread."""

    def __init__(
        self,
        account_settings,
        market_settings,
        instrument_settings,
        *,
        account_ref,
        scope_ref,
        stream_scope,
        security_profiles,
    ):
        self._account_settings = account_settings
        self._market_settings = market_settings
        self._instrument_settings = instrument_settings
        self._account_ref, self._scope_ref = account_ref, scope_ref
        self._stream_scope, self._security_profiles = stream_scope, security_profiles
        self._account = self._market = self._instrument = None
        self._uncertainties = {"account": 0, "market": 0, "instrument": 0}

    def start(self, runtime):
        if self._account_settings is not None:
            from bt_api_py.gateway_strategy_account import GatewayAccountOwner

            self._account = GatewayAccountOwner(
                self._account_settings,
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
                mount="store.account",
            )
            self._account.start(runtime)
        if self._instrument_settings is not None:
            from bt_api_py.gateway_strategy_instrument import GatewayInstrumentOwner

            self._instrument = GatewayInstrumentOwner(
                self._instrument_settings,
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
                mount="store.account",
            )
            self._instrument.start(runtime)
        if self._market_settings is not None:
            from bt_api_py.gateway_strategy_market import GatewayMarketOwner

            self._market = GatewayMarketOwner(
                self._market_settings,
                stream_scope=self._stream_scope,
                security_profiles=self._security_profiles,
                mount="store.market",
            )
            self._market.start(runtime)

    def invoke(self, operation, arguments):
        if operation in {
            "account.refresh",
            "account.snapshot",
            "account.status",
            "account.validate",
        }:
            if self._account is None:
                raise OwnerOperationError("ACCOUNT_UNAVAILABLE")
            return self._account.invoke(operation.removeprefix("account."), arguments)
        if operation.startswith("instrument.") and self._instrument is not None:
            return self._instrument.invoke(
                operation.removeprefix("instrument."), arguments
            )
        if operation.startswith("market.") and self._market is not None:
            return self._market.invoke(operation.removeprefix("market."), arguments)
        raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")

    @property
    def cleanup_uncertainties(self):
        for name in ("account", "market", "instrument"):
            owner = getattr(self, "_" + name)
            if owner is not None:
                count = owner.cleanup_uncertainties
                if type(count) is not int or not 0 <= count < 2**63:
                    raise ValueError("INVALID_OWNER_CLEANUP_COUNT")
                self._uncertainties[name] = max(self._uncertainties[name], count)
        total = sum(self._uncertainties.values())
        if total >= 2**63:
            raise ValueError("INVALID_OWNER_CLEANUP_COUNT")
        return total

    def close(self):
        failed = False
        for name in ("market", "instrument", "account"):
            owner = getattr(self, "_" + name)
            if owner is None:
                continue
            try:
                owner.close()
                count = owner.cleanup_uncertainties
                if type(count) is not int or not 0 <= count < 2**63:
                    raise ValueError("INVALID_OWNER_CLEANUP_COUNT")
                self._uncertainties[name] = max(self._uncertainties[name], count)
            except BaseException:
                failed = True
            else:
                setattr(self, "_" + name, None)
        if failed:
            raise OwnerOperationError("OWNER_CLEANUP_FAILED")


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
        account_settings: GatewayAccountClientSettings | None = None,
        market_settings: GatewayMarketClientSettings | None = None,
        instrument_settings: GatewayInstrumentClientSettings | None = None,
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
            expected_mounts = {"broker.direct_write"}
            if account_settings is not None or instrument_settings is not None:
                expected_mounts.add("store.account")
            if market_settings is not None:
                expected_mounts.add("store.market")
            indexed = {item["mount"]: item for item in bindings}
            if len(bindings) != len(indexed) or set(indexed) != expected_mounts:
                raise ValueError
            configured = indexed["broker.direct_write"]
            for item in indexed.values():
                if item["required"] is not True or any(
                    item[name] != configured[name]
                    for name in (
                        "service_id",
                        "instance_id",
                        "scope_ref",
                        "transport_ref",
                    )
                ):
                    raise ValueError
                # The public store.market binding omits account_ref. The shared
                # scope resolver supplies the same account for its registration.
                if (
                    item["mount"] != "store.market"
                    and item.get("account_ref") != configured["account_ref"]
                ):
                    raise ValueError
            profiles = config_copy.communication["profiles"]
            if set(profiles) != {configured["transport_ref"]}:
                raise ValueError
            profile = profiles[configured["transport_ref"]]
            endpoints = (
                {"rpc_connect"}
                if market_settings is None
                else {"rpc_connect", "market_connect"}
            )
            if profile["backend"] != "zmq" or set(profile["endpoints"]) != endpoints:
                raise ValueError
            if (
                type(binding) is not GatewayCommandBinding
                or type(route) is not GatewayClientRoute
            ):
                raise ValueError
            if (
                type(route.scope) is not WireScope
                or type(route.identity) is not WireIdentity
            ):
                raise ValueError
            # Reconstruct immutable neutral values to avoid retaining supplied
            # instances that could be modified using object.__setattr__.
            bound = GatewayCommandBinding(
                binding.journal_id,
                binding.account_scope,
                binding.strategy_scope,
                binding.principal,
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
                route.mount,
                route.service_id,
                route.instance_id,
                route.scope_ref,
                scope,
                identity,
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
                {
                    security_ref: ClientCurveKeys(
                        keys.server_key, keys.public_key, keys.secret_key
                    )
                }
            )
        except Exception:
            raise ValueError("INVALID_CLIENT_CONFIGURATION") from None

        allowed = PORT_OPERATIONS["service.direct_write"]
        if (
            type(requested_operations) is not frozenset
            or not requested_operations
            or not requested_operations <= allowed
            or (
                requested_operations
                & {"direct.reserve", "direct.release", "direct.dispatch"}
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

        frozen_account = None
        if account_settings is not None:
            from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings

            if type(account_settings) is not GatewayAccountClientSettings:
                raise ValueError("INVALID_ACCOUNT_SETTINGS")
            frozen_account = GatewayAccountClientSettings(
                expected_account=account_settings.expected_account,
                required_coverage=account_settings.required_coverage,
                requested_operations=account_settings.requested_operations,
                require_open_orders=account_settings.require_open_orders,
            )
        frozen_market = None
        if market_settings is not None:
            from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings

            if type(market_settings) is not GatewayMarketClientSettings:
                raise ValueError("INVALID_MARKET_SETTINGS")
            frozen_market = GatewayMarketClientSettings(
                subscriptions=market_settings.subscriptions,
                requested_operations=market_settings.requested_operations,
            )
        frozen_instrument = None
        if instrument_settings is not None:
            from bt_api_py.gateway_strategy_instrument import (
                GatewayInstrumentClientSettings,
            )

            if type(instrument_settings) is not GatewayInstrumentClientSettings:
                raise ValueError("INVALID_INSTRUMENT_SETTINGS")
            frozen_instrument = GatewayInstrumentClientSettings(
                instruments=instrument_settings.instruments,
                requested_operations=instrument_settings.requested_operations,
            )
        operation_map = {route_copy.mount: requested_operations}
        if frozen_account is not None:
            operation_map["store.account"] = frozen_account.requested_operations
        if frozen_instrument is not None:
            operation_map["store.account"] = (
                operation_map.get("store.account", frozenset())
                | frozen_instrument.requested_operations
            )
        if frozen_market is not None:
            operation_map["store.market"] = frozen_market.requested_operations
        base_scope = WireScope(
            scope.execution_domain_ref, account_ref=scope.account_ref
        )
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
                requested_operations=operation_map,
            )

        runtime_options = {}
        if (
            frozen_account is not None
            or frozen_market is not None
            or frozen_instrument is not None
        ):
            runtime_options["owner_extension_factory"] = (
                lambda: _StrategyOwnerExtension(
                    frozen_account,
                    frozen_market,
                    frozen_instrument,
                    account_ref=bound.account_scope,
                    scope_ref=route_copy.scope_ref,
                    stream_scope=WireScope(scope.execution_domain_ref),
                    security_profiles=frozen_security,
                )
            )
        runtime = ThreadedServiceClientRuntime(
            runtime_factory, queue_size=queue_size, **runtime_options
        )
        # Both clients validate the exact neutral binding, route and host before
        # the runtime can be started.
        command_client = GatewayDirectClient(
            runtime, binding=bound, route=route_copy, host=host, cancel_host=cancel_host
        )
        execution_stream_client = GatewayExecutionStreamClient(
            runtime, binding=bound, route=route_copy
        )
        account_client = market_client = None
        if frozen_account is not None:
            from bt_api_py.gateway_strategy_account import GatewayStrategyAccountClient

            account_client = GatewayStrategyAccountClient(runtime)
        if frozen_market is not None:
            from bt_api_py.gateway_strategy_market import GatewayStrategyMarketClient

            market_client = GatewayStrategyMarketClient(runtime, frozen_market)
        instrument_client = None
        if frozen_instrument is not None:
            from bt_api_py.gateway_strategy_instrument import (
                GatewayStrategyInstrumentClient,
            )

            instrument_client = GatewayStrategyInstrumentClient(
                runtime, frozen_instrument
            )
        self._account_client, self._market_client = account_client, market_client
        self._instrument_client = instrument_client
        self._runtime = runtime
        self._command_client = command_client
        self._execution_stream_client = execution_stream_client
        self._binding = bound
        self._route = route_copy

    @property
    def account_client(self):
        return self._account_client

    @property
    def market_client(self):
        return self._market_client

    @property
    def instruments(self):
        return self._instrument_client

    def get_instrument_spec(self, exchange_name, symbol, *, timeout_seconds=5.0):
        if self._instrument_client is None:
            raise OwnerOperationError("INSTRUMENT_CLIENT_UNAVAILABLE")
        return self._instrument_client.get_instrument_spec(
            exchange_name, symbol, timeout_seconds=timeout_seconds
        )

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
