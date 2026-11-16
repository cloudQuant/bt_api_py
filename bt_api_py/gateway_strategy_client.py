"""Explicit strategy-side composition of one Gateway client session.

A healthy runtime reports transport and registration health only. It does not
establish account readiness, execution reconciliation, or permission to trade.
The caller supplies the durable host and separately resolved CURVE keys.
"""

from __future__ import annotations

import math
import threading
import time
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
from bt_api_py.gateway_strategy_delegated import GatewayStrategyDelegatedClient
from bt_api_py.gateway_strategy_position import GatewayStrategyPositionClient
from bt_api_py.service_config import ClientConfig, parse_client_config
from bt_api_py.service_runtime import (
    PORT_OPERATIONS,
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)

if TYPE_CHECKING:
    from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
    from bt_api_py.gateway_strategy_instrument import GatewayInstrumentClientSettings
    from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings


# Keep the original session reachable while its owner or cleanup is uncertain.
# This is local custody only, not a cross-session identity or account fence.
_RETAINED_STRATEGY_SESSIONS = {}


class _CleanupNotStarted(Exception):
    """The strict close call was not issued and may be explicitly retried."""


def _strategy_registration_scope_matches(snapshot, route):
    if (type(snapshot) is not RuntimeSnapshot or snapshot.identity != route.identity
            or snapshot.run_id != route.scope.run_id or type(snapshot.bindings) is not tuple):
        return False
    active = [binding for binding in snapshot.bindings if binding.active is True]
    return (bool(active) and all(binding.scope == route.scope for binding in active)
            and any(binding.mount == route.mount and binding.service_id == route.service_id
                    and binding.instance_id == route.instance_id and binding.scope_ref == route.scope_ref
                    and binding.required is True for binding in active))


def _release_scope_rejected_custody(session, runtime, snapshot):
    """Release only this original owner's locally closed non-strict session.

    The non-strict close contract does not prove remote unregister.
    """
    if (type(session) is not GatewayStrategyClientSession
            or session._strict_cleanup is not False or session._start_scope_rejected is not True
            or session._runtime is not runtime or session._original_runtime is not runtime
            or type(snapshot) is not RuntimeSnapshot or snapshot.state not in ("closed", "failed")
            or snapshot.thread_alive is not False
            or _RETAINED_STRATEGY_SESSIONS.get(id(session)) is not session):
        return
    try:
        if runtime.status() == snapshot:
            _RETAINED_STRATEGY_SESSIONS.pop(id(session), None)
    except Exception:
        pass


def _strict_close_snapshot(runtime, timeout_seconds, *, allow_close, identity, run_id):
    """Observe a strict cleanup once; None means the original owner is unknown."""
    if (type(runtime) is not ThreadedServiceClientRuntime
            or runtime.strict_cleanup_required is not True):
        return None
    try:
        valid_timeout = (type(timeout_seconds) in (int, float)
                         and math.isfinite(timeout_seconds) and timeout_seconds > 0)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ThreadedRuntimeError("CLEANUP_NOT_STARTED")
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        if not math.isfinite(value) or value <= 0:
            raise ThreadedRuntimeError("CLEANUP_NOT_STARTED")
        return value

    try:
        before, had_thread = runtime.cleanup_observation(timeout_seconds=remaining())
    except ThreadedRuntimeError as error:
        if type(error) is ThreadedRuntimeError and error.code in (
            "CLEANUP_SNAPSHOT_TIMEOUT", "CLEANUP_NOT_STARTED"
        ):
            raise _CleanupNotStarted from None
        raise
    if type(before) is not RuntimeSnapshot or type(had_thread) is not bool:
        return None
    never_started = (
        before.state in ("new", "closed") and not had_thread
        and before.thread_alive is False and before.bindings == ()
        and before.identity is None and before.run_id is None
        and type(before.unknown_registration_count) is int and before.unknown_registration_count == 0
        and type(before.unregister_uncertainties) is int and before.unregister_uncertainties == 0
        and type(before.owner_cleanup_uncertainties) is int and before.owner_cleanup_uncertainties == 0
        and type(before.cleanup_failures) is int and before.cleanup_failures == 0
    )
    if not never_started and (before.identity != identity or before.run_id != run_id):
        return None
    if before.state in ("new", "running"):
        if ((before.state == "new" and not never_started)
                or (before.state == "running" and not allow_close)):
            return None
        try:
            close_timeout = remaining()
        except ThreadedRuntimeError as error:
            if type(error) is ThreadedRuntimeError and error.code == "CLEANUP_NOT_STARTED":
                raise _CleanupNotStarted from None
            raise
        try:
            closed = runtime.close_once_if_state(expected_state=before.state,
                                                 timeout_seconds=close_timeout)
        except ThreadedRuntimeError as error:
            # This exact runtime code is emitted only before its atomic close
            # transition; all other failures keep the result unknown.
            if type(error) is ThreadedRuntimeError and error.code == "CLOSE_TIMEOUT_BEFORE_START":
                raise _CleanupNotStarted from None
            raise
        close_called = True
    elif before.state in ("closed", "failed"):
        closed = before
        close_called = False
    else:
        return None
    try:
        current, current_had_thread = runtime.cleanup_observation(timeout_seconds=remaining())
        remaining()
    except ThreadedRuntimeError as error:
        if (not close_called and type(error) is ThreadedRuntimeError
                and error.code in ("CLEANUP_SNAPSHOT_TIMEOUT", "CLEANUP_NOT_STARTED")):
            raise _CleanupNotStarted from None
        raise
    if (type(closed) is not RuntimeSnapshot or type(current) is not RuntimeSnapshot
            or closed != current or type(current_had_thread) is not bool
            or current_had_thread is not had_thread or current.thread_alive is not False
            or type(current.bindings) is not tuple or current.bindings != ()
            or current.identity != before.identity or current.run_id != before.run_id
            or not (current.strict_unregister_ack is True
                    or (never_started and current.strict_unregister_ack is None))
            or type(current.cleanup_failures) is not int or current.cleanup_failures != 0
            or not runtime_cleanup_confirmed(current)):
        return None
    return current


class _StrategyOwnerExtension:
    """Compose selected resources on the single runtime owner thread."""

    def __init__(
        self,
        account_settings,
        market_settings,
        instrument_settings,
        calendar_settings,
        *,
        account_ref,
        scope_ref,
        stream_scope,
        security_profiles,
        route,
        peer,
        instrument_status_enabled=False,
    ):
        self._route, self._peer = route, peer
        self._instrument_status_enabled = instrument_status_enabled
        self._calendar_settings = calendar_settings
        self._account_settings = account_settings
        self._market_settings = market_settings
        self._instrument_settings = instrument_settings
        self._account_ref, self._scope_ref = account_ref, scope_ref
        self._stream_scope, self._security_profiles = stream_scope, security_profiles
        self._account = self._market = self._instrument = self._calendar = self._instrument_status = None
        self._uncertainties = {"account": 0, "market": 0, "instrument": 0, "calendar": 0, "instrument_status": 0}

    def start(self, runtime):
        if self._instrument_status_enabled:
            from bt_api_py.gateway_strategy_instrument_status import GatewayInstrumentStatusOwner
            self._instrument_status = GatewayInstrumentStatusOwner(
                account_ref=self._account_ref, scope_ref=self._scope_ref, route=self._route, peer=self._peer)
            self._instrument_status.start(runtime)
        if self._calendar_settings is not None:
            from bt_api_py.gateway_strategy_calendar import GatewayCalendarOwner
            self._calendar = GatewayCalendarOwner(self._calendar_settings, account_ref=self._account_ref, scope_ref=self._scope_ref, route=self._route, peer=self._peer)
            self._calendar.start(runtime)
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
        if operation.startswith('instrument_status.') and self._instrument_status is not None:
            return self._instrument_status.invoke(operation.removeprefix('instrument_status.'), arguments)
        if operation in {
            "account.refresh",
            "account.snapshot",
            "account.status",
            "account.validate",
        }:
            if self._account is None:
                raise OwnerOperationError("ACCOUNT_UNAVAILABLE")
            return self._account.invoke(operation.removeprefix("account."), arguments)
        if operation.startswith("calendar.") and self._calendar is not None:
            return self._calendar.invoke(operation.removeprefix("calendar."), arguments)
        if operation.startswith("instrument.") and self._instrument is not None:
            return self._instrument.invoke(
                operation.removeprefix("instrument."), arguments
            )
        if operation.startswith("market.") and self._market is not None:
            return self._market.invoke(operation.removeprefix("market."), arguments)
        raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")

    @property
    def cleanup_uncertainties(self):
        for name in ("account", "market", "instrument", "calendar", "instrument_status"):
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
        for name in ("instrument_status", "calendar", "market", "instrument", "account"):
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
    """Bind one parsed Gateway route to one non-restartable runtime handle.

    ``strict_cleanup`` selects one-shot session cleanup with strict Registry
    unregister acknowledgement. It currently excludes optional account,
    market and instrument owners whose cleanup has a separate lifetime. A
    successful close does not prove in-flight handlers or account state. The
    exposed raw runtime remains an independently callable trusted interface;
    callers of this mode must use the session's lifecycle methods.
    Retention and one-shot cleanup apply only to this session object. They do
    not prevent a separate session for the same identity or scope from starting;
    an external owner must provide any required cross-session exclusion.
    """

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
        delegated_cancel_host=None,
        requested_operations: frozenset[str],
        queue_size: int = 32,
        strict_cleanup: bool = False,
        account_settings: GatewayAccountClientSettings | None = None,
        account_observer_operations: frozenset[str] | None = None,
        account_facts_operations: frozenset[str] | None = None,
        market_settings: GatewayMarketClientSettings | None = None,
        instrument_settings: GatewayInstrumentClientSettings | None = None,
        calendar_settings=None,
    ) -> None:
        if type(config) is not ClientConfig:
            raise ValueError("INVALID_CONFIG")
        if type(strict_cleanup) is not bool:
            raise ValueError("INVALID_CLEANUP_POLICY")
        if strict_cleanup and any(item is not None for item in (
            account_settings, market_settings, instrument_settings, calendar_settings
        )):
            # These owners have separate acquisition/cleanup lifetimes and
            # are not yet composed into this strict session's custody.
            raise ValueError("STRICT_OWNER_CLEANUP_UNAVAILABLE")
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
            if (account_settings is not None or instrument_settings is not None
                    or account_observer_operations is not None or account_facts_operations is not None):
                expected_mounts.add("store.account")
            if market_settings is not None:
                expected_mounts.add("store.market")
            if calendar_settings is not None:
                expected_mounts.add("store.calendar")
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
            or (
                "direct.delegated_cancel" in requested_operations
                and "direct.delegated_cancel_status" not in requested_operations
            )
            or (
                requested_operations & {"direct.delegated_cancel", "direct.delegated_cancel_status"}
                and delegated_cancel_host is None
            )
        ):
            raise ValueError("INVALID_REQUESTED_OPERATIONS")

        frozen_account = None
        frozen_account_facts = None
        if account_facts_operations is not None:
            supported_facts = frozenset({'account.facts.read_after', 'account.facts.progress', 'account.facts.ack', 'account.facts.status'})
            if (type(account_facts_operations) is not frozenset or not account_facts_operations
                    or not account_facts_operations <= supported_facts):
                raise ValueError('INVALID_ACCOUNT_FACTS_OPERATIONS')
            frozen_account_facts = account_facts_operations
        frozen_account_observer = None
        if account_observer_operations is not None:
            supported = frozenset({'account.events.read_after', 'account.events.progress', 'account.events.ack'})
            if (type(account_observer_operations) is not frozenset or not account_observer_operations
                    or not account_observer_operations <= supported):
                raise ValueError('INVALID_ACCOUNT_OBSERVER_OPERATIONS')
            frozen_account_observer = account_observer_operations
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
        frozen_calendar = None
        if calendar_settings is not None:
            from bt_api_py.gateway_strategy_calendar import GatewayCalendarClientSettings
            if type(calendar_settings) is not GatewayCalendarClientSettings:
                raise ValueError("INVALID_CALENDAR_SETTINGS")
            frozen_calendar = GatewayCalendarClientSettings(calendar_settings.mode, tuple(dict(item) for item in calendar_settings.scopes), calendar_settings.range_read)
        operation_map = {route_copy.mount: requested_operations}
        if frozen_account is not None:
            operation_map["store.account"] = frozen_account.requested_operations
        if frozen_account_observer is not None:
            operation_map["store.account"] = operation_map.get("store.account", frozenset()) | frozen_account_observer
        if frozen_account_facts is not None:
            operation_map["store.account"] = operation_map.get("store.account", frozenset()) | frozen_account_facts
        if frozen_instrument is not None:
            operation_map["store.account"] = (
                operation_map.get("store.account", frozenset())
                | frozen_instrument.requested_operations
            )
        if frozen_calendar is not None:
            operation_map["store.calendar"] = frozen_calendar.requested_operations
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
            cleanup_options = (
                {"require_unregister_ack": True, "cleanup_unknown": lambda: False,
                 "on_cleanup_sticky": lambda: None} if strict_cleanup else {}
            )
            return ServiceClientRuntime(
                config_copy,
                identity=runtime_identity,
                scope_resolver=scope_resolver,
                peer_resolver=peer_resolver,
                transport_factory=ZmqTransportFactory(frozen_security),
                run_id=scope.run_id,
                requested_operations=operation_map,
                **cleanup_options,
            )

        runtime_options = {}
        if (
            frozen_account is not None
            or frozen_market is not None
            or frozen_instrument is not None
            or frozen_calendar is not None
            or 'direct.instrument_status' in requested_operations
        ):
            runtime_options["owner_extension_factory"] = (
                lambda: _StrategyOwnerExtension(
                    frozen_account,
                    frozen_market,
                    frozen_instrument,
                    frozen_calendar,
                    account_ref=bound.account_scope,
                    scope_ref=route_copy.scope_ref,
                    stream_scope=WireScope(scope.execution_domain_ref),
                    security_profiles=frozen_security,
                    route=route_copy, peer=peer_copy,
                    instrument_status_enabled='direct.instrument_status' in requested_operations,
                )
            )
        if strict_cleanup:
            runtime_options.update(require_unregister_ack=True, cleanup_unknown=lambda: False,
                                   on_cleanup_sticky=lambda _runtime: None)
        runtime = ThreadedServiceClientRuntime(runtime_factory, queue_size=queue_size, **runtime_options)
        # Both clients validate the exact neutral binding, route and host before
        # the runtime can be started.
        command_client = GatewayDirectClient(
            runtime, binding=bound, route=route_copy, host=host, cancel_host=cancel_host
        )
        execution_stream_client = GatewayExecutionStreamClient(
            runtime, binding=bound, route=route_copy
        )
        position_client = GatewayStrategyPositionClient(
            runtime, binding=bound, route=route_copy
        )
        instrument_status_client = None
        if 'direct.instrument_status' in requested_operations:
            from bt_api_py.gateway_strategy_instrument_status import (
                GatewayStrategyInstrumentStatusClient,
            )
            instrument_status_client = GatewayStrategyInstrumentStatusClient(runtime, route=route_copy, peer=peer_copy)
        delegated_client = GatewayStrategyDelegatedClient(
            runtime, binding=bound, route=route_copy, cancel_host=delegated_cancel_host
        )
        from bt_api_py.gateway_strategy_financial import GatewayStrategyFinancialClient
        financial_client = GatewayStrategyFinancialClient(runtime, binding=bound, route=route_copy)
        account_observer = None
        account_facts = None
        if frozen_account_facts is not None:
            from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
            from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
            account_facts = GatewayAccountFactsClient(runtime, route=GatewayAccountObserverRoute(
                'store.account', route_copy.service_id, route_copy.instance_id, route_copy.scope_ref,
                route_copy.scope, route_copy.identity))
        if frozen_account_observer is not None:
            from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
            account_observer = GatewayAccountObserverClient(runtime, route=GatewayAccountObserverRoute(
                'store.account', route_copy.service_id, route_copy.instance_id, route_copy.scope_ref,
                route_copy.scope, route_copy.identity))
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
        calendar_client = None
        if frozen_calendar is not None:
            from bt_api_py.gateway_strategy_calendar import GatewayStrategyCalendarClient
            calendar_client = GatewayStrategyCalendarClient(runtime, frozen_calendar, route=route_copy, peer=peer_copy)
        self._calendar_client = calendar_client
        self._account_client, self._market_client = account_client, market_client
        self._instrument_client = instrument_client
        self._runtime = runtime
        self._strict_cleanup = strict_cleanup
        self._original_runtime = runtime
        self._lifecycle_lock = threading.Lock()
        self._close_requested = False
        self._start_uncertain = False
        self._start_scope_rejected = False
        self._cleanup_uncertain = False
        self._closed_snapshot = None
        self._command_client = command_client
        self._execution_stream_client = execution_stream_client
        self._position_client = position_client
        self._instrument_status_client = instrument_status_client
        self._delegated_client = delegated_client
        self._financial_client = financial_client
        self._account_observer = account_observer
        self._account_facts = account_facts
        self._binding = bound
        self._route = route_copy

    @property
    def account_client(self):
        return self._account_client

    @property
    def market_client(self):
        return self._market_client

    @property
    def calendar(self):
        return self._calendar_client

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
    def position_client(self) -> GatewayStrategyPositionClient:
        return self._position_client

    @property
    def instrument_status(self):
        return self._instrument_status_client

    @property
    def delegated_client(self) -> GatewayStrategyDelegatedClient:
        return self._delegated_client

    @property
    def financial_client(self):
        return self._financial_client

    @property
    def account_observer(self):
        return self._account_observer

    @property
    def account_facts(self):
        return self._account_facts

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
        if not self._strict_cleanup:
            if self._start_scope_rejected:
                raise ThreadedRuntimeError("REGISTRATION_SCOPE_MISMATCH")
            snapshot = self._runtime.start(timeout_seconds=timeout_seconds)
            if not _strategy_registration_scope_matches(snapshot, self._route):
                self._start_scope_rejected = True
                _RETAINED_STRATEGY_SESSIONS[id(self)] = self
                raise ThreadedRuntimeError("REGISTRATION_SCOPE_MISMATCH")
            return snapshot
        if not self._lifecycle_lock.acquire(blocking=False):
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        try:
            if (self._close_requested or self._cleanup_uncertain or self._start_uncertain
                    or self._start_scope_rejected
                    or self._runtime is not self._original_runtime
                    or type(self._runtime) is not ThreadedServiceClientRuntime
                    or self._runtime.strict_cleanup_required is not True):
                raise ThreadedRuntimeError("CLEANUP_POLICY_UNPROVEN")
            _RETAINED_STRATEGY_SESSIONS[id(self)] = self
            try:
                snapshot = self._runtime.start(timeout_seconds=timeout_seconds)
                if (type(snapshot) is not RuntimeSnapshot
                        or snapshot.strict_unregister_ack is not True
                        or snapshot.state != "running" or snapshot.healthy is not True
                        or snapshot.thread_alive is not True
                        or snapshot.identity != self._route.identity
                        or snapshot.run_id != self._route.scope.run_id
                        or self._close_requested):
                    raise ThreadedRuntimeError("CLEANUP_POLICY_UNPROVEN")
                if not _strategy_registration_scope_matches(snapshot, self._route):
                    self._start_scope_rejected = True
                    raise ThreadedRuntimeError("REGISTRATION_SCOPE_MISMATCH")
                return snapshot
            except BaseException:
                if not self._start_scope_rejected:
                    self._start_uncertain = True
                raise
        finally:
            self._lifecycle_lock.release()

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        if not self._strict_cleanup:
            snapshot = self._runtime.close(timeout_seconds=timeout_seconds)
            _release_scope_rejected_custody(self, self._original_runtime, snapshot)
            return snapshot
        self._close_requested = True
        if not self._lifecycle_lock.acquire(blocking=False):
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        try:
            if self._runtime is not self._original_runtime or self._cleanup_uncertain:
                raise ThreadedRuntimeError("CLEANUP_FAILED")
            if self._closed_snapshot is not None:
                return self._closed_snapshot
            try:
                valid_timeout = (type(timeout_seconds) in (int, float)
                                 and math.isfinite(timeout_seconds) and timeout_seconds > 0)
            except OverflowError:
                valid_timeout = False
            if not valid_timeout:
                raise ThreadedRuntimeError("CLEANUP_NOT_STARTED")
            _RETAINED_STRATEGY_SESSIONS[id(self)] = self
            # An interruption after the close starts is an unknown outcome.
            # This session will not reissue it or substitute its runtime.
            self._cleanup_uncertain = True
            try:
                snapshot = _strict_close_snapshot(
                    self._runtime, timeout_seconds, allow_close=not self._start_uncertain,
                    identity=self._route.identity, run_id=self._route.scope.run_id,
                )
                if snapshot is None:
                    raise ThreadedRuntimeError("CLEANUP_FAILED")
                self._closed_snapshot = snapshot
                self._cleanup_uncertain = False
                _RETAINED_STRATEGY_SESSIONS.pop(id(self), None)
                return snapshot
            except _CleanupNotStarted:
                self._cleanup_uncertain = False
                raise ThreadedRuntimeError("CLEANUP_NOT_STARTED") from None
            except Exception:
                raise ThreadedRuntimeError("CLEANUP_FAILED") from None
        finally:
            self._lifecycle_lock.release()

    def status(self) -> RuntimeSnapshot:
        return self._runtime.status()
