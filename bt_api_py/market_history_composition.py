"""Exact-route composition and bounded collection for market history."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
import re
from threading import RLock

from bt_api_base.market_history_provider_protocol import (
    MarketHistoryProviderBinding,
    MarketHistoryProviderContractError,
    MarketHistoryProviderFactory,
    MarketHistoryProviderRegistryPort,
    SelectedMarketHistoryRoute,
)
from bt_api_base.market_history_protocol import MarketBar, MarketHistoryPage, MarketHistoryRequest

from .service_market_history import MarketHistoryCollectionError, collect_market_history


MARKET_HISTORY_PROVIDER_ENTRY_POINT_GROUP = "bt_api.market_history_providers"
_COLLECTION_ERROR_CODES = frozenset(
    {
        "CLOCK_INVALID",
        "HISTORY_LIMIT_INVALID",
        "HISTORY_PAGE_LIMIT",
        "HISTORY_ROW_LIMIT",
        "HISTORY_PAGE_INVALID",
        "HISTORY_BAR_INVALID",
        "HISTORY_ORDER_INVALID",
        "HISTORY_OVERLAP",
        "HISTORY_CURSOR_INVALID",
        "HISTORY_WINDOW_INVALID",
        "HISTORY_WINDOW_UNALIGNED",
        "HISTORY_SINCE_UNSUPPORTED",
        "HISTORY_BAR_CONFLICT",
        "HISTORY_DUPLICATE_BAR",
        "MARKET_BAR_TIMEFRAME_MISMATCH",
        "MARKET_BAR_ALIGNMENT_INVALID",
        "MARKET_BAR_FUTURE",
        "MARKET_BAR_NOT_CLOSED_AT_SENT",
        "MARKET_KEY_INVALID",
        "MARKET_CLIENT_INVALID",
        "MARKET_CLIENT_KEY_CHANGED",
    }
)


class MarketHistoryCompositionError(RuntimeError):
    """Finite, value-free route, capability, provider, and collection failure."""

    _CODES = frozenset(
        {
            "MARKET_HISTORY_ROUTE_INVALID",
            "MARKET_HISTORY_ROUTE_UNAVAILABLE",
            "MARKET_HISTORY_ROUTE_CHANGED",
            "MARKET_HISTORY_TIMEFRAME_UNAVAILABLE",
            "MARKET_HISTORY_RAW_FIELDS_UNSUPPORTED",
            "MARKET_HISTORY_PROVIDER_CAPABILITY_UNAVAILABLE",
            "MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT",
            "MARKET_HISTORY_PROVIDER_ENTRY_POINT_INVALID",
            "MARKET_HISTORY_PROVIDER_IMPORT_FAILED",
            "MARKET_HISTORY_PROVIDER_REGISTRATION_FAILED",
            "MARKET_HISTORY_PROVIDER_REGISTRATION_SCOPE_INVALID",
            "MARKET_HISTORY_PROVIDER_DISCOVERY_FAILED",
            "MARKET_HISTORY_PROVIDER_INITIALIZATION_FAILED",
            "MARKET_HISTORY_PROVIDER_BINDING_INVALID",
            "MARKET_HISTORY_PROVIDER_FAILED",
            *_COLLECTION_ERROR_CODES,
            "MARKET_HISTORY_PROVIDER_ROUTE_INVALID",
            "MARKET_HISTORY_PROVIDER_TIMEFRAME_UNAVAILABLE",
            "MARKET_HISTORY_PROVIDER_ADAPTER_INVALID",
            "MARKET_HISTORY_PROVIDER_POLICY_INVALID",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in self._CODES
            else "MARKET_HISTORY_ROUTE_INVALID"
        )
        super().__init__(self.code)


class MarketHistoryProviderRegistry(MarketHistoryProviderRegistryPort):
    """Exact route registry populated by provider-owned entrypoints."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, MarketHistoryProviderFactory] = {}
        self._sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: MarketHistoryProviderFactory, /) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = None

    def has_provider(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> MarketHistoryProviderFactory:
        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise MarketHistoryCompositionError(
                "MARKET_HISTORY_PROVIDER_CAPABILITY_UNAVAILABLE"
            )
        return factory

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: MarketHistoryProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class MarketHistoryProviderRouter:
    """Discover and invoke only the exact provider selected for one route."""

    def __init__(self, registry: MarketHistoryProviderRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = RLock()

    def create_binding(
        self,
        route: SelectedMarketHistoryRoute,
    ) -> MarketHistoryProviderBinding:
        if type(route) is not SelectedMarketHistoryRoute:
            raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")
        self._load_selected_provider(route.exchange_name)
        if not self._registry.has_provider(route.exchange_name):
            raise MarketHistoryCompositionError(
                "MARKET_HISTORY_PROVIDER_CAPABILITY_UNAVAILABLE"
            )
        factory = self._registry.resolve(route.exchange_name)
        try:
            binding = factory(route)
        except MarketHistoryProviderContractError as error:
            raise MarketHistoryCompositionError(error.code) from None
        except Exception:
            raise MarketHistoryCompositionError(
                "MARKET_HISTORY_PROVIDER_INITIALIZATION_FAILED"
            ) from None
        if (
            type(binding) is not MarketHistoryProviderBinding
            or binding.key.instrument != route.instrument
            or binding.key.timeframe != route.timeframe
            or binding.key.channel != "bar"
        ):
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_BINDING_INVALID")
        return binding

    def _load_selected_provider(self, exchange_name: str) -> None:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise MarketHistoryCompositionError(
                "MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT"
            )
        if not entry_points:
            return
        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_provider(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT"
                )
            try:
                registrar = entry_point.load()
            except Exception:
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_IMPORT_FAILED"
                ) from None
            if not callable(registrar):
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_ENTRY_POINT_INVALID"
                )
            isolated = MarketHistoryProviderRegistry()
            try:
                registrar(isolated)
            except MarketHistoryCompositionError as error:
                raise MarketHistoryCompositionError(error.code) from None
            except Exception:
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_REGISTRATION_FAILED"
                ) from None
            if isolated._registered_routes() != (exchange_name,):
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_REGISTRATION_SCOPE_INVALID"
                )
            factory = isolated.resolve(exchange_name)
            try:
                self._registry._register_entry_point_factory(exchange_name, factory, source)
            except MarketHistoryCompositionError:
                raise MarketHistoryCompositionError(
                    "MARKET_HISTORY_PROVIDER_CAPABILITY_CONFLICT"
                ) from None


class _BoundHistoryClient:
    """Adapt the exact provider binding to the shared page collector."""

    def __init__(self, binding: MarketHistoryProviderBinding) -> None:
        self.key = binding.key
        self._adapter = binding.adapter
        self._timeout_seconds = float(binding.request_timeout_seconds)

    def history(self, request: MarketHistoryRequest) -> MarketHistoryPage:
        if type(request) is not MarketHistoryRequest or request.key is not self.key:
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_BINDING_INVALID")
        try:
            page = self._adapter.fetch(request, self._timeout_seconds)
        except Exception:
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_FAILED") from None
        if type(page) is not MarketHistoryPage or page.request is not request:
            raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_FAILED")
        return page


def register_market_history_provider_factory(
    exchange_name: str,
    factory: MarketHistoryProviderFactory,
    *,
    registry: MarketHistoryProviderRegistry | None = None,
) -> None:
    """Register one provider-owned capability for one exact exchange route."""

    _select_registry(registry).register(exchange_name, factory)


def default_market_history_provider_registry() -> MarketHistoryProviderRegistry:
    return _DEFAULT_REGISTRY


def default_market_history_provider_router() -> MarketHistoryProviderRouter:
    return _DEFAULT_ROUTER


def market_history_collection_invalid(bars: object) -> bool:
    """Check the neutral MarketBar collection shape without asserting provenance."""
    return type(bars) is not tuple or any(type(bar) is not MarketBar for bar in bars)


def fetch_market_history(
    api: object,
    exchange_name: str,
    instrument: object,
    timeframe: str | None,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    preserve_raw_bars: bool = False,
) -> tuple[MarketBar, ...]:
    """Collect complete typed bars for the API's exact selected provider route."""

    from bt_api_base.market_protocol import MarketInstrumentKey

    if type(preserve_raw_bars) is not bool:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")
    if preserve_raw_bars:
        raise MarketHistoryCompositionError("MARKET_HISTORY_RAW_FIELDS_UNSUPPORTED")
    if (
        type(exchange_name) is not str
        or not exchange_name
        or type(instrument) is not MarketInstrumentKey
    ):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")
    if type(timeframe) is not str or re.fullmatch(r"[1-9][0-9]{0,11}[smhdw]", timeframe) is None:
        raise MarketHistoryCompositionError("MARKET_HISTORY_TIMEFRAME_UNAVAILABLE")

    selected = getattr(api, "_selected_provider_route", None)
    if not callable(selected):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_UNAVAILABLE")
    try:
        selected_route = selected(exchange_name)
    except Exception:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_UNAVAILABLE") from None
    if type(selected_route) is not tuple or len(selected_route) != 2:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_UNAVAILABLE")
    feed, environment = selected_route
    if not _selected_client_is_current(api, exchange_name, feed):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_CHANGED")
    if (
        environment is not None
        and instrument.environment != environment
    ):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")

    try:
        route = SelectedMarketHistoryRoute(
            exchange_name=exchange_name,
            client=feed,
            instrument=instrument,
            timeframe=timeframe,
            environment=environment,
        )
        binding = _DEFAULT_ROUTER.create_binding(route)
    except MarketHistoryCompositionError:
        raise
    except MarketHistoryProviderContractError as error:
        raise MarketHistoryCompositionError(error.code) from None
    except Exception:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID") from None

    page_client = _BoundHistoryClient(binding)
    try:
        bars = collect_market_history(
            page_client,
            history_start_utc=None,
            history_end_utc=None,
            since=since,
            now_utc=datetime.now(timezone.utc),
            limit=limit,
            default_limit=binding.default_limit,
            page_size=binding.page_size,
            max_pages=binding.max_pages,
            max_rows=binding.max_rows,
        )
    except MarketHistoryCollectionError as error:
        raise MarketHistoryCompositionError(error.code) from None
    except MarketHistoryCompositionError:
        raise
    except Exception:
        raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_FAILED") from None

    try:
        selected_after = selected(exchange_name)
    except Exception:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_CHANGED") from None
    if (
        type(selected_after) is not tuple
        or len(selected_after) != 2
        or selected_after[0] is not feed
        or selected_after[1] != environment
        or not _selected_client_is_current(api, exchange_name, feed)
    ):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_CHANGED")
    if market_history_collection_invalid(bars):
        raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_FAILED")
    return bars


def _selected_client_is_current(api: object, exchange_name: str, feed: object) -> bool:
    lock = getattr(api, "_sdk_capability_lock", None)
    clients = getattr(api, "_selected_route_clients", None)
    feeds = getattr(api, "exchange_feeds", None)
    if lock is None or not callable(getattr(lock, "__enter__", None)):
        return False
    try:
        with lock:
            return (
                isinstance(clients, Mapping)
                and clients.get(exchange_name) is feed
                and isinstance(feeds, Mapping)
                and feeds.get(exchange_name) is feed
            )
    except Exception:
        return False


_DEFAULT_REGISTRY = MarketHistoryProviderRegistry()


def _select_registry(
    registry: MarketHistoryProviderRegistry | None,
) -> MarketHistoryProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not MarketHistoryProviderRegistry:
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")
    return registry


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", errors="strict")) > 256
    ):
        raise MarketHistoryCompositionError("MARKET_HISTORY_ROUTE_INVALID")


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=MARKET_HISTORY_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(MARKET_HISTORY_PROVIDER_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None) == MARKET_HISTORY_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise MarketHistoryCompositionError(
            "MARKET_HISTORY_PROVIDER_DISCOVERY_FAILED"
        ) from None
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
        raise MarketHistoryCompositionError("MARKET_HISTORY_PROVIDER_ENTRY_POINT_INVALID")
    return exchange_name, value, distribution_name


# Built after every helper it calls (``_select_registry``) is defined.
_DEFAULT_ROUTER = MarketHistoryProviderRouter(_DEFAULT_REGISTRY)


__all__ = [
    "MARKET_HISTORY_PROVIDER_ENTRY_POINT_GROUP",
    "MarketHistoryCompositionError",
    "MarketHistoryProviderRegistry",
    "MarketHistoryProviderRouter",
    "default_market_history_provider_registry",
    "default_market_history_provider_router",
    "fetch_market_history",
    "market_history_collection_invalid",
    "register_market_history_provider_factory",
]
