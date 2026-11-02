"""Exact-route composition for provider-neutral market-session resolution.

Provider packages register one resolver factory for an exact SDK route. The
SDK selects the route and instrument before invoking that factory. This module
does not import a venue package, create a calendar, infer an instrument from an
event string, or treat route selection as source-clock or freshness evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from threading import RLock
from typing import Callable, Optional

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.futures_market_protocol import FuturesProductKey
from bt_api_base.market_session_protocol import (
    MarketSessionCalendar,
    MarketSessionProviderFactory,
    MarketSessionProviderRegistryPort,
    MarketSessionResolution,
    MarketSessionResolutionStatus,
    MarketSessionResolver,
    MarketSessionSegmentKind,
    MarketSessionWindow,
    SelectedMarketSessionRoute,
)
from bt_api_base.market_tick_protocol import MarketTickEvent


__all__ = [
    "MARKET_SESSION_PROVIDER_ENTRY_POINT_GROUP",
    "MarketSessionCompositionError",
    "MarketSessionProviderRegistry",
    "MarketSessionRouteResolver",
    "MarketSessionRouter",
    "SelectedMarketSessionRoute",
    "default_market_session_provider_registry",
    "register_market_session_provider_factory",
]


MARKET_SESSION_PROVIDER_ENTRY_POINT_GROUP = "bt_api.market_session_providers"
MarketSessionRouteResolver = Callable[
    [str, MarketInstrumentKey], Optional[SelectedMarketSessionRoute]
]


class MarketSessionCompositionError(RuntimeError):
    """Finite, value-free route or provider composition failure."""

    def __init__(self, code: str) -> None:
        self.code = _finite_code(code, "MARKET_SESSION_PROVIDER_UNAVAILABLE")
        super().__init__(self.code)


class MarketSessionProviderRegistry(MarketSessionProviderRegistryPort):
    """Immutable exact-route registry shared with provider registrar entry points."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, MarketSessionProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: MarketSessionProviderFactory) -> None:
        """Register one provider-owned factory for an exact SDK route key."""

        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise MarketSessionCompositionError(
                    "MARKET_SESSION_PROVIDER_ROUTE_ALREADY_REGISTERED"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> MarketSessionProviderFactory:
        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_UNAVAILABLE")
        return factory

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: MarketSessionProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_FACTORY_INVALID")
        with self._lock:
            existing = self._factories.get(exchange_name)
            if existing is not None:
                if self._entry_point_sources.get(exchange_name) == source:
                    return
                raise MarketSessionCompositionError(
                    "MARKET_SESSION_PROVIDER_CAPABILITY_CONFLICT"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class _UnavailableMarketSessionResolver:
    """Neutral finite result for a selected route without this capability."""

    __slots__ = ("__exchange_name", "__instrument", "__reason")

    def __init__(self, exchange_name: str, instrument: MarketInstrumentKey, reason: str) -> None:
        self.__exchange_name = exchange_name
        self.__instrument = instrument
        self.__reason = _finite_code(reason, "MARKET_SESSION_PROVIDER_UNAVAILABLE")

    def resolve(
        self,
        event: MarketTickEvent,
        calendar: MarketSessionCalendar,
    ) -> MarketSessionResolution:
        _validate_resolution_input(event, calendar, self.__instrument)
        return _unavailable_resolution(event, calendar, self.__reason)

    def __repr__(self) -> str:
        return "<unavailable market-session resolver>"


class _RouteBoundMarketSessionResolver:
    """Hide the provider port and reject a changed selected route at use time."""

    __slots__ = ("__route", "__route_resolver", "__delegate", "__failure_code")

    def __init__(
        self,
        route: SelectedMarketSessionRoute,
        route_resolver: MarketSessionRouteResolver,
        delegate: MarketSessionResolver | None,
        failure_code: str | None = None,
    ) -> None:
        self.__route = route
        self.__route_resolver = route_resolver
        self.__delegate = delegate
        self.__failure_code = failure_code

    def resolve(
        self,
        event: MarketTickEvent,
        calendar: MarketSessionCalendar,
    ) -> MarketSessionResolution:
        _validate_resolution_input(event, calendar, self.__route.instrument)
        try:
            current = self.__route_resolver(
                self.__route.exchange_name,
                self.__route.instrument,
            )
        except Exception:
            return _unavailable_resolution(
                event,
                calendar,
                "MARKET_SESSION_ROUTE_RESOLUTION_FAILED",
            )
        if not _valid_route(
            current,
            self.__route.exchange_name,
            self.__route.instrument,
        ) or not _same_route(current, self.__route):
            return _unavailable_resolution(
                event,
                calendar,
                "MARKET_SESSION_ROUTE_CHANGED",
            )
        if self.__failure_code is not None or self.__delegate is None:
            return _unavailable_resolution(
                event,
                calendar,
                self.__failure_code or "MARKET_SESSION_PROVIDER_UNAVAILABLE",
            )

        try:
            result = self.__delegate.resolve(event, calendar)
        except Exception:
            return _unavailable_resolution(
                event,
                calendar,
                "MARKET_SESSION_PROVIDER_RESOLUTION_FAILED",
            )
        if not _valid_resolution_binding(result, event, calendar, self.__route.instrument):
            return _unavailable_resolution(
                event,
                calendar,
                "MARKET_SESSION_PROVIDER_PROJECTION_INVALID",
            )
        return result

    def __repr__(self) -> str:
        return "<route-bound market-session resolver>"


class MarketSessionRouter:
    """Resolve one exact route/instrument provider and cache its neutral port.

    ``route_resolver`` is SDK-owned and returns only the already-selected
    ``SelectedMarketSessionRoute`` for the requested exchange/instrument. A
    provider factory is loaded only for that exact exchange-name entry point.
    Returned wrappers recheck route/client identity on each resolution and do
    not expose the provider implementation.
    """

    def __init__(
        self,
        route_resolver: MarketSessionRouteResolver,
        *,
        registry: MarketSessionProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise MarketSessionCompositionError("MARKET_SESSION_ROUTE_RESOLVER_INVALID")
        self._route_resolver = route_resolver
        self._registry = _select_registry(registry)
        self._lock = RLock()
        self._resolvers: dict[
            tuple[str, MarketInstrumentKey],
            tuple[SelectedMarketSessionRoute, _RouteBoundMarketSessionResolver],
        ] = {}

    def market_session_resolver(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> MarketSessionResolver:
        """Select the exact route-bound neutral resolver for an instrument."""

        _require_exchange_name(exchange_name)
        if type(instrument) is not MarketInstrumentKey:
            raise MarketSessionCompositionError("MARKET_SESSION_INSTRUMENT_INVALID")
        key = (exchange_name, instrument)
        try:
            route = self._route_resolver(exchange_name, instrument)
        except Exception:
            route = None
        if not _valid_route(route, exchange_name, instrument):
            return _UnavailableMarketSessionResolver(
                exchange_name,
                instrument,
                "MARKET_SESSION_ROUTE_UNAVAILABLE",
            )

        with self._lock:
            cached = self._resolvers.get(key)
            if cached is not None and _same_route(cached[0], route):
                return cached[1]
            try:
                factory = self._load_selected_factory(exchange_name)
                resolver = factory(route)
                if not isinstance(resolver, MarketSessionResolver):
                    raise MarketSessionCompositionError(
                        "MARKET_SESSION_PROVIDER_PORT_INCOMPATIBLE"
                    )
                selected = _RouteBoundMarketSessionResolver(
                    route,
                    self._route_resolver,
                    resolver,
                )
            except MarketSessionCompositionError as error:
                selected = _RouteBoundMarketSessionResolver(
                    route,
                    self._route_resolver,
                    None,
                    _finite_code(error.code, "MARKET_SESSION_PROVIDER_UNAVAILABLE"),
                )
            except Exception:
                selected = _RouteBoundMarketSessionResolver(
                    route,
                    self._route_resolver,
                    None,
                    "MARKET_SESSION_PROVIDER_INITIALIZATION_FAILED",
                )
            self._resolvers[key] = (route, selected)
            return selected

    def _load_selected_factory(self, exchange_name: str) -> MarketSessionProviderFactory:
        candidates = _route_entry_points(exchange_name)
        if len(candidates) > 1:
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_ENTRY_POINT_CONFLICT")
        if not candidates:
            if self._registry.has_capability(exchange_name):
                return self._registry.resolve(exchange_name)
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_UNAVAILABLE")

        entry_point = candidates[0]
        source = _entry_point_identity(entry_point, exchange_name)
        if self._registry.has_capability(exchange_name):
            if self._registry._entry_point_source(exchange_name) == source:
                return self._registry.resolve(exchange_name)
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_CAPABILITY_CONFLICT")
        try:
            registrar = entry_point.load()
        except Exception:
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_IMPORT_FAILED") from None
        if not callable(registrar):
            raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_ENTRY_POINT_INVALID")
        isolated = MarketSessionProviderRegistry()
        try:
            registrar(isolated)
        except Exception:
            raise MarketSessionCompositionError(
                "MARKET_SESSION_PROVIDER_REGISTRATION_FAILED"
            ) from None
        if isolated._registered_routes() != (exchange_name,):
            raise MarketSessionCompositionError(
                "MARKET_SESSION_PROVIDER_REGISTRATION_SCOPE_INVALID"
            )
        factory = isolated.resolve(exchange_name)
        self._registry._register_entry_point_factory(exchange_name, factory, source)
        return self._registry.resolve(exchange_name)


_DEFAULT_REGISTRY = MarketSessionProviderRegistry()


def default_market_session_provider_registry() -> MarketSessionProviderRegistry:
    """Return the exact-route registry used by SDK provider initialization."""

    return _DEFAULT_REGISTRY


def register_market_session_provider_factory(
    exchange_name: str,
    factory: MarketSessionProviderFactory,
    *,
    registry: MarketSessionProviderRegistry | None = None,
) -> None:
    """Register one SDK provider's market-session resolver factory."""

    _select_registry(registry).register(exchange_name, factory)


def _select_registry(
    registry: MarketSessionProviderRegistry | None,
) -> MarketSessionProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not MarketSessionProviderRegistry:
        raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_REGISTRY_INVALID")
    return registry


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise MarketSessionCompositionError("MARKET_SESSION_ROUTE_INVALID")
    try:
        if len(value.encode("utf-8", errors="strict")) > 256:
            raise MarketSessionCompositionError("MARKET_SESSION_ROUTE_INVALID")
    except UnicodeEncodeError:
        raise MarketSessionCompositionError("MARKET_SESSION_ROUTE_INVALID") from None


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=MARKET_SESSION_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(MARKET_SESSION_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                item
                for item in discovered
                if getattr(item, "group", None)
                == MARKET_SESSION_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_DISCOVERY_FAILED") from None
    try:
        return tuple(
            item for item in candidates if getattr(item, "name", None) == exchange_name
        )
    except Exception:
        raise MarketSessionCompositionError(
            "MARKET_SESSION_PROVIDER_DISCOVERY_FAILED"
        ) from None


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
        raise MarketSessionCompositionError("MARKET_SESSION_PROVIDER_ENTRY_POINT_INVALID")
    return (exchange_name, value, distribution_name)


def _valid_route(
    route: object,
    exchange_name: str,
    instrument: MarketInstrumentKey,
) -> bool:
    if type(route) is not SelectedMarketSessionRoute:
        return False
    if route.exchange_name != exchange_name or route.instrument != instrument:
        return False
    try:
        SelectedMarketSessionRoute.__post_init__(route)
    except Exception:
        return False
    return True


def _same_route(
    left: object,
    right: SelectedMarketSessionRoute,
) -> bool:
    return (
        type(left) is SelectedMarketSessionRoute
        and left.exchange_name == right.exchange_name
        and left.instrument == right.instrument
        and left.environment == right.environment
        and left.client is right.client
    )


def _validate_resolution_input(
    event: MarketTickEvent,
    calendar: MarketSessionCalendar,
    instrument: MarketInstrumentKey,
) -> None:
    if type(event) is not MarketTickEvent or type(calendar) is not MarketSessionCalendar:
        raise MarketSessionCompositionError("MARKET_SESSION_INPUT_INVALID")
    if (
        event.data.instrument != instrument
        or calendar.instrument != instrument
        or event.source.key.instrument != instrument
    ):
        raise MarketSessionCompositionError("MARKET_SESSION_INSTRUMENT_MISMATCH")


def _valid_resolution_binding(
    result: object,
    event: MarketTickEvent,
    calendar: MarketSessionCalendar,
    instrument: MarketInstrumentKey,
) -> bool:
    if type(result) is not MarketSessionResolution:
        return False
    try:
        MarketSessionResolution.__post_init__(result)
    except Exception:
        return False
    if not (
        result.instrument == instrument
        and result.product == calendar.product
        and result.calendar_ref == calendar.calendar_ref
        and result.calendar_digest == calendar.calendar_digest
        and result.event_time_utc == _event_time_utc(event)
        and result.source_clock_quality is event.clock.source_clock_quality
        and result.receive_clock_quality is event.clock.receive_clock_quality
        and result.freshness_status is event.clock.freshness_status
    ):
        return False
    if result.status is MarketSessionResolutionStatus.COMPLETE:
        if result.trading_date not in calendar.trading_dates:
            return False
        if result.segment_kind is MarketSessionSegmentKind.CONTINUOUS:
            event_time = result.event_time_utc
            if event_time is None or not any(
                _window_matches_resolution(window, result)
                for window in calendar.windows
            ):
                return False
        elif result.segment_kind is MarketSessionSegmentKind.OUTSIDE:
            if not calendar.complete or result.event_time_utc is None:
                return False
            if any(
                window.trading_date == result.trading_date
                and window.start_at_utc <= result.event_time_utc < window.end_at_utc
                for window in calendar.windows
            ):
                return False
        elif result.segment_kind is MarketSessionSegmentKind.OPENING_AUCTION_BASELINE:
            if result.event_time_utc is not None and any(
                window.trading_date == result.trading_date
                and window.start_at_utc <= result.event_time_utc < window.end_at_utc
                for window in calendar.windows
            ):
                return False
    return True


def _window_matches_resolution(
    window: MarketSessionWindow,
    result: MarketSessionResolution,
) -> bool:
    return (
        window.trading_date == result.trading_date
        and window.session_date == result.session_date
        and result.event_session_date in window.event_session_dates
        and window.session_id == result.session_id
        and result.event_time_utc is not None
        and window.start_at_utc <= result.event_time_utc < window.end_at_utc
    )


def _event_time_utc(event: MarketTickEvent) -> datetime | None:
    observed = event.clock.provider_event_time_utc
    if observed is None:
        return None
    try:
        return datetime.fromisoformat(observed.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except Exception:
        return None


def _unavailable_resolution(
    event: MarketTickEvent,
    calendar: MarketSessionCalendar,
    reason: str,
) -> MarketSessionResolution:
    return MarketSessionResolution(
        status=MarketSessionResolutionStatus.UNAVAILABLE,
        instrument=calendar.instrument,
        product=calendar.product,
        trading_date=None,
        session_date=None,
        event_session_date=None,
        event_time_utc=_event_time_utc(event),
        session_id=None,
        segment_kind=MarketSessionSegmentKind.UNKNOWN,
        calendar_ref=calendar.calendar_ref,
        calendar_digest=calendar.calendar_digest,
        source_clock_quality=event.clock.source_clock_quality,
        receive_clock_quality=event.clock.receive_clock_quality,
        freshness_status=event.clock.freshness_status,
        blockers=(_finite_code(reason, "MARKET_SESSION_PROVIDER_UNAVAILABLE"),),
    )


def _finite_code(value: object, fallback: str) -> str:
    if (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    ):
        return value
    return fallback
