"""Compose exact-route provider adapters with an injected service runtime.

Provider packages only choose a neutral adapter for an SDK-selected route.
The trusted host runtime factory owns service configuration, source and
entitlement references, and ``ServiceClientRuntime``. This module creates the
exact public service projectors but never infers a source, clock, freshness,
authorization, venue, or Backtrader object from client shape.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import metadata as importlib_metadata
import threading
from typing import Protocol, runtime_checkable

from bt_api_base.market_tick_provider_protocol import (
    MarketTickProviderFactory,
    MarketTickProviderRegistryPort,
    MarketTickProviderRouteError,
    MarketTickSourceBinding,
    MarketTickSourceBindingError,
    SelectedMarketTickRoute,
)
from bt_api_py.service_contracts import MarketSubscriptionKey


MARKET_TICK_PROVIDER_ENTRY_POINT_GROUP = "bt_api.market_tick_providers"


class MarketTickCompositionError(RuntimeError):
    """Finite, value-free market tick capability composition failures."""

    _CODES = frozenset(
        {
            "MARKET_TICK_ROUTE_INVALID",
            "MARKET_TICK_ROUTE_CLIENT_UNAVAILABLE",
            "MARKET_TICK_INSTRUMENT_ROUTE_MISMATCH",
            "MARKET_TICK_CAPABILITY_UNAVAILABLE",
            "MARKET_TICK_ENTRY_POINT_CONFLICT",
            "MARKET_TICK_ENTRY_POINT_INVALID",
            "MARKET_TICK_ENTRY_POINT_IMPORT_FAILED",
            "MARKET_TICK_REGISTRATION_FAILED",
            "MARKET_TICK_REGISTRATION_SCOPE_INVALID",
            "MARKET_TICK_PROVIDER_REGISTRY_INVALID",
            "MARKET_TICK_PROVIDER_CAPABILITY_CONFLICT",
            "MARKET_TICK_PROVIDER_CAPABILITY_ALREADY_REGISTERED",
            "MARKET_TICK_PROVIDER_INITIALIZATION_FAILED",
            "MARKET_TICK_SOURCE_BINDING_INVALID",
            "MARKET_TICK_SOURCE_INSTRUMENT_MISMATCH",
            "MARKET_TICK_SOURCE_CHANNEL_UNSUPPORTED",
            "MARKET_TICK_SOURCE_ADAPTER_INVALID",
            "MARKET_TICK_SERVICE_RUNTIME_UNAVAILABLE",
            "MARKET_TICK_RUNTIME_FACTORY_INVALID",
            "MARKET_TICK_RUNTIME_OWNER_THREAD_MISMATCH",
            "MARKET_TICK_RUNTIME_BINDING_UNVERIFIED",
            "MARKET_TICK_SERVICE_CLIENT_INVALID",
            "MARKET_TICK_SERVICE_CLIENT_KEY_MISMATCH",
            "MARKET_TICK_SERVICE_CLIENT_CLOSE_UNCONFIRMED",
            "MARKET_TICK_PROJECTOR_INVALID",
            "MARKET_TICK_PROVIDER_DISCOVERY_FAILED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in self._CODES
            else "MARKET_TICK_ROUTE_INVALID"
        )
        super().__init__(self.code)


@runtime_checkable
class MarketTickRuntimeFactory(Protocol):
    """Trusted host-owned factory for one owner-thread service market client.

    The host factory supplies source and entitlement references from explicit
    trusted configuration, asks the provider adapter to construct the neutral
    subscription key, and binds the resulting client to this exact SDK route.
    It owns the runtime and must create the client on ``owner_thread_id``. The
    projector owns and closes the returned client; the host still closes the
    runtime after every borrowed market client has been closed.
    """

    @property
    def owner_thread_id(self) -> int:
        """Thread on which the runtime and all bound market operations belong."""

    def create_market_client(
        self,
        route: SelectedMarketTickRoute,
        binding: MarketTickSourceBinding,
        /,
    ) -> object:
        """Create one exact-key ``ServiceMarketClient`` on the owner thread."""

    def validate_market_client_binding(
        self,
        route: SelectedMarketTickRoute,
        binding: MarketTickSourceBinding,
        client: object,
        /,
    ) -> bool:
        """Verify route, adapter, key, runtime, and client owner-thread identity."""


class MarketTickProviderRegistry(MarketTickProviderRegistryPort):
    """Immutable exact-route registry for provider-owned neutral adapters."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, MarketTickProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: MarketTickProviderFactory) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketTickCompositionError("MARKET_TICK_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketTickCompositionError(
                    "MARKET_TICK_PROVIDER_CAPABILITY_ALREADY_REGISTERED"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> MarketTickProviderFactory:
        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise MarketTickCompositionError("MARKET_TICK_CAPABILITY_UNAVAILABLE")
        return factory

    def create_source_binding(self, route: SelectedMarketTickRoute) -> MarketTickSourceBinding:
        if type(route) is not SelectedMarketTickRoute:
            raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")
        factory = self.resolve(route.exchange_name)
        try:
            binding = factory(route)
        except MarketTickCompositionError:
            raise
        except (MarketTickProviderRouteError, MarketTickSourceBindingError) as error:
            raise _provider_contract_error(error) from None
        except Exception:
            raise MarketTickCompositionError(
                "MARKET_TICK_PROVIDER_INITIALIZATION_FAILED"
            ) from None
        _validate_source_binding(binding, route)
        return binding

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: MarketTickProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketTickCompositionError("MARKET_TICK_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketTickCompositionError(
                    "MARKET_TICK_PROVIDER_CAPABILITY_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class MarketTickProviderRouter:
    """Load only the provider entry point for the already selected route."""

    def __init__(self, registry: MarketTickProviderRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = threading.RLock()

    def create_source_binding(self, route: SelectedMarketTickRoute) -> MarketTickSourceBinding:
        if type(route) is not SelectedMarketTickRoute:
            raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")
        self._load_selected_provider(route.exchange_name)
        return self._registry.create_source_binding(route)

    def _load_selected_provider(self, exchange_name: str) -> None:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise MarketTickCompositionError("MARKET_TICK_ENTRY_POINT_CONFLICT")
        if not entry_points:
            if self._registry.has_capability(exchange_name):
                return
            raise MarketTickCompositionError("MARKET_TICK_CAPABILITY_UNAVAILABLE")

        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_capability(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise MarketTickCompositionError("MARKET_TICK_PROVIDER_CAPABILITY_CONFLICT")
            try:
                registrar = entry_point.load()
            except Exception:
                raise MarketTickCompositionError(
                    "MARKET_TICK_ENTRY_POINT_IMPORT_FAILED"
                ) from None
            if not callable(registrar):
                raise MarketTickCompositionError("MARKET_TICK_ENTRY_POINT_INVALID")
            isolated = MarketTickProviderRegistry()
            try:
                registrar(isolated)
            except MarketTickCompositionError as error:
                raise MarketTickCompositionError(error.code) from None
            except Exception:
                raise MarketTickCompositionError("MARKET_TICK_REGISTRATION_FAILED") from None
            if isolated._registered_routes() != (exchange_name,):
                raise MarketTickCompositionError("MARKET_TICK_REGISTRATION_SCOPE_INVALID")
            factory = isolated.resolve(exchange_name)
            try:
                self._registry._register_entry_point_factory(exchange_name, factory, source)
            except MarketTickCompositionError:
                raise MarketTickCompositionError(
                    "MARKET_TICK_PROVIDER_CAPABILITY_CONFLICT"
                ) from None


_DEFAULT_REGISTRY = MarketTickProviderRegistry()


def register_market_tick_provider_factory(
    exchange_name: str,
    factory: MarketTickProviderFactory,
    *,
    registry: MarketTickProviderRegistry | None = None,
) -> None:
    """Register one provider-owned neutral source adapter factory."""

    _select_registry(registry).register(exchange_name, factory)


def create_market_tick_projector(
    route: SelectedMarketTickRoute,
    *,
    runtime_factory: MarketTickRuntimeFactory | None,
    registry: MarketTickProviderRegistry | None = None,
) -> object:
    """Create the exact public tick projector from a trusted host runtime.

    The provider registry returns only a neutral adapter binding. The explicit
    host factory owns configured source/entitlement references and creates an
    exact ``ServiceMarketClient`` on the same owner thread that will call its
    projector. The projector owns the market client lifecycle, not the runtime.
    No queue is shared with ``BtApi.poll_events`` and there is no direct-feed
    fallback.
    """

    if type(route) is not SelectedMarketTickRoute:
        raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")
    if runtime_factory is None:
        raise MarketTickCompositionError("MARKET_TICK_SERVICE_RUNTIME_UNAVAILABLE")
    if not isinstance(runtime_factory, MarketTickRuntimeFactory):
        raise MarketTickCompositionError("MARKET_TICK_RUNTIME_FACTORY_INVALID")
    try:
        owner_thread_id = runtime_factory.owner_thread_id
    except Exception:
        raise MarketTickCompositionError("MARKET_TICK_RUNTIME_FACTORY_INVALID") from None
    if type(owner_thread_id) is not int or owner_thread_id != threading.get_ident():
        raise MarketTickCompositionError("MARKET_TICK_RUNTIME_OWNER_THREAD_MISMATCH")

    router = _DEFAULT_ROUTER if registry is None else MarketTickProviderRouter(registry)
    binding = router.create_source_binding(route)
    try:
        client = runtime_factory.create_market_client(route, binding)
    except MarketTickCompositionError:
        raise
    except Exception:
        raise MarketTickCompositionError("MARKET_TICK_SERVICE_RUNTIME_UNAVAILABLE") from None

    try:
        from bt_api_py.service_market import ServiceMarketClient
        from bt_api_py.service_market_projection import ServiceMarketEventProjector
        from bt_api_py.service_market_tick_projection import ServiceMarketTickProjector
    except Exception:
        _close_unusable_client(client)
        raise MarketTickCompositionError("MARKET_TICK_PROJECTOR_INVALID") from None

    if type(client) is not ServiceMarketClient:
        raise MarketTickCompositionError("MARKET_TICK_SERVICE_CLIENT_INVALID")
    _validate_client_key(client, route)
    try:
        current_owner = runtime_factory.owner_thread_id
        binding_valid = runtime_factory.validate_market_client_binding(route, binding, client)
    except Exception:
        current_owner = None
        binding_valid = None
    if (
        type(current_owner) is not int
        or current_owner != threading.get_ident()
        or type(binding_valid) is not bool
        or binding_valid is not True
    ):
        _close_unusable_client(client)
        raise MarketTickCompositionError("MARKET_TICK_RUNTIME_BINDING_UNVERIFIED")

    try:
        event_projector = ServiceMarketEventProjector(client)
        projector = ServiceMarketTickProjector(event_projector)
    except Exception:
        _close_unusable_client(client)
        raise MarketTickCompositionError("MARKET_TICK_PROJECTOR_INVALID") from None
    if type(projector) is not ServiceMarketTickProjector:
        _close_unusable_client(client)
        raise MarketTickCompositionError("MARKET_TICK_PROJECTOR_INVALID")
    return projector


def default_market_tick_provider_registry() -> MarketTickProviderRegistry:
    return _DEFAULT_REGISTRY


def _validate_source_binding(
    binding: object,
    route: SelectedMarketTickRoute,
) -> None:
    if type(binding) is not MarketTickSourceBinding:
        raise MarketTickCompositionError("MARKET_TICK_SOURCE_BINDING_INVALID")
    if binding.instrument != route.instrument:
        raise MarketTickCompositionError("MARKET_TICK_SOURCE_INSTRUMENT_MISMATCH")
    if binding.channel != "tick":
        raise MarketTickCompositionError("MARKET_TICK_SOURCE_CHANNEL_UNSUPPORTED")


def _validate_client_key(client: object, route: SelectedMarketTickRoute) -> None:
    key = getattr(client, "key", None)
    if (
        type(key) is not MarketSubscriptionKey
        or key.instrument != route.instrument
        or key.channel != "tick"
        or key.depth is not None
        or key.timeframe is not None
    ):
        _close_unusable_client(client)
        raise MarketTickCompositionError("MARKET_TICK_SERVICE_CLIENT_KEY_MISMATCH")


def _close_unusable_client(client: object) -> None:
    """Make one same-thread close attempt on an unusable exact market client."""

    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        raise MarketTickCompositionError(
            "MARKET_TICK_SERVICE_CLIENT_CLOSE_UNCONFIRMED"
        ) from None


def _provider_contract_error(
    error: MarketTickProviderRouteError | MarketTickSourceBindingError,
) -> MarketTickCompositionError:
    code_map = {
        "MARKET_TICK_PROVIDER_ROUTE_INVALID": "MARKET_TICK_ROUTE_INVALID",
        "MARKET_TICK_PROVIDER_CLIENT_UNAVAILABLE": "MARKET_TICK_ROUTE_CLIENT_UNAVAILABLE",
        "MARKET_TICK_PROVIDER_ENVIRONMENT_INVALID": "MARKET_TICK_ROUTE_INVALID",
        "MARKET_TICK_SOURCE_BINDING_INVALID": "MARKET_TICK_SOURCE_BINDING_INVALID",
        "MARKET_TICK_SOURCE_INSTRUMENT_MISMATCH": "MARKET_TICK_SOURCE_INSTRUMENT_MISMATCH",
        "MARKET_TICK_SOURCE_CHANNEL_UNSUPPORTED": "MARKET_TICK_SOURCE_CHANNEL_UNSUPPORTED",
        "MARKET_TICK_SOURCE_ADAPTER_INVALID": "MARKET_TICK_SOURCE_ADAPTER_INVALID",
    }
    return MarketTickCompositionError(code_map.get(error.code, "MARKET_TICK_SOURCE_BINDING_INVALID"))


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=MARKET_TICK_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(MARKET_TICK_PROVIDER_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == MARKET_TICK_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise MarketTickCompositionError("MARKET_TICK_PROVIDER_DISCOVERY_FAILED") from None
    return tuple(
        entry_point
        for entry_point in candidates
        if getattr(entry_point, "name", None) == exchange_name
    )


def _entry_point_identity(entry_point: object, exchange_name: str) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(entry_point, "dist", None)
    distribution_name = getattr(distribution, "name", "") if distribution is not None else ""
    if (
        type(value) is not str
        or not value
        or type(distribution_name) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise MarketTickCompositionError("MARKET_TICK_ENTRY_POINT_INVALID")
    return (exchange_name, value, distribution_name)


def _select_registry(
    registry: MarketTickProviderRegistry | None,
) -> MarketTickProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not MarketTickProviderRegistry:
        raise MarketTickCompositionError("MARKET_TICK_PROVIDER_REGISTRY_INVALID")
    return registry


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", errors="strict")) > 256
    ):
        raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")


# Built after every helper it calls (``_select_registry``) is defined.
_DEFAULT_ROUTER = MarketTickProviderRouter(_DEFAULT_REGISTRY)


__all__ = [
    "MARKET_TICK_PROVIDER_ENTRY_POINT_GROUP",
    "MarketTickCompositionError",
    "MarketTickProviderFactory",
    "MarketTickProviderRegistry",
    "MarketTickProviderRouter",
    "MarketTickRuntimeFactory",
    "SelectedMarketTickRoute",
    "create_market_tick_projector",
    "default_market_tick_provider_registry",
    "register_market_tick_provider_factory",
]
