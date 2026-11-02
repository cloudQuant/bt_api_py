"""Exact-route composition for provider-owned SDK routes and runtimes.

This module is provider-neutral. It combines one provider-issued
``ProviderConfiguration`` with an opaque runtime binding, explicit access
mode, and (for managed writes) distinct operator-grant and SDK authorization
sources. It delegates route-only checks for stateless read-only providers, or
live readiness and provider/session checks for managed execution, to the
uniquely selected provider factory.

The binding and authorization source are carriers, not proof or permission.
For read-only construction the selected provider may verify only its exact
route/config/feed binding; that does not assert connection or account
readiness. Managed execution requires the selected provider to verify its live
runtime/session and existing authorization gates. Provider-native objects
stay inside the SDK. The parent composer itself never constructs a provider
runtime; an exact provider may bootstrap one from its opaque host binding and
owns its serve/close lifecycle. The composer never falls back to a direct
client when a provider is missing.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from threading import Lock, RLock
from typing import Callable, Literal, Optional

from bt_api_base.managed_runtime_protocol import (
    ManagedRuntimeAccessMode,
    ManagedRuntimeBinding,
    ManagedRuntimeContractError,
    ManagedRuntimeFactory,
    ManagedRuntimeFactoryRegistryPort,
    ManagedRuntimeProviderProduct,
    OperatorWriteGrant,
    ReadOnlyRouteOwnerCloseError,
    ReadOnlyRouteOwnerFactory,
    ReadOnlyRouteOwnerProduct,
    ReadOnlyRouteOwnerRegistryPort,
    SelectedReadOnlyRoute,
    SelectedManagedRuntimeRoute,
    RuntimeThreadAffinity,
    RuntimeThreadConstraint,
)
from bt_api_base.provider_configuration_protocol import ProviderConfiguration

from .facade_protocol import (
    AccountSessionFacade,
    AccountSessionPort,
    CorrelatedOrderExecutionFacade,
    CorrelatedOrderExecutionSelector,
    ExecutionCapacityFacade,
    ExecutionCapacitySelector,
    ExecutionEconomicsFacade,
    ExecutionEconomicsPort,
    ExecutionRulesFacade,
    ExecutionRulesSnapshotPort,
    MarketInstrumentKey,
    MarketSessionResolver,
    MarketSessionResolverSelector,
    MarketTickProjectorSelector,
    OrderRequest,
    CancelOrderRequest,
    QueryFacade,
    QueryFacadeSelector,
    QueryOrderRequest,
    ReconciliationFacadePort,
    ReconciliationFacadeSelector,
    SettlementConfirmationFacade,
    SettlementConfirmationPort,
    ServiceMarketTickProjector,
    UnifiedBtApiFacade,
)

__all__ = (
    "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_GROUP",
    "READ_ONLY_ROUTE_OWNER_ENTRY_POINT_GROUP",
    "ManagedRuntimeBinding",
    "ManagedRuntimeComposition",
    "ManagedRuntimeCompositionError",
    "ManagedRuntimeComposer",
    "ManagedRuntimeFactory",
    "ManagedRuntimeFactoryRegistry",
    "ManagedRuntimeProviderProduct",
    "ManagedExecutionRuntimeFacade",
    "OperatorWriteGrant",
    "ReadOnlyRouteOwnerComposer",
    "ReadOnlyRouteOwnerFactory",
    "ReadOnlyRouteOwnerProduct",
    "ReadOnlyRouteOwnerRegistry",
    "ReadOnlyRouteOwnerRegistryPort",
    "ReadOnlyManagedRuntimeFacade",
    "RuntimeThreadAffinity",
    "RuntimeThreadConstraint",
    "SelectedReadOnlyRoute",
    "SelectedManagedRuntimeRoute",
    "compose_read_only_runtime",
    "compose_managed_runtime",
    "default_managed_runtime_factory_registry",
    "default_read_only_route_owner_registry",
    "register_managed_runtime_factory",
    "register_read_only_route_owner_factory",
)


MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_GROUP = "bt_api.managed_runtime_providers"
READ_ONLY_ROUTE_OWNER_ENTRY_POINT_GROUP = "bt_api.read_only_route_owners"


class ManagedRuntimeCompositionError(RuntimeError):
    """Finite, non-sensitive status from managed runtime composition."""

    _CODES = frozenset(
        {
            "MANAGED_RUNTIME_ROUTE_INVALID",
            "MANAGED_RUNTIME_CONFIGURATION_INVALID",
            "MANAGED_RUNTIME_BINDING_UNAVAILABLE",
            "MANAGED_RUNTIME_BINDING_ROUTE_MISMATCH",
            "MANAGED_RUNTIME_BINDING_UNVERIFIED",
            "MANAGED_RUNTIME_AUTHORIZATION_SOURCE_UNAVAILABLE",
            "MANAGED_RUNTIME_OPERATOR_GRANT_INVALID",
            "MANAGED_RUNTIME_OPERATOR_GRANT_REJECTED",
            "MANAGED_RUNTIME_ACCESS_MODE_INVALID",
            "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE",
            "MANAGED_RUNTIME_WRITE_UNAVAILABLE",
            "MANAGED_RUNTIME_READ_FAILED",
            "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE",
            "MANAGED_RUNTIME_PROVIDER_UNAVAILABLE",
            "MANAGED_RUNTIME_PROVIDER_DISCOVERY_FAILED",
            "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_CONFLICT",
            "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_INVALID",
            "MANAGED_RUNTIME_PROVIDER_IMPORT_FAILED",
            "MANAGED_RUNTIME_PROVIDER_REGISTRATION_FAILED",
            "MANAGED_RUNTIME_PROVIDER_REGISTRATION_SCOPE_INVALID",
            "MANAGED_RUNTIME_PROVIDER_CONFLICT",
            "MANAGED_RUNTIME_ROUTE_OWNER_UNAVAILABLE",
            "MANAGED_RUNTIME_ROUTE_OWNER_DISCOVERY_FAILED",
            "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT",
            "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_INVALID",
            "MANAGED_RUNTIME_ROUTE_OWNER_IMPORT_FAILED",
            "MANAGED_RUNTIME_ROUTE_OWNER_REGISTRATION_FAILED",
            "MANAGED_RUNTIME_ROUTE_OWNER_SCOPE_INVALID",
            "MANAGED_RUNTIME_ROUTE_OWNER_INVALID",
            "MANAGED_RUNTIME_FACTORY_INVALID",
            "MANAGED_RUNTIME_COMPOSITION_FAILED",
            "MANAGED_RUNTIME_FACADE_INVALID",
            "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH",
            "MANAGED_RUNTIME_CLOSE_ALREADY_ATTEMPTED",
            "MANAGED_RUNTIME_CLOSE_FAILED",
            "MANAGED_RUNTIME_SERVE_UNAVAILABLE",
            "MANAGED_RUNTIME_SERVE_FAILED",
            "MANAGED_RUNTIME_SERVE_AFTER_CLOSE",
            "MANAGED_RUNTIME_SERVE_AFTER_FAILURE",
            "MANAGED_RUNTIME_SERVE_BUSY",
            "MANAGED_RUNTIME_CLOSE_BUSY",
            "MANAGED_RUNTIME_THREAD_CONSTRAINT_INVALID",
            "MANAGED_RUNTIME_OWNER_THREAD_UNVERIFIED",
            "MANAGED_RUNTIME_WRONG_THREAD",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in self._CODES
            else "MANAGED_RUNTIME_COMPOSITION_FAILED"
        )
        super().__init__(self.code)


class ReadOnlyManagedRuntimeFacade:
    """Allowlisted query/session/feed facade over one route-verified owner.

    Write-shaped base methods reject without delegation. There is no dynamic
    attribute forwarding and no settlement confirmation or correlated-write
    selector on this object.
    """

    __slots__ = ("__api", "__exchange_name", "__close_callback")

    def __init__(
        self,
        api: UnifiedBtApiFacade,
        exchange_name: str,
        *,
        close_callback: Callable[[], None],
    ) -> None:
        self.__api = api
        self.__exchange_name = exchange_name
        self.__close_callback = close_callback

    def list_exchanges(self) -> list[str]:
        self.__check_route()
        return [self.__exchange_name]

    async def async_make_order(
        self,
        exchange_name: str,
        request: OrderRequest,
        *,
        normalized: Literal[True] = True,
        budget_capability: object | None = None,
    ) -> Mapping[str, object]:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_WRITE_UNAVAILABLE")

    async def async_cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        normalized: Literal[True] = True,
        budget_capability: object | None = None,
    ) -> Mapping[str, object]:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_WRITE_UNAVAILABLE")

    async def async_query_order(
        self,
        exchange_name: str,
        request: QueryOrderRequest,
        *,
        normalized: Literal[True] = True,
    ) -> Mapping[str, object]:
        self.__require_selected_route(exchange_name)
        try:
            result = await self.__api.async_query_order(
                exchange_name,
                request,
                normalized=normalized,
            )
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED") from None
        if not isinstance(result, Mapping):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED")
        return result

    def poll_events(
        self,
        exchange_name: str,
        *,
        max_raw_items: int | None = None,
        coalesce_market_snapshots: Iterable[str] = ("orderbook", "tick"),
    ) -> Iterable[Mapping[str, object]]:
        self.__require_selected_route(exchange_name)
        try:
            events = self.__api.poll_events(
                exchange_name,
                max_raw_items=max_raw_items,
                coalesce_market_snapshots=coalesce_market_snapshots,
            )
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED") from None
        try:
            iterator = iter(events)
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED") from None
        return self.__iter_event_records(iterator)

    def query_facade(self, exchange_name: str) -> QueryFacade:
        self.__require_selected_route(exchange_name)
        try:
            if not isinstance(self.__api, QueryFacadeSelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = self.__api.query_facade(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if type(selected) is not QueryFacade:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def account_session(self, exchange_name: str) -> AccountSessionPort:
        self.__require_selected_route(exchange_name)
        try:
            if not isinstance(self.__api, AccountSessionFacade):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = self.__api.account_session(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if not isinstance(selected, AccountSessionPort):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def market_tick_projector(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> ServiceMarketTickProjector:
        self.__require_selected_route(exchange_name)
        if type(instrument) is not MarketInstrumentKey:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        try:
            if not isinstance(self.__api, MarketTickProjectorSelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = self.__api.market_tick_projector(exchange_name, instrument)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if type(selected) is not ServiceMarketTickProjector:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def market_session_resolver(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> MarketSessionResolver:
        self.__require_selected_route(exchange_name)
        if type(instrument) is not MarketInstrumentKey:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        try:
            if not isinstance(self.__api, MarketSessionResolverSelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = self.__api.market_session_resolver(exchange_name, instrument)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if not isinstance(selected, MarketSessionResolver):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def close(self) -> None:
        self.__close_callback()

    def __require_selected_route(self, exchange_name: object) -> None:
        if type(exchange_name) is not str or exchange_name != self.__exchange_name:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH"
            )
        self.__check_route()

    def __check_route(self) -> None:
        try:
            exchanges = self.__api.list_exchanges()
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED") from None
        if (
            type(exchanges) is not list
            or any(type(name) is not str for name in exchanges)
            or exchanges != [self.__exchange_name]
        ):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH"
            )

    @staticmethod
    def __iter_event_records(
        events: Iterable[Mapping[str, object]],
    ) -> Iterator[Mapping[str, object]]:
        try:
            for event in events:
                if not isinstance(event, Mapping):
                    raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED")
                yield event
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_READ_FAILED") from None


class ManagedExecutionRuntimeFacade(ReadOnlyManagedRuntimeFacade):
    """Allowlisted managed facade with only explicit typed write capability.

    Legacy ``async_make_order`` and ``async_cancel_order`` remain blocked.
    Writes are available only through the route-bound correlated execution
    facade, which requires separate SDK authorization and claim values.
    """

    __slots__ = ()

    def correlated_order_execution(
        self,
        exchange_name: str,
    ) -> CorrelatedOrderExecutionFacade:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, CorrelatedOrderExecutionSelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
                )
            selected = api.correlated_order_execution(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
            ) from None
        if not isinstance(selected, CorrelatedOrderExecutionFacade):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
            )
        return selected

    def execution_rules(self, exchange_name: str) -> ExecutionRulesSnapshotPort:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, ExecutionRulesFacade):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = api.execution_rules(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if not isinstance(selected, ExecutionRulesSnapshotPort):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def execution_economics(self, exchange_name: str) -> ExecutionEconomicsPort:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, ExecutionEconomicsFacade):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = api.execution_economics(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if not isinstance(selected, ExecutionEconomicsPort):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def reconciliation(self, exchange_name: str) -> ReconciliationFacadePort:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, ReconciliationFacadeSelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = api.reconciliation(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if not isinstance(selected, ReconciliationFacadePort):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def order_capacity(self, exchange_name: str) -> ExecutionCapacityFacade:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, ExecutionCapacitySelector):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
                )
            selected = api.order_capacity(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            ) from None
        if type(selected) is not ExecutionCapacityFacade:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE"
            )
        return selected

    def settlement_confirmation(
        self,
        exchange_name: str,
    ) -> SettlementConfirmationPort:
        self._ReadOnlyManagedRuntimeFacade__require_selected_route(exchange_name)
        api = self._ReadOnlyManagedRuntimeFacade__api
        try:
            if not isinstance(api, SettlementConfirmationFacade):
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
                )
            selected = api.settlement_confirmation(exchange_name)
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
            ) from None
        if not isinstance(selected, SettlementConfirmationPort):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CORRELATED_EXECUTION_UNAVAILABLE"
            )
        return selected


class ManagedRuntimeFactoryRegistry(ManagedRuntimeFactoryRegistryPort):
    """Duplicate-rejecting exact-route registration table for runtime factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ManagedRuntimeFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: ManagedRuntimeFactory) -> None:
        _require_text(exchange_name, "MANAGED_RUNTIME_ROUTE_INVALID")
        if not callable(factory):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_PROVIDER_CONFLICT")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ManagedRuntimeFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_text(exchange_name, "MANAGED_RUNTIME_ROUTE_INVALID")
        if not callable(factory):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_PROVIDER_CONFLICT")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> ManagedRuntimeFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = ManagedRuntimeFactoryRegistry()


def default_managed_runtime_factory_registry() -> ManagedRuntimeFactoryRegistry:
    """Return the SDK process registry; no provider is installed by default."""

    return _DEFAULT_REGISTRY


def register_managed_runtime_factory(
    exchange_name: str,
    factory: ManagedRuntimeFactory,
    *,
    registry: ManagedRuntimeFactoryRegistry | None = None,
) -> None:
    """Register one provider-owned factory for the exact exchange route."""

    selected = _select_registry(registry)
    selected.register(exchange_name, factory)


class ReadOnlyRouteOwnerRegistry(ReadOnlyRouteOwnerRegistryPort):
    """Duplicate-rejecting exact-route registry for stateless route owners."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ReadOnlyRouteOwnerFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: ReadOnlyRouteOwnerFactory) -> None:
        _require_text(exchange_name, "MANAGED_RUNTIME_ROUTE_INVALID")
        if not callable(factory):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ReadOnlyRouteOwnerFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_text(exchange_name, "MANAGED_RUNTIME_ROUTE_INVALID")
        if not callable(factory):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> ReadOnlyRouteOwnerFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_READ_ONLY_ROUTE_OWNER_REGISTRY = ReadOnlyRouteOwnerRegistry()


def default_read_only_route_owner_registry() -> ReadOnlyRouteOwnerRegistry:
    """Return the process registry; provider entry points load on exact route use."""

    return _DEFAULT_READ_ONLY_ROUTE_OWNER_REGISTRY


def register_read_only_route_owner_factory(
    exchange_name: str,
    factory: ReadOnlyRouteOwnerFactory,
    *,
    registry: ReadOnlyRouteOwnerRegistry | None = None,
) -> None:
    selected = _select_read_only_route_owner_registry(registry)
    selected.register(exchange_name, factory)


class ManagedRuntimeComposition:
    """Neutral facade with explicit single-step serve and close handles.

    The runtime/route binding, provider factory, and authorization source
    remain private. ``serve_once`` invokes at most one synchronous provider
    maintenance step and never creates a polling thread. Provider code
    supplies an explicit thread constraint; ``assert_owner_thread`` checks it
    on the actual calling thread before serve or close. Closing delegates one time to the
    provider's route close function, which owns or borrows the runtime
    according to its exact route contract.
    """

    def __init__(
        self,
        api: UnifiedBtApiFacade,
        exchange_name: str,
        close_route: Callable[[], None],
        *,
        access_mode: ManagedRuntimeAccessMode,
        serve_route: Callable[[], object] | None = None,
        thread_constraint: RuntimeThreadConstraint | None = None,
    ) -> None:
        if type(access_mode) is not str or access_mode not in {
            "read_only",
            "managed_execution",
        }:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_ACCESS_MODE_INVALID")
        if serve_route is not None and not callable(serve_route):
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        if thread_constraint is not None and type(thread_constraint) is not RuntimeThreadConstraint:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_THREAD_CONSTRAINT_INVALID"
            )
        self.__exchange_name = exchange_name
        self.__close_route = close_route
        self.__serve_route = serve_route
        self.__thread_constraint = thread_constraint
        self.__lock = Lock()
        self.__close_attempted = False
        self.__serve_active = False
        self.__serve_failed = False
        if access_mode == "read_only":
            self.__api = ReadOnlyManagedRuntimeFacade(
                api,
                exchange_name,
                close_callback=self.close,
            )
        elif access_mode == "managed_execution":
            self.__api = ManagedExecutionRuntimeFacade(
                api,
                exchange_name,
                close_callback=self.close,
            )

    @property
    def api(self) -> UnifiedBtApiFacade:
        """Return the neutral SDK facade for this exact route."""

        return self.__api

    @property
    def exchange_name(self) -> str:
        return self.__exchange_name

    def assert_owner_thread(self) -> None:
        """Verify the actual calling thread before any runtime operation.

        The provider contract either explicitly guarantees any-thread safety,
        or checks the current thread against its private runtime owner. This
        method takes no thread ID and cannot be satisfied by marshalling a
        callback to another thread.
        """

        with self.__lock:
            if self.__close_attempted:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_CLOSE_ALREADY_ATTEMPTED"
                )
            if self.__serve_active:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_SERVE_BUSY")
            constraint = self.__thread_constraint
        if constraint is None:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OWNER_THREAD_UNVERIFIED"
            )
        try:
            constraint.assert_current_thread()
        except ManagedRuntimeContractError as exc:
            raise ManagedRuntimeCompositionError(exc.code) from None

    def close(self) -> None:
        """Close the composed route once; never retry an uncertain close."""

        self.assert_owner_thread()
        with self.__lock:
            if self.__close_attempted:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_CLOSE_ALREADY_ATTEMPTED"
                )
            if self.__serve_active:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CLOSE_BUSY")
            self.__close_attempted = True
        try:
            self.__close_route()
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CLOSE_FAILED") from None

    def serve_once(self) -> None:
        """Run one synchronous provider maintenance step on the owner thread.

        The provider callback must perform one bounded pump and return
        ``None``. It must not start a background worker or retry uncertain
        work. Exceptions become a finite SDK failure and are never suppressed.
        Callers may invoke this again for a later maintenance tick only after
        the prior call returned normally.
        """

        self.assert_owner_thread()
        with self.__lock:
            if self.__close_attempted:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_SERVE_AFTER_CLOSE"
                )
            if self.__serve_failed:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_SERVE_AFTER_FAILURE"
                )
            callback = self.__serve_route
            if callback is None:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_SERVE_UNAVAILABLE"
                )
            if self.__serve_active:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_SERVE_BUSY")
            self.__serve_active = True
        try:
            result = callback()
            if inspect.isawaitable(result):
                close_awaitable = getattr(result, "close", None)
                if callable(close_awaitable):
                    close_awaitable()
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_SERVE_FAILED")
            if result is not None:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_SERVE_FAILED")
        except Exception as exc:
            with self.__lock:
                self.__serve_failed = True
            if type(exc) is ManagedRuntimeCompositionError:
                raise
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_SERVE_FAILED") from None
        finally:
            with self.__lock:
                self.__serve_active = False

    def __repr__(self) -> str:
        return "<SDK managed runtime composition route={!r}>".format(self.__exchange_name)


class ManagedRuntimeComposer:
    """Compose a neutral facade from exact config, binding, and provider."""

    def __init__(self, registry: ManagedRuntimeFactoryRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = RLock()

    def compose(
        self,
        configuration: ProviderConfiguration,
        *,
        runtime_binding: ManagedRuntimeBinding,
        access_mode: ManagedRuntimeAccessMode,
        authorization_source: object | None = None,
        operator_grant: OperatorWriteGrant | None = None,
    ) -> ManagedRuntimeComposition:
        """Compose one exact route from explicit configuration and owner inputs.

        ``read_only`` accepts no operator grant or authorization source and
        returns an allowlisted query/session/feed facade whose write methods
        reject. A provider may verify only an exact route/config binding for a
        stateless read-only API; that result does not mean connected or
        account-ready. ``managed_execution`` requires both values separately
        and a live runtime/session check. The operator grant supplies bounded
        operator intent only; it never creates SDK Basic/Risk authorization.
        The selected provider must independently validate all exact inputs.
        """

        route = _selected_route(
            configuration,
            runtime_binding=runtime_binding,
            access_mode=access_mode,
            authorization_source=authorization_source,
            operator_grant=operator_grant,
        )
        factory = self._resolve_factory(route.exchange_name)
        try:
            product = factory(route)
        except ManagedRuntimeCompositionError:
            raise
        except ManagedRuntimeContractError as exc:
            raise ManagedRuntimeCompositionError(exc.code) from None
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_COMPOSITION_FAILED"
            ) from None
        if type(product) is not ManagedRuntimeProviderProduct:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        if route.access_mode == "managed_execution":
            if product.runtime_binding_verified is not True:
                _close_rejected_product(product)
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_BINDING_UNVERIFIED"
                )
        elif (
            product.runtime_binding_verified is not True
            and product.route_binding_verified is not True
        ):
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_BINDING_UNVERIFIED")
        if route.access_mode == "managed_execution" and product.operator_grant_verified is not True:
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OPERATOR_GRANT_REJECTED"
            )
        if route.access_mode == "read_only" and product.operator_grant_verified is not False:
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        try:
            valid_api = isinstance(product.api, UnifiedBtApiFacade)
        except Exception:
            valid_api = False
        if not valid_api:
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACADE_INVALID")
        try:
            exchanges = product.api.list_exchanges()
        except Exception:
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACADE_INVALID") from None
        if (
            type(exchanges) is not list
            or any(type(name) is not str for name in exchanges)
            or len(exchanges) != 1
            or exchanges[0] != route.exchange_name
        ):
            _close_rejected_product(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH")
        return ManagedRuntimeComposition(
            product.api,
            route.exchange_name,
            product.close_route,
            access_mode=route.access_mode,
            serve_route=product.serve_route,
            thread_constraint=product.thread_constraint,
        )

    def _resolve_factory(self, exchange_name: str) -> ManagedRuntimeFactory:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_CONFLICT"
            )
        if not entry_points:
            factory = self._registry._resolve(exchange_name)
            if factory is None:
                raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_PROVIDER_UNAVAILABLE")
            return factory

        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        registered_source = self._registry._entry_point_source(exchange_name)
        factory = self._registry._resolve(exchange_name)
        if factory is not None:
            if registered_source == source:
                return factory
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_PROVIDER_CONFLICT")

        try:
            registrar = entry_point.load()
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_PROVIDER_IMPORT_FAILED"
            ) from None
        if not callable(registrar):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_INVALID"
            )
        isolated = ManagedRuntimeFactoryRegistry()
        try:
            registrar(isolated)
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_PROVIDER_REGISTRATION_FAILED"
            ) from None
        if isolated._registered_routes() != (exchange_name,):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_PROVIDER_REGISTRATION_SCOPE_INVALID"
            )
        factory = isolated._resolve(exchange_name)
        if factory is None:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
        try:
            self._registry._register_entry_point_factory(exchange_name, factory, source)
        except ManagedRuntimeCompositionError:
            if self._registry._entry_point_source(exchange_name) != source:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_PROVIDER_CONFLICT"
                ) from None
            existing = self._registry._resolve(exchange_name)
            if existing is None:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_PROVIDER_UNAVAILABLE"
                ) from None
            return existing
        return factory


class ReadOnlyRouteOwnerComposer:
    """Build one route-only owner from provider configuration and entry points.

    The provider selected by the exact configuration route consumes the
    provider-owned opaque handle and constructs its client. This composer
    neither opens configuration files nor reads credentials from the handle.
    Route proof is accepted only for this write-denying read-only facade; it
    does not mean the client is connected or the account is ready.
    """

    def __init__(self, registry: ReadOnlyRouteOwnerRegistry | None = None) -> None:
        self._registry = _select_read_only_route_owner_registry(registry)
        self._lock = RLock()

    def compose(self, configuration: ProviderConfiguration) -> ManagedRuntimeComposition:
        exchange_name = _validate_read_only_configuration(configuration)
        factory = self._resolve_factory(exchange_name)
        try:
            from .bt_api import BtApi
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_BINDING_UNAVAILABLE"
            ) from None
        try:
            selected_route = SelectedReadOnlyRoute(
                exchange_name=exchange_name,
                configuration=configuration,
                api_factory=BtApi,
            )
        except ManagedRuntimeContractError as exc:
            raise ManagedRuntimeCompositionError(exc.code) from None
        try:
            product = factory(selected_route)
        except ReadOnlyRouteOwnerCloseError:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CLOSE_FAILED") from None
        except ManagedRuntimeCompositionError:
            raise
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_COMPOSITION_FAILED"
            ) from None
        if type(product) is not ReadOnlyRouteOwnerProduct:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_INVALID"
            )
        if product.exchange_name != exchange_name:
            _close_rejected_route_owner(product)
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH"
            )
        if product.route_binding_verified is not True:
            _close_rejected_route_owner(product)
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_BINDING_UNVERIFIED"
            )
        try:
            valid_api = isinstance(product.api, UnifiedBtApiFacade)
        except Exception:
            valid_api = False
        if not valid_api:
            _close_rejected_route_owner(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACADE_INVALID")
        try:
            exchanges = product.api.list_exchanges()
        except Exception:
            _close_rejected_route_owner(product)
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACADE_INVALID") from None
        if (
            type(exchanges) is not list
            or any(type(name) is not str for name in exchanges)
            or exchanges != [exchange_name]
        ):
            _close_rejected_route_owner(product)
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH"
            )
        return ManagedRuntimeComposition(
            product.api,
            exchange_name,
            product.close_route,
            access_mode="read_only",
            thread_constraint=product.thread_constraint,
        )

    def _resolve_factory(self, exchange_name: str) -> ReadOnlyRouteOwnerFactory:
        entry_points = _route_entry_points(
            exchange_name,
            group=READ_ONLY_ROUTE_OWNER_ENTRY_POINT_GROUP,
        )
        if len(entry_points) > 1:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT"
            )
        if not entry_points:
            factory = self._registry._resolve(exchange_name)
            if factory is None:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_ROUTE_OWNER_UNAVAILABLE"
                )
            return factory
        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        registered_source = self._registry._entry_point_source(exchange_name)
        factory = self._registry._resolve(exchange_name)
        if factory is not None:
            if registered_source == source:
                return factory
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT"
            )
        try:
            registrar = entry_point.load()
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_IMPORT_FAILED"
            ) from None
        if not callable(registrar):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_INVALID"
            )
        isolated = ReadOnlyRouteOwnerRegistry()
        try:
            registrar(isolated)
        except Exception:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_REGISTRATION_FAILED"
            ) from None
        if isolated._registered_routes() != (exchange_name,):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_SCOPE_INVALID"
            )
        factory = isolated._resolve(exchange_name)
        if factory is None:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_ROUTE_OWNER_INVALID"
            )
        try:
            self._registry._register_entry_point_factory(exchange_name, factory, source)
        except ManagedRuntimeCompositionError:
            if self._registry._entry_point_source(exchange_name) != source:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_ROUTE_OWNER_ENTRY_POINT_CONFLICT"
                ) from None
            existing = self._registry._resolve(exchange_name)
            if existing is None:
                raise ManagedRuntimeCompositionError(
                    "MANAGED_RUNTIME_ROUTE_OWNER_UNAVAILABLE"
                ) from None
            return existing
        return factory


def compose_managed_runtime(
    configuration: ProviderConfiguration,
    *,
    runtime_binding: ManagedRuntimeBinding,
    access_mode: ManagedRuntimeAccessMode,
    authorization_source: object | None = None,
    operator_grant: OperatorWriteGrant | None = None,
    registry: ManagedRuntimeFactoryRegistry | None = None,
) -> ManagedRuntimeComposition:
    """Compose using the process registry or one explicitly supplied registry."""

    return ManagedRuntimeComposer(registry).compose(
        configuration,
        runtime_binding=runtime_binding,
        access_mode=access_mode,
        authorization_source=authorization_source,
        operator_grant=operator_grant,
    )


def compose_read_only_runtime(
    configuration: ProviderConfiguration,
    *,
    registry: ReadOnlyRouteOwnerRegistry | None = None,
) -> ManagedRuntimeComposition:
    """Compose the exact configured route without a caller-supplied runtime.

    The selected provider consumes its private configuration handle and
    returns a route-verified owner. The result denies order and cancel writes;
    it does not assert a connected session or account readiness.
    """

    return ReadOnlyRouteOwnerComposer(registry).compose(configuration)


def _selected_route(
    configuration: ProviderConfiguration,
    *,
    runtime_binding: ManagedRuntimeBinding,
    access_mode: ManagedRuntimeAccessMode,
    authorization_source: object | None,
    operator_grant: OperatorWriteGrant | None,
) -> SelectedManagedRuntimeRoute:
    if type(configuration) is not ProviderConfiguration:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CONFIGURATION_INVALID")
    # Configuration construction validates its schema. Re-read only the
    # secret-free route selectors here; provider-owned opaque configuration
    # handles are passed through untouched and never unpacked by this module.
    try:
        _require_text(configuration.exchange_name, "MANAGED_RUNTIME_CONFIGURATION_INVALID")
        _require_text(configuration.environment, "MANAGED_RUNTIME_CONFIGURATION_INVALID")
        if type(configuration.instrument) is not MarketInstrumentKey:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CONFIGURATION_INVALID"
            )
        if configuration.instrument.environment != configuration.environment:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CONFIGURATION_INVALID"
            )
    except ManagedRuntimeCompositionError:
        raise
    except Exception:
        raise ManagedRuntimeCompositionError(
            "MANAGED_RUNTIME_CONFIGURATION_INVALID"
        ) from None
    if type(runtime_binding) is not ManagedRuntimeBinding:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_BINDING_UNAVAILABLE")
    if type(access_mode) is not str or access_mode not in {
        "read_only",
        "managed_execution",
    }:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_ACCESS_MODE_INVALID")
    if (
        runtime_binding.exchange_name != configuration.exchange_name
        or runtime_binding.environment != configuration.environment
    ):
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_BINDING_ROUTE_MISMATCH")
    if access_mode == "read_only":
        if authorization_source is not None or operator_grant is not None:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_ACCESS_MODE_INVALID")
    else:
        if _is_missing_authorization_source(authorization_source):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_AUTHORIZATION_SOURCE_UNAVAILABLE"
            )
        if type(operator_grant) is not OperatorWriteGrant:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OPERATOR_GRANT_INVALID"
            )
        if authorization_source is operator_grant or authorization_source is runtime_binding:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_AUTHORIZATION_SOURCE_UNAVAILABLE"
            )
        if (
            operator_grant.provider_ref != configuration.instrument.provider
            or operator_grant.environment != configuration.environment
            or configuration.instrument.native_symbol not in operator_grant.symbols
            or (
                operator_grant.mode == "live"
                and operator_grant.allow_live_write is not True
            )
        ):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OPERATOR_GRANT_REJECTED"
            )
        try:
            expires_at_utc = datetime.fromisoformat(
                operator_grant.expires_at_utc[:-1] + "+00:00"
            )
        except (ValueError, OverflowError):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OPERATOR_GRANT_INVALID"
            ) from None
        if expires_at_utc <= datetime.now(timezone.utc):
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_OPERATOR_GRANT_REJECTED"
            )
    api_factory = None
    if access_mode == "read_only":
        try:
            from .bt_api import BtApi
        except Exception:
            raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_BINDING_UNAVAILABLE") from None
        api_factory = BtApi
    try:
        return SelectedManagedRuntimeRoute(
            exchange_name=configuration.exchange_name,
            environment=configuration.environment,
            access_mode=access_mode,
            configuration=configuration,
            runtime_binding=runtime_binding,
            operator_grant=operator_grant,
            authorization_source=authorization_source,
            api_factory=api_factory,
        )
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeCompositionError(exc.code) from None


def _validate_read_only_configuration(configuration: object) -> str:
    """Validate only the public route selectors; never unwrap its handle."""

    if type(configuration) is not ProviderConfiguration:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CONFIGURATION_INVALID")
    try:
        _require_text(configuration.exchange_name, "MANAGED_RUNTIME_CONFIGURATION_INVALID")
        _require_text(configuration.environment, "MANAGED_RUNTIME_CONFIGURATION_INVALID")
        if type(configuration.instrument) is not MarketInstrumentKey:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CONFIGURATION_INVALID"
            )
        if configuration.instrument.environment != configuration.environment:
            raise ManagedRuntimeCompositionError(
                "MANAGED_RUNTIME_CONFIGURATION_INVALID"
            )
    except ManagedRuntimeCompositionError:
        raise
    except Exception:
        raise ManagedRuntimeCompositionError(
            "MANAGED_RUNTIME_CONFIGURATION_INVALID"
        ) from None
    return configuration.exchange_name


def _is_missing_authorization_source(value: object) -> bool:
    return value is None or type(value) in (bool, str, bytes, int, float)


def _close_rejected_product(product: ManagedRuntimeProviderProduct) -> None:
    """Attempt one provider cleanup after composition validation fails."""

    _assert_product_thread(product.thread_constraint)
    try:
        product.close_route()
    except Exception:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CLOSE_FAILED") from None


def _close_rejected_route_owner(product: ReadOnlyRouteOwnerProduct) -> None:
    """Attempt one close after route-owner product validation fails."""

    _assert_product_thread(product.thread_constraint)
    try:
        product.close_route()
    except Exception:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_CLOSE_FAILED") from None


def _assert_product_thread(
    constraint: RuntimeThreadConstraint | None,
) -> None:
    if constraint is None:
        raise ManagedRuntimeCompositionError(
            "MANAGED_RUNTIME_OWNER_THREAD_UNVERIFIED"
        )
    try:
        constraint.assert_current_thread()
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeCompositionError(exc.code) from None


def _select_registry(
    registry: ManagedRuntimeFactoryRegistry | None,
) -> ManagedRuntimeFactoryRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not ManagedRuntimeFactoryRegistry:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
    return registry


def _select_read_only_route_owner_registry(
    registry: ReadOnlyRouteOwnerRegistry | None,
) -> ReadOnlyRouteOwnerRegistry:
    if registry is None:
        return _DEFAULT_READ_ONLY_ROUTE_OWNER_REGISTRY
    if type(registry) is not ReadOnlyRouteOwnerRegistry:
        raise ManagedRuntimeCompositionError("MANAGED_RUNTIME_FACTORY_INVALID")
    return registry


def _route_entry_points(
    exchange_name: str,
    *,
    group: str = MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_GROUP,
) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(discovered.select(group=group))
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(group, ()))
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None) == group
            )
        return tuple(
            entry
            for entry in candidates
            if getattr(entry, "name", None) == exchange_name
        )
    except Exception:
        code = (
            "MANAGED_RUNTIME_ROUTE_OWNER_DISCOVERY_FAILED"
            if group == READ_ONLY_ROUTE_OWNER_ENTRY_POINT_GROUP
            else "MANAGED_RUNTIME_PROVIDER_DISCOVERY_FAILED"
        )
        raise ManagedRuntimeCompositionError(code) from None


def _entry_point_identity(
    entry_point: object,
    exchange_name: str,
) -> tuple[str, str, str]:
    try:
        value = getattr(entry_point, "value", None)
        distribution = getattr(entry_point, "dist", None)
        distribution_name = (
            getattr(distribution, "name", "") if distribution is not None else ""
        )
        entry_name = getattr(entry_point, "name", None)
    except Exception:
        raise ManagedRuntimeCompositionError(
            "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_INVALID"
        ) from None
    if (
        type(value) is not str
        or not value
        or type(distribution_name) is not str
        or type(entry_name) is not str
        or entry_name != exchange_name
    ):
        raise ManagedRuntimeCompositionError(
            "MANAGED_RUNTIME_PROVIDER_ENTRY_POINT_INVALID"
        )
    return exchange_name, value, distribution_name


def _require_text(value: object, error_code: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ManagedRuntimeCompositionError(error_code)
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ManagedRuntimeCompositionError(error_code) from None
    if size > 256:
        raise ManagedRuntimeCompositionError(error_code)
