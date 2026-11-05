"""Neutral SDK facade for explicitly requested futures EOD rankings.

Provider adapters own public-source downloads and exchange identity mapping.
This facade only binds a provider result to an exact market-catalog snapshot
and, when supplied, revalidates the same catalog lease before and after the
read. Construction and import do not perform network I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from threading import RLock

from bt_api_py.futures_contracts import (
    FuturesEodCatalogScope,
    FuturesEodRankingPort,
    FuturesEodRankingProviderFactory,
    FuturesEodRankingProviderRegistryPort,
    FuturesEodRankingRequest,
    FuturesEodRankingSnapshot,
    QuerySnapshotLease,
    SelectedFuturesEodRankingRoute,
)
from bt_api_py.futures_market_facade import (
    FuturesMarketSnapshot,
    FuturesMarketSnapshotFacade,
)


class FuturesEodRankingError(RuntimeError):
    """Finite failure at the public EOD ranking composition boundary."""

    _CODES = frozenset(
        {
            "request_invalid",
            "market_snapshot_invalid",
            "catalog_scope_mismatch",
            "catalog_lease_invalid",
            "provider_unavailable",
            "provider_collection_failed",
            "provider_snapshot_invalid",
            "catalog_lease_expired",
            "route_invalid",
            "provider_capability_unavailable",
            "provider_factory_invalid",
            "provider_entry_point_conflict",
            "provider_capability_conflict",
            "provider_import_failed",
            "provider_discovery_failed",
            "provider_entry_point_invalid",
            "provider_registration_failed",
            "provider_registration_scope_invalid",
            "provider_initialization_failed",
            "provider_port_incompatible",
            "provider_registry_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "provider_collection_failed"
        super().__init__(self.code)


class FuturesEodRankingFacade:
    """Bind one provider's EOD output to a neutral market-catalog snapshot.

    The provider is chosen by trusted SDK composition. The facade has no
    default or fallback provider. A supplied market lease is always checked
    on the same exact snapshot and facade before and after collection. Without
    a lease, incomplete catalog scope remains visible in the returned neutral
    coverage and cannot be promoted to complete by this facade.
    """

    def __init__(self, provider: FuturesEodRankingPort) -> None:
        if not isinstance(provider, FuturesEodRankingPort):
            raise FuturesEodRankingError("provider_unavailable")
        self._provider = provider

    def collect(
        self,
        request: FuturesEodRankingRequest,
        market_snapshot: FuturesMarketSnapshot,
        *,
        deadline: datetime,
        market_snapshot_facade: FuturesMarketSnapshotFacade | None = None,
        market_snapshot_lease: QuerySnapshotLease | None = None,
    ) -> FuturesEodRankingSnapshot:
        """Read the requested EOD sources and preserve exact catalog binding."""

        if type(request) is not FuturesEodRankingRequest:
            raise FuturesEodRankingError("request_invalid")
        if type(market_snapshot) is not FuturesMarketSnapshot:
            raise FuturesEodRankingError("market_snapshot_invalid")
        if type(deadline) is not datetime or deadline.tzinfo is None or deadline.utcoffset() is None:
            raise FuturesEodRankingError("request_invalid")
        if deadline.utcoffset().total_seconds() != 0:
            raise FuturesEodRankingError("request_invalid")
        if (market_snapshot_facade is None) != (market_snapshot_lease is None):
            raise FuturesEodRankingError("catalog_lease_invalid")
        if market_snapshot_facade is not None and (
            type(market_snapshot_facade) is not FuturesMarketSnapshotFacade
            or type(market_snapshot_lease) is not QuerySnapshotLease
        ):
            raise FuturesEodRankingError("catalog_lease_invalid")

        if not _request_in_market_scope(request, market_snapshot):
            raise FuturesEodRankingError("catalog_scope_mismatch")
        catalog = _catalog_scope(market_snapshot)

        if market_snapshot_facade is not None and not _lease_valid(
            market_snapshot_facade,
            market_snapshot,
            market_snapshot_lease,
        ):
            raise FuturesEodRankingError("catalog_lease_invalid")

        try:
            result = self._provider.collect(request, catalog, deadline=deadline)
        except Exception:
            raise FuturesEodRankingError("provider_collection_failed") from None
        if (
            type(result) is not FuturesEodRankingSnapshot
            or result.request != request
            or result.catalog != catalog
        ):
            raise FuturesEodRankingError("provider_snapshot_invalid")

        if market_snapshot_facade is not None and not _lease_valid(
            market_snapshot_facade,
            market_snapshot,
            market_snapshot_lease,
        ):
            raise FuturesEodRankingError("catalog_lease_expired")
        return result


FUTURES_EOD_RANKING_PROVIDER_ENTRY_POINT_GROUP = "bt_api.futures_eod_ranking_providers"


class FuturesEodRankingProviderRegistry(FuturesEodRankingProviderRegistryPort):
    """Exact route-to-provider factory registry for EOD ranking reads."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, FuturesEodRankingProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        factory: FuturesEodRankingProviderFactory,
        /,
    ) -> None:
        """Register one provider factory for an exact selected route."""

        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise FuturesEodRankingError("provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise FuturesEodRankingError("provider_capability_conflict")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> FuturesEodRankingProviderFactory:
        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise FuturesEodRankingError("provider_capability_unavailable")
        return factory

    def create_facade(self, route: SelectedFuturesEodRankingRoute) -> FuturesEodRankingFacade:
        if type(route) is not SelectedFuturesEodRankingRoute:
            raise FuturesEodRankingError("route_invalid")
        factory = self.resolve(route.exchange_name)
        try:
            provider = factory(route)
        except Exception:
            raise FuturesEodRankingError("provider_initialization_failed") from None
        try:
            return FuturesEodRankingFacade(provider)
        except Exception:
            raise FuturesEodRankingError("provider_port_incompatible") from None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: FuturesEodRankingProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise FuturesEodRankingError("provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise FuturesEodRankingError("provider_capability_conflict")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class FuturesEodRankingRouter:
    """Import and invoke only the entry point for the selected EOD route."""

    def __init__(self, registry: FuturesEodRankingProviderRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = RLock()

    def create_facade(self, route: SelectedFuturesEodRankingRoute) -> FuturesEodRankingFacade:
        if type(route) is not SelectedFuturesEodRankingRoute:
            raise FuturesEodRankingError("route_invalid")
        self._load_selected_provider(route.exchange_name)
        return self._registry.create_facade(route)

    def _load_selected_provider(self, exchange_name: str) -> None:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise FuturesEodRankingError("provider_entry_point_conflict")
        if not entry_points:
            if self._registry.has_capability(exchange_name):
                return
            raise FuturesEodRankingError("provider_capability_unavailable")

        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_capability(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise FuturesEodRankingError("provider_capability_conflict")

            try:
                registrar = entry_point.load()
            except Exception:
                raise FuturesEodRankingError("provider_import_failed") from None
            if not callable(registrar):
                raise FuturesEodRankingError("provider_entry_point_invalid")

            isolated = FuturesEodRankingProviderRegistry()
            try:
                registrar(isolated)
            except Exception:
                raise FuturesEodRankingError("provider_registration_failed") from None
            if isolated._registered_routes() != (exchange_name,):
                raise FuturesEodRankingError("provider_registration_scope_invalid")
            factory = isolated.resolve(exchange_name)
            try:
                self._registry._register_entry_point_factory(exchange_name, factory, source)
            except FuturesEodRankingError:
                raise FuturesEodRankingError("provider_capability_conflict") from None


_DEFAULT_REGISTRY = FuturesEodRankingProviderRegistry()


def register_futures_eod_ranking_provider(
    exchange_name: str,
    factory: FuturesEodRankingProviderFactory,
    *,
    registry: FuturesEodRankingProviderRegistry | None = None,
) -> None:
    """Register a provider factory in the SDK-selected capability registry."""

    _select_registry(registry).register(exchange_name, factory)


def create_futures_eod_ranking_facade(
    route: SelectedFuturesEodRankingRoute,
    *,
    registry: FuturesEodRankingProviderRegistry | None = None,
) -> FuturesEodRankingFacade:
    """Compose the exact provider selected by the trusted SDK route."""

    if type(route) is not SelectedFuturesEodRankingRoute:
        raise FuturesEodRankingError("route_invalid")
    if registry is None:
        return _DEFAULT_ROUTER.create_facade(route)
    return FuturesEodRankingRouter(registry).create_facade(route)


def default_futures_eod_ranking_provider_registry() -> FuturesEodRankingProviderRegistry:
    """Return the SDK process registry used by provider entry points."""

    return _DEFAULT_REGISTRY


def _lease_valid(
    facade: FuturesMarketSnapshotFacade,
    snapshot: FuturesMarketSnapshot,
    lease: QuerySnapshotLease,
) -> bool:
    try:
        return facade.validate_lease(snapshot, lease, now=datetime.now(timezone.utc)) is True
    except Exception:
        return False


def _request_in_market_scope(
    request: FuturesEodRankingRequest,
    snapshot: FuturesMarketSnapshot,
) -> bool:
    scope = snapshot.request
    target_keys = {(item.venue, item.product_id) for item in request.products}
    if scope.venues is not None and any(item.venue not in scope.venues for item in request.products):
        return False
    if scope.products is not None and not target_keys.issubset(
        {(item.venue, item.product_id) for item in scope.products}
    ):
        return False
    if snapshot.coverage.product_universe_complete is True and not target_keys.issubset(
        {(item.venue, item.product_id) for item in snapshot.products}
    ):
        return False
    if scope.instruments is not None:
        return False
    return True


def _catalog_scope(snapshot: FuturesMarketSnapshot) -> FuturesEodCatalogScope:
    coverage = snapshot.coverage
    catalog = coverage.catalog
    product_universe = coverage.product_universe
    return FuturesEodCatalogScope(
        market_snapshot_ref=snapshot.receipt.batch_ref,
        market_source_ref=snapshot.source_ref,
        market_generation=snapshot.generation,
        market_records_digest=snapshot.records_digest,
        environment=snapshot.request.environment,
        venues=snapshot.request.venues,
        catalog_products=snapshot.request.products,
        catalog_request_ref=catalog.request_ref,
        catalog_snapshot_ref=catalog.snapshot_ref,
        catalog_records_digest=catalog.records_digest,
        catalog_completeness=catalog.completeness,
        catalog_scope_complete=catalog.scope_complete,
        catalog_records=snapshot.catalog,
        product_universe_request_ref=(
            None if product_universe is None else product_universe.request_ref
        ),
        product_universe_snapshot_ref=(
            None if product_universe is None else product_universe.snapshot_ref
        ),
        product_universe_records_digest=(
            None if product_universe is None else product_universe.records_digest
        ),
        product_universe_complete=snapshot.coverage.product_universe_complete,
        product_universe=(
            snapshot.products
            if snapshot.coverage.product_universe_complete is True
            else None
        ),
    )


def _select_registry(
    registry: FuturesEodRankingProviderRegistry | None,
) -> FuturesEodRankingProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not FuturesEodRankingProviderRegistry:
        raise FuturesEodRankingError("provider_registry_invalid")
    return registry


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", errors="strict")) > 256
    ):
        raise FuturesEodRankingError("route_invalid")


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=FUTURES_EOD_RANKING_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(FUTURES_EOD_RANKING_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == FUTURES_EOD_RANKING_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise FuturesEodRankingError("provider_discovery_failed") from None
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
        or type(getattr(entry_point, "name", None)) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise FuturesEodRankingError("provider_entry_point_invalid")
    return (exchange_name, value, distribution_name)


# Built after every helper it calls (``_select_registry``) is defined.
_DEFAULT_ROUTER = FuturesEodRankingRouter(_DEFAULT_REGISTRY)


__all__ = [
    "FUTURES_EOD_RANKING_PROVIDER_ENTRY_POINT_GROUP",
    "FuturesEodRankingError",
    "FuturesEodRankingFacade",
    "FuturesEodRankingProviderRegistry",
    "FuturesEodRankingRouter",
    "SelectedFuturesEodRankingRoute",
    "create_futures_eod_ranking_facade",
    "default_futures_eod_ranking_provider_registry",
    "register_futures_eod_ranking_provider",
]
