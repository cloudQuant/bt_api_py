"""Exact-route composition for provider-owned expected futures calendars.

The SDK selects one configured route before loading a provider package. The
router validates the exact request/result types and retains result identity;
it does not own exchange rules or upgrade expected rules into observed market
facts.
"""

from __future__ import annotations

from datetime import date
from importlib import metadata as importlib_metadata
from threading import RLock
from typing import Callable
from weakref import WeakValueDictionary

from bt_api_base.futures_expected_calendar_protocol import (
    FuturesExpectedCalendarPort,
    FuturesExpectedCalendarProviderFactory,
    FuturesExpectedCalendarProviderRegistryPort,
    FuturesExpectedCalendarRequest,
    FuturesExpectedCalendarSnapshot,
    SelectedFuturesExpectedCalendarRoute,
)


__all__ = [
    "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_GROUP",
    "FuturesExpectedCalendarCompositionError",
    "FuturesExpectedCalendarProviderRegistry",
    "FuturesExpectedCalendarProviderRouter",
    "FuturesExpectedCalendarRouteResolver",
    "default_futures_expected_calendar_provider_registry",
    "register_futures_expected_calendar_provider_factory",
]


FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_GROUP = (
    "bt_api.futures_expected_calendar_providers"
)
FuturesExpectedCalendarRouteResolver = Callable[
    [str], SelectedFuturesExpectedCalendarRoute | None
]


class FuturesExpectedCalendarCompositionError(RuntimeError):
    """Finite route/provider failure without leaking route or client values."""

    _CODES = frozenset(
        {
            "FUTURES_EXPECTED_CALENDAR_ROUTE_INVALID",
            "FUTURES_EXPECTED_CALENDAR_ROUTE_UNAVAILABLE",
            "FUTURES_EXPECTED_CALENDAR_ROUTE_CHANGED",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_REGISTRY_INVALID",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_FACTORY_INVALID",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_CONFLICT",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_INVALID",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_DISCOVERY_FAILED",
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_INITIALIZATION_FAILED",
            "FUTURES_EXPECTED_CALENDAR_REQUEST_INVALID",
            "FUTURES_EXPECTED_CALENDAR_RESULT_INVALID",
            "FUTURES_EXPECTED_CALENDAR_RESULT_NOT_ISSUED_HERE",
            "FUTURES_EXPECTED_CALENDAR_YEAR_UNSUPPORTED",
            "FUTURES_EXPECTED_VENUE_UNSUPPORTED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in self._CODES
            else "FUTURES_EXPECTED_CALENDAR_ROUTE_INVALID"
        )
        super().__init__(self.code)


class FuturesExpectedCalendarProviderRegistry(
    FuturesExpectedCalendarProviderRegistryPort
):
    """Process registry for exact-route expected-calendar provider factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, FuturesExpectedCalendarProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        factory: FuturesExpectedCalendarProviderFactory,
    ) -> None:
        _require_route_name(exchange_name)
        if not callable(factory):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_FACTORY_INVALID"
            )
        with self._lock:
            if exchange_name in self._factories:
                raise FuturesExpectedCalendarCompositionError(
                    "FUTURES_EXPECTED_CALENDAR_PROVIDER_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_route_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> FuturesExpectedCalendarProviderFactory:
        _require_route_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE"
            )
        return factory

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: FuturesExpectedCalendarProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_route_name(exchange_name)
        if not callable(factory):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_FACTORY_INVALID"
            )
        with self._lock:
            existing = self._factories.get(exchange_name)
            if existing is not None:
                if self._entry_point_sources.get(exchange_name) == source:
                    return
                raise FuturesExpectedCalendarCompositionError(
                    "FUTURES_EXPECTED_CALENDAR_PROVIDER_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = FuturesExpectedCalendarProviderRegistry()


def default_futures_expected_calendar_provider_registry() -> (
    FuturesExpectedCalendarProviderRegistry
):
    """Return the SDK process registry without loading provider packages."""

    return _DEFAULT_REGISTRY


def register_futures_expected_calendar_provider_factory(
    exchange_name: str,
    factory: FuturesExpectedCalendarProviderFactory,
    *,
    registry: FuturesExpectedCalendarProviderRegistry | None = None,
) -> None:
    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not FuturesExpectedCalendarProviderRegistry:
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_REGISTRY_INVALID"
        )
    selected.register(exchange_name, factory)


class _RouteBoundExpectedCalendarPort:
    """Hide the provider and reject route changes or caller-built result DTOs."""

    def __init__(
        self,
        route: SelectedFuturesExpectedCalendarRoute,
        route_resolver: FuturesExpectedCalendarRouteResolver,
        delegate: FuturesExpectedCalendarPort,
    ) -> None:
        self._route = route
        self._route_resolver = route_resolver
        self._delegate = delegate
        self._issued: WeakValueDictionary[int, FuturesExpectedCalendarSnapshot] = (
            WeakValueDictionary()
        )
        self._lock = RLock()

    def build(
        self,
        request: FuturesExpectedCalendarRequest,
    ) -> FuturesExpectedCalendarSnapshot:
        if type(request) is not FuturesExpectedCalendarRequest:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_REQUEST_INVALID"
            )
        self._require_current_route()
        try:
            snapshot = self._delegate.build(request)
        except Exception:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_RESULT_INVALID"
            ) from None
        if (
            type(snapshot) is not FuturesExpectedCalendarSnapshot
            or snapshot.request is not request
            or self._delegate.validate(snapshot) is not True
        ):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_RESULT_INVALID"
            )
        self._require_current_route()
        with self._lock:
            self._issued[id(snapshot)] = snapshot
        return snapshot

    def expected_trading_days(self, venue: str, year: int):
        self._require_current_route()
        try:
            result = self._delegate.expected_trading_days(venue, year)
        except Exception as error:
            code = getattr(error, "code", None)
            if type(code) is str and code in FuturesExpectedCalendarCompositionError._CODES:
                raise FuturesExpectedCalendarCompositionError(code) from None
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE"
            ) from None
        if (
            type(result) is not tuple
            or any(type(item) is not date for item in result)
            or tuple(sorted(set(result))) != result
        ):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_RESULT_INVALID"
            )
        self._require_current_route()
        return result

    def validate(self, snapshot: FuturesExpectedCalendarSnapshot) -> bool:
        if type(snapshot) is not FuturesExpectedCalendarSnapshot:
            return False
        with self._lock:
            if self._issued.get(id(snapshot)) is not snapshot:
                return False
        try:
            self._require_current_route()
            return self._delegate.validate(snapshot) is True
        except FuturesExpectedCalendarCompositionError:
            return False

    def _require_current_route(self) -> None:
        try:
            current = self._route_resolver(self._route.exchange_name)
        except Exception:
            current = None
        if not _same_route(current, self._route):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_ROUTE_CHANGED"
            )


class FuturesExpectedCalendarProviderRouter:
    """Select and cache one expected-calendar provider for an exact SDK route."""

    def __init__(
        self,
        route_resolver: FuturesExpectedCalendarRouteResolver,
        *,
        registry: FuturesExpectedCalendarProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_ROUTE_INVALID"
            )
        selected = _DEFAULT_REGISTRY if registry is None else registry
        if type(selected) is not FuturesExpectedCalendarProviderRegistry:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_REGISTRY_INVALID"
            )
        self._route_resolver = route_resolver
        self._registry = selected
        self._lock = RLock()
        self._ports: dict[
            str,
            tuple[SelectedFuturesExpectedCalendarRoute, _RouteBoundExpectedCalendarPort],
        ] = {}

    def expected_calendar(self, exchange_name: str) -> FuturesExpectedCalendarPort:
        _require_route_name(exchange_name)
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            route = None
        if not _valid_route(route, exchange_name):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_ROUTE_UNAVAILABLE"
            )
        with self._lock:
            cached = self._ports.get(exchange_name)
            if cached is not None and _same_route(cached[0], route):
                return cached[1]
            try:
                factory = self._load_factory(exchange_name)
                delegate = factory(route)
                if not isinstance(delegate, FuturesExpectedCalendarPort):
                    raise FuturesExpectedCalendarCompositionError(
                        "FUTURES_EXPECTED_CALENDAR_PROVIDER_FACTORY_INVALID"
                    )
                port = _RouteBoundExpectedCalendarPort(route, self._route_resolver, delegate)
            except FuturesExpectedCalendarCompositionError:
                raise
            except Exception:
                raise FuturesExpectedCalendarCompositionError(
                    "FUTURES_EXPECTED_CALENDAR_PROVIDER_INITIALIZATION_FAILED"
                ) from None
            self._ports[exchange_name] = (route, port)
            return port

    def _load_factory(self, exchange_name: str) -> FuturesExpectedCalendarProviderFactory:
        candidates = tuple(
            item for item in _entry_points() if getattr(item, "name", None) == exchange_name
        )
        if len(candidates) > 1:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_CONFLICT"
            )
        if not candidates:
            if self._registry.has_capability(exchange_name):
                return self._registry.resolve(exchange_name)
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE"
            )
        entry = candidates[0]
        source = _entry_point_identity(entry, exchange_name)
        if self._registry.has_capability(exchange_name):
            if self._registry._entry_point_source(exchange_name) == source:
                return self._registry.resolve(exchange_name)
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_CONFLICT"
            )
        try:
            registrar = entry.load()
        except Exception:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_DISCOVERY_FAILED"
            ) from None
        if not callable(registrar):
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_INVALID"
            )
        isolated = FuturesExpectedCalendarProviderRegistry()
        try:
            registrar(isolated)
            factory = isolated.resolve(exchange_name)
        except Exception:
            raise FuturesExpectedCalendarCompositionError(
                "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_INVALID"
            ) from None
        self._registry._register_entry_point_factory(exchange_name, factory, source)
        return factory


def _require_route_name(value: object) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_ROUTE_INVALID"
        )


def _valid_route(
    route: object,
    exchange_name: str,
) -> bool:
    return (
        type(route) is SelectedFuturesExpectedCalendarRoute
        and route.exchange_name == exchange_name
    )


def _same_route(
    left: object,
    right: SelectedFuturesExpectedCalendarRoute,
) -> bool:
    return (
        _valid_route(left, right.exchange_name)
        and left.client is right.client
        and left.environment == right.environment
    )


def _entry_points() -> tuple[object, ...]:
    try:
        values = importlib_metadata.entry_points()
        if hasattr(values, "select"):
            return tuple(values.select(group=FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_GROUP))
        return tuple(
            values.get(FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_GROUP, ())
        )
    except Exception:
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_DISCOVERY_FAILED"
        ) from None


def _entry_point_identity(entry: object, exchange_name: str) -> tuple[str, str, str]:
    name = getattr(entry, "name", None)
    value = getattr(entry, "value", None)
    group = getattr(entry, "group", None)
    if name != exchange_name or group != FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_GROUP:
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_INVALID"
        )
    if type(value) is not str or not value or value != value.strip():
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_ENTRY_POINT_INVALID"
        )
    return group, name, value
