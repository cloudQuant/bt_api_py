"""Exact-route registry and lazy provider composition for market streams."""

from __future__ import annotations

from importlib import metadata as importlib_metadata
import threading

from bt_api_base.market_stream_provider_protocol import (
    MarketStreamChannelCapability,
    MarketStreamProviderBinding,
    MarketStreamProviderContractError,
    MarketStreamProviderFactory,
    MarketStreamProviderRegistryPort,
    SelectedMarketStreamRoute,
)
from bt_api_base.market_stream_protocol import MarketStreamRequest


MARKET_STREAM_PROVIDER_ENTRY_POINT_GROUP = "bt_api.market_stream_providers"


class MarketStreamCompositionError(RuntimeError):
    """Finite, value-free market stream route and capability failures."""

    _CODES = frozenset(
        {
            "MARKET_STREAM_ROUTE_INVALID",
            "MARKET_STREAM_ROUTE_UNAVAILABLE",
            "MARKET_STREAM_ROUTE_CHANGED",
            "MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT",
            "MARKET_STREAM_PROVIDER_ENTRY_POINT_INVALID",
            "MARKET_STREAM_PROVIDER_ENTRY_POINT_IMPORT_FAILED",
            "MARKET_STREAM_PROVIDER_REGISTRATION_FAILED",
            "MARKET_STREAM_PROVIDER_REGISTRATION_SCOPE_INVALID",
            "MARKET_STREAM_PROVIDER_REGISTRY_INVALID",
            "MARKET_STREAM_PROVIDER_BINDING_INVALID",
            "MARKET_STREAM_PROVIDER_ADAPTER_INVALID",
            "MARKET_STREAM_PROVIDER_CAPABILITY_INVALID",
            "MARKET_STREAM_PROVIDER_INITIALIZATION_FAILED",
            "MARKET_STREAM_PROVIDER_DISCOVERY_FAILED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "MARKET_STREAM_ROUTE_INVALID"
        super().__init__(self.code)


class MarketStreamProviderRegistry(MarketStreamProviderRegistryPort):
    """Thread-safe route registry populated by one exact provider entrypoint."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, MarketStreamProviderFactory] = {}
        self._sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: MarketStreamProviderFactory, /) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = None

    def has_provider(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> MarketStreamProviderFactory:
        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE")
        return factory

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: MarketStreamProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class MarketStreamProviderRouter:
    """Load only the exact provider selected by a trusted SDK route."""

    def __init__(self, registry: MarketStreamProviderRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = threading.RLock()

    def create_binding(
        self,
        route: SelectedMarketStreamRoute,
    ) -> MarketStreamProviderBinding | None:
        if type(route) is not SelectedMarketStreamRoute:
            raise MarketStreamCompositionError("MARKET_STREAM_ROUTE_INVALID")
        self._load_selected_provider(route.exchange_name)
        if not self._registry.has_provider(route.exchange_name):
            return None
        factory = self._registry.resolve(route.exchange_name)
        try:
            binding = factory(route)
        except MarketStreamProviderContractError as error:
            raise _contract_error(error) from None
        except Exception:
            raise MarketStreamCompositionError(
                "MARKET_STREAM_PROVIDER_INITIALIZATION_FAILED"
            ) from None
        if (
            type(binding) is not MarketStreamProviderBinding
            or binding.instrument != route.instrument
        ):
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_BINDING_INVALID")
        try:
            capabilities = binding.adapter.capabilities()
        except Exception:
            raise MarketStreamCompositionError(
                "MARKET_STREAM_PROVIDER_INITIALIZATION_FAILED"
            ) from None
        if (
            type(capabilities) is not tuple
            or any(type(item) is not MarketStreamChannelCapability for item in capabilities)
            or len({item.channel for item in capabilities}) != len(capabilities)
        ):
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_CAPABILITY_INVALID")
        return binding

    def resolve_request(
        self,
        route: SelectedMarketStreamRoute,
        request: MarketStreamRequest,
    ) -> tuple[MarketStreamProviderBinding, MarketStreamChannelCapability | None] | None:
        if type(request) is not MarketStreamRequest or request.instrument != route.instrument:
            raise MarketStreamCompositionError("MARKET_STREAM_ROUTE_INVALID")
        binding = self.create_binding(route)
        if binding is None:
            return None
        try:
            capabilities = binding.adapter.capabilities()
        except Exception:
            raise MarketStreamCompositionError(
                "MARKET_STREAM_PROVIDER_INITIALIZATION_FAILED"
            ) from None
        for capability in capabilities:
            if capability.supports(request):
                return binding, capability
        return binding, None

    def _load_selected_provider(self, exchange_name: str) -> None:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT")
        if not entry_points:
            return
        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_provider(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT")
            try:
                registrar = entry_point.load()
            except Exception:
                raise MarketStreamCompositionError(
                    "MARKET_STREAM_PROVIDER_ENTRY_POINT_IMPORT_FAILED"
                ) from None
            if not callable(registrar):
                raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_INVALID")
            isolated = MarketStreamProviderRegistry()
            try:
                registrar(isolated)
            except MarketStreamCompositionError as error:
                raise MarketStreamCompositionError(error.code) from None
            except Exception:
                raise MarketStreamCompositionError(
                    "MARKET_STREAM_PROVIDER_REGISTRATION_FAILED"
                ) from None
            if isolated._registered_routes() != (exchange_name,):
                raise MarketStreamCompositionError(
                    "MARKET_STREAM_PROVIDER_REGISTRATION_SCOPE_INVALID"
                )
            factory = isolated.resolve(exchange_name)
            try:
                self._registry._register_entry_point_factory(
                    exchange_name,
                    factory,
                    source,
                )
            except MarketStreamCompositionError:
                raise MarketStreamCompositionError(
                    "MARKET_STREAM_PROVIDER_ENTRY_POINT_CONFLICT"
                ) from None


_DEFAULT_REGISTRY = MarketStreamProviderRegistry()


def register_market_stream_provider_factory(
    exchange_name: str,
    factory: MarketStreamProviderFactory,
    *,
    registry: MarketStreamProviderRegistry | None = None,
) -> None:
    _select_registry(registry).register(exchange_name, factory)


def default_market_stream_provider_registry() -> MarketStreamProviderRegistry:
    return _DEFAULT_REGISTRY


def default_market_stream_provider_router() -> MarketStreamProviderRouter:
    return _DEFAULT_ROUTER


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=MARKET_STREAM_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(MARKET_STREAM_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == MARKET_STREAM_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise MarketStreamCompositionError(
            "MARKET_STREAM_PROVIDER_DISCOVERY_FAILED"
        ) from None
    return tuple(
        entry_point
        for entry_point in candidates
        if getattr(entry_point, "name", None) == exchange_name
    )


def _entry_point_identity(
    entry_point: object,
    exchange_name: str,
) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(entry_point, "dist", None)
    distribution_name = getattr(distribution, "name", "") if distribution is not None else ""
    if (
        type(value) is not str
        or not value
        or type(distribution_name) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_ENTRY_POINT_INVALID")
    return exchange_name, value, distribution_name


def _contract_error(error: MarketStreamProviderContractError) -> MarketStreamCompositionError:
    codes = {
        "MARKET_STREAM_PROVIDER_ROUTE_INVALID": "MARKET_STREAM_ROUTE_INVALID",
        "MARKET_STREAM_PROVIDER_CHANNEL_INVALID": "MARKET_STREAM_PROVIDER_CAPABILITY_INVALID",
        "MARKET_STREAM_PROVIDER_CHANNEL_UNAVAILABLE": "MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE",
        "MARKET_STREAM_PROVIDER_ADAPTER_INVALID": "MARKET_STREAM_PROVIDER_ADAPTER_INVALID",
        "MARKET_STREAM_PROVIDER_SESSION_INVALID": "MARKET_STREAM_PROVIDER_ADAPTER_INVALID",
        "MARKET_STREAM_PROVIDER_BINDING_INVALID": "MARKET_STREAM_PROVIDER_BINDING_INVALID",
        "MARKET_STREAM_PROVIDER_EVENT_INVALID": "MARKET_STREAM_PROVIDER_BINDING_INVALID",
    }
    return MarketStreamCompositionError(codes.get(error.code, "MARKET_STREAM_ROUTE_INVALID"))


def _select_registry(
    registry: MarketStreamProviderRegistry | None,
) -> MarketStreamProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not MarketStreamProviderRegistry:
        raise MarketStreamCompositionError("MARKET_STREAM_PROVIDER_REGISTRY_INVALID")
    return registry


def _require_exchange_name(value: object) -> None:
    if not _valid_text(value):
        raise MarketStreamCompositionError("MARKET_STREAM_ROUTE_INVALID")


def _valid_text(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeEncodeError:
        return False


# Built after every helper it calls (``_select_registry``) is defined.
_DEFAULT_ROUTER = MarketStreamProviderRouter(_DEFAULT_REGISTRY)


__all__ = [
    "MARKET_STREAM_PROVIDER_ENTRY_POINT_GROUP",
    "MarketStreamCompositionError",
    "MarketStreamProviderRegistry",
    "MarketStreamProviderRouter",
    "default_market_stream_provider_registry",
    "default_market_stream_provider_router",
    "register_market_stream_provider_factory",
]
