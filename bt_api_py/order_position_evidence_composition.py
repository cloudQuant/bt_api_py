"""Exact-route SDK composition for source-issued order position evidence.

The provider owns the query and its opaque receipt. This module selects one
provider for the already selected SDK route, validates the exact returned DTO,
and retains object identity through later currentness checks. It never reads,
constructs, or accepts position quantities from a Backtrader caller.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import metadata as importlib_metadata
import json
from threading import RLock
from typing import Callable

from bt_api_base.execution_correlation_protocol import SelectedExecutionRoute
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.order_position_evidence_protocol import (
    OrderPositionEvidence,
    OrderPositionEvidenceFacadePort,
    OrderPositionEvidenceProviderFactory,
    OrderPositionEvidenceProviderPort,
    OrderPositionEvidenceProviderRegistryPort,
    OrderPositionEvidenceUnavailable,
    ProviderQueryFacadePort,
    OrderPositionQuantities,
)

__all__ = (
    "ORDER_POSITION_EVIDENCE_PROVIDER_ENTRY_POINT_GROUP",
    "OrderPositionEvidenceProviderRegistry",
    "OrderPositionEvidenceProviderRouter",
    "OrderPositionEvidenceRouteResolver",
    "default_order_position_evidence_provider_registry",
    "register_order_position_evidence_provider_factory",
)


ORDER_POSITION_EVIDENCE_PROVIDER_ENTRY_POINT_GROUP = (
    "bt_api.order_position_evidence_providers"
)
OrderPositionEvidenceRouteResolver = Callable[[str], SelectedExecutionRoute | None]
_UTC_ZERO = timedelta(0)
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_ISSUED_EVIDENCE = 128


class OrderPositionEvidenceProviderRegistry(OrderPositionEvidenceProviderRegistryPort):
    """Process registry for exact-route provider factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, OrderPositionEvidenceProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        factory: OrderPositionEvidenceProviderFactory,
        /,
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
        with self._lock:
            if exchange_name in self._factories:
                raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: OrderPositionEvidenceProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
        with self._lock:
            if exchange_name in self._factories:
                raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> OrderPositionEvidenceProviderFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = OrderPositionEvidenceProviderRegistry()


def default_order_position_evidence_provider_registry() -> OrderPositionEvidenceProviderRegistry:
    """Return the process registry; no provider is installed by default."""

    return _DEFAULT_REGISTRY


def register_order_position_evidence_provider_factory(
    exchange_name: str,
    factory: OrderPositionEvidenceProviderFactory,
    *,
    registry: OrderPositionEvidenceProviderRegistry | None = None,
) -> None:
    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not OrderPositionEvidenceProviderRegistry:
        raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
    selected.register(exchange_name, factory)


class OrderPositionEvidenceProviderRouter:
    """Per-BtApi exact-route provider cache and opaque evidence facade."""

    def __init__(
        self,
        route_resolver: OrderPositionEvidenceRouteResolver,
        *,
        registry: OrderPositionEvidenceProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_ROUTE_UNAVAILABLE")
        selected = _DEFAULT_REGISTRY if registry is None else registry
        if type(selected) is not OrderPositionEvidenceProviderRegistry:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE")
        self._route_resolver = route_resolver
        self._registry = selected
        self._lock = RLock()
        self._providers: dict[
            str,
            tuple[SelectedExecutionRoute, OrderPositionEvidenceProviderPort | None],
        ] = {}
        self._facades: dict[
            str,
            tuple[
                SelectedExecutionRoute,
                OrderPositionEvidenceProviderPort | None,
                _RouteBoundOrderPositionEvidenceFacade,
            ],
        ] = {}

    def order_position_evidence(
        self,
        exchange_name: str,
    ) -> OrderPositionEvidenceFacadePort:
        _require_exchange_name(exchange_name)
        route = self._resolve_route(exchange_name)
        if route is None:
            return _UnavailableOrderPositionEvidenceFacade(
                "POSITION_EVIDENCE_ROUTE_UNAVAILABLE"
            )
        provider = self._provider_for_route(route)
        if provider is None:
            return _UnavailableOrderPositionEvidenceFacade(
                "POSITION_EVIDENCE_PROVIDER_UNAVAILABLE"
            )
        with self._lock:
            cached = self._facades.get(exchange_name)
            if (
                cached is not None
                and _same_route(cached[0], route)
                and cached[1] is provider
            ):
                return cached[2]
            facade = _RouteBoundOrderPositionEvidenceFacade(
                route=route,
                route_resolver=self._route_resolver,
                provider_resolver=self._provider_for_route,
                provider=provider,
            )
            self._facades[exchange_name] = (route, provider, facade)
            return facade

    def _resolve_route(self, exchange_name: str) -> SelectedExecutionRoute | None:
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            return None
        if type(route) is not SelectedExecutionRoute or route.exchange_name != exchange_name:
            return None
        return route

    def _provider_for_route(
        self,
        route: SelectedExecutionRoute,
    ) -> OrderPositionEvidenceProviderPort | None:
        with self._lock:
            cached = self._providers.get(route.exchange_name)
            if cached is not None and _same_route(cached[0], route):
                return cached[1]
            if not self._load_selected_provider(route.exchange_name):
                self._providers[route.exchange_name] = (route, None)
                return None
            factory = self._registry._resolve(route.exchange_name)
            if factory is None:
                self._providers[route.exchange_name] = (route, None)
                return None
            try:
                query_facade = self._query_facade_for_route(route)
                provider = factory(route, query_facade)
            except Exception:
                provider = None
            if not _is_provider_port(provider):
                provider = None
            self._providers[route.exchange_name] = (route, provider)
            return provider

    @staticmethod
    def _query_facade_for_route(route: SelectedExecutionRoute) -> ProviderQueryFacadePort:
        """Compose a query facade for the exact same selected feed/environment."""

        from .query_composition import (
            SelectedQueryRoute,
            create_query_facade,
        )
        from .query_facade import QueryFacade

        query_route = SelectedQueryRoute(
            exchange_name=route.exchange_name,
            client=route.client,
            environment=route.environment,
        )
        facade = create_query_facade(query_route)
        if type(facade) is not QueryFacade:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SOURCE_UNAVAILABLE")
        if (
            query_route.exchange_name != route.exchange_name
            or query_route.client is not route.client
            or query_route.environment != route.environment
        ):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SOURCE_UNAVAILABLE")
        return facade

    def _load_selected_provider(self, exchange_name: str) -> bool:
        entry_points = _route_entry_points(exchange_name)
        if entry_points is None:
            return False
        if len(entry_points) > 1:
            return False
        if not entry_points:
            return self._registry._resolve(exchange_name) is not None
        entry_point = entry_points[0]
        try:
            source = _entry_point_identity(entry_point, exchange_name)
        except ValueError:
            return False
        registered_source = self._registry._entry_point_source(exchange_name)
        if self._registry._resolve(exchange_name) is not None:
            return registered_source == source
        try:
            registrar = entry_point.load()
        except Exception:
            return False
        if not callable(registrar):
            return False
        isolated = OrderPositionEvidenceProviderRegistry()
        try:
            registrar(isolated)
        except Exception:
            return False
        if isolated._registered_routes() != (exchange_name,):
            return False
        factory = isolated._resolve(exchange_name)
        if factory is None:
            return False
        try:
            self._registry._register_entry_point_factory(exchange_name, factory, source)
        except OrderPositionEvidenceUnavailable:
            return self._registry._entry_point_source(exchange_name) == source
        return True


class _UnavailableOrderPositionEvidenceFacade:
    __slots__ = ("__code",)

    def __init__(self, code: str) -> None:
        self.__code = code

    def snapshot(
        self,
        instrument: MarketInstrumentKey,
        *,
        timeout: float = 5.0,
    ) -> OrderPositionEvidence:
        _check_timeout(timeout)
        if type(instrument) is not MarketInstrumentKey:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SCOPE_UNAVAILABLE")
        raise OrderPositionEvidenceUnavailable(self.__code)

    def validate(self, evidence: OrderPositionEvidence, *, now: datetime) -> bool:
        return False

    def __repr__(self) -> str:
        return "<unavailable SDK order-position-evidence facade>"


class _RouteBoundOrderPositionEvidenceFacade:
    __slots__ = (
        "__route",
        "__route_resolver",
        "__provider_resolver",
        "__provider",
        "__issued",
        "__lock",
    )

    def __init__(
        self,
        *,
        route: SelectedExecutionRoute,
        route_resolver: OrderPositionEvidenceRouteResolver,
        provider_resolver: Callable[
            [SelectedExecutionRoute], OrderPositionEvidenceProviderPort | None
        ],
        provider: OrderPositionEvidenceProviderPort,
    ) -> None:
        self.__route = route
        self.__route_resolver = route_resolver
        self.__provider_resolver = provider_resolver
        self.__provider = provider
        self.__issued: OrderedDict[
            int,
            tuple[OrderPositionEvidence, MarketInstrumentKey, str],
        ] = OrderedDict()
        self.__lock = RLock()

    def snapshot(
        self,
        instrument: MarketInstrumentKey,
        *,
        timeout: float = 5.0,
    ) -> OrderPositionEvidence:
        if type(instrument) is not MarketInstrumentKey:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SCOPE_UNAVAILABLE")
        timeout = _check_timeout(timeout)
        if not self.__require_current_route():
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_ROUTE_UNAVAILABLE")
        try:
            evidence = self.__provider.snapshot(instrument, timeout=timeout)
        except OrderPositionEvidenceUnavailable:
            raise
        except Exception:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_PROVIDER_UNAVAILABLE") from None
        if type(evidence) is not OrderPositionEvidence:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SOURCE_UNAVAILABLE")
        try:
            OrderPositionEvidence.__post_init__(evidence)
            quantities = evidence.quantities
            if (
                type(quantities) is not OrderPositionQuantities
                or quantities.instrument != instrument
                or instrument.environment != self.__route.environment
            ):
                raise ValueError("evidence scope mismatch")
            digest = _evidence_digest(evidence)
        except Exception:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_SOURCE_UNAVAILABLE") from None

        trusted_now = _utc_now()
        if not _evidence_is_current(evidence, trusted_now):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_LEASE_NOT_CURRENT")
        try:
            valid = self.__provider.validate(evidence, now=trusted_now)
        except Exception:
            valid = False
        if type(valid) is not bool or valid is not True:
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_LEASE_NOT_CURRENT")
        if not self.__require_current_route():
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_ROUTE_UNAVAILABLE")
        if not _evidence_is_current(evidence, _utc_now()):
            raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_LEASE_NOT_CURRENT")

        with self.__lock:
            self.__issued[id(evidence)] = (evidence, instrument, digest)
            self.__issued.move_to_end(id(evidence))
            while len(self.__issued) > _MAX_ISSUED_EVIDENCE:
                self.__issued.popitem(last=False)
        return evidence

    def validate(self, evidence: OrderPositionEvidence, *, now: datetime) -> bool:
        if type(evidence) is not OrderPositionEvidence or not _is_utc_datetime(now):
            return False
        with self.__lock:
            issued = self.__issued.get(id(evidence))
        if issued is None or issued[0] is not evidence:
            return False
        try:
            OrderPositionEvidence.__post_init__(evidence)
            if evidence.quantities.instrument != issued[1]:
                return False
            if _evidence_digest(evidence) != issued[2]:
                return False
        except Exception:
            return False
        if not self.__require_current_route():
            return False
        trusted_now = _utc_now()
        if not _evidence_is_current(evidence, trusted_now):
            return False
        try:
            valid = self.__provider.validate(evidence, now=trusted_now)
        except Exception:
            return False
        return (
            type(valid) is bool
            and valid is True
            and self.__require_current_route()
            and _evidence_is_current(evidence, _utc_now())
        )

    def __require_current_route(self) -> bool:
        try:
            current = self.__route_resolver(self.__route.exchange_name)
        except Exception:
            return False
        if not _same_route(current, self.__route):
            return False
        try:
            current_provider = self.__provider_resolver(current)
        except Exception:
            return False
        return current_provider is self.__provider

    def __repr__(self) -> str:
        return "<route-bound SDK order-position-evidence facade>"


def _is_provider_port(value: object) -> bool:
    try:
        return (
            value is not None
            and callable(getattr(value, "snapshot", None))
            and callable(getattr(value, "validate", None))
        )
    except Exception:
        return False


def _route_entry_points(exchange_name: str) -> tuple[object, ...] | None:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=ORDER_POSITION_EVIDENCE_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(ORDER_POSITION_EVIDENCE_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry
                for entry in discovered
                if getattr(entry, "group", None)
                == ORDER_POSITION_EVIDENCE_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        return None
    return tuple(entry for entry in candidates if getattr(entry, "name", None) == exchange_name)


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
        raise ValueError("invalid provider entrypoint")
    return (exchange_name, value, distribution_name)


def _require_exchange_name(value: object) -> None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_ROUTE_UNAVAILABLE")


def _check_timeout(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_TIMEOUT")
    timeout = float(value)
    if not 0.01 <= timeout <= _MAX_TIMEOUT_SECONDS:
        raise OrderPositionEvidenceUnavailable("POSITION_EVIDENCE_TIMEOUT")
    return timeout


def _is_utc_datetime(value: object) -> bool:
    if type(value) is not datetime or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() == _UTC_ZERO
    except Exception:
        return False


def _evidence_is_current(evidence: OrderPositionEvidence, now: datetime) -> bool:
    return (
        _is_utc_datetime(now)
        and evidence.observed_at_utc <= now < evidence.expires_at_utc
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _same_route(current: object, expected: SelectedExecutionRoute) -> bool:
    return (
        type(current) is SelectedExecutionRoute
        and current.exchange_name == expected.exchange_name
        and current.client is expected.client
        and current.environment == expected.environment
        and current.execution_context is expected.execution_context
    )


def _evidence_digest(evidence: OrderPositionEvidence) -> str:
    """Pin every public field so frozen-object mutation cannot alter a lease."""

    OrderPositionEvidence.__post_init__(evidence)
    quantities = evidence.quantities
    OrderPositionQuantities.__post_init__(quantities)
    instrument = quantities.instrument
    body = {
        "instrument": {
            "provider": instrument.provider,
            "venue": instrument.venue,
            "environment": instrument.environment,
            "product_type": instrument.product_type,
            "instrument_id": instrument.instrument_id,
            "native_symbol": instrument.native_symbol,
        },
        "quantities": {
            "qty_unit": quantities.qty_unit,
            "net_position_size": quantities.net_position_size,
            "long_position_size": quantities.long_position_size,
            "short_position_size": quantities.short_position_size,
            "long_close_today_size": quantities.long_close_today_size,
            "long_close_yesterday_size": quantities.long_close_yesterday_size,
            "short_close_today_size": quantities.short_close_today_size,
            "short_close_yesterday_size": quantities.short_close_yesterday_size,
        },
        "evidence": {
            "evidence_ref": evidence.evidence_ref,
            "issuer_ref": evidence.issuer_ref,
            "source_ref": evidence.source_ref,
            "session_generation": evidence.session_generation,
            "receipt_ref": evidence.receipt_ref,
            "snapshot_ref": evidence.snapshot_ref,
            "lease_ref": evidence.lease_ref,
            "records_digest": evidence.records_digest,
            "observed_at_utc": evidence.observed_at_utc.isoformat(),
            "expires_at_utc": evidence.expires_at_utc.isoformat(),
            "snapshot_record_count": evidence.snapshot_record_count,
            "instrument_record_count": evidence.instrument_record_count,
            "complete": evidence.complete,
            "scope_complete": evidence.scope_complete,
            "empty_result_proven": evidence.empty_result_proven,
        },
    }
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("ascii")).hexdigest()
