"""Provider-neutral composition for the bounded SDK query facade.

Provider packages register a factory for a selected SDK route during their
normal initialization. The factory receives the SDK-selected route binding
and returns a public :class:`bt_api_base.query_protocol.ProviderQueryPort`.
This module knows no venue packages and never guesses a provider from client
shape, configuration values, or exchange-specific branches.

No query provider is registered by this module. Until a provider owner
registers its route capability, composition fails with an explicit unavailable
error; it does not fall back to direct client methods or another provider.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from importlib import metadata as importlib_metadata
from threading import RLock

from bt_api_base.account_session_protocol import AccountSessionSnapshot
from bt_api_base.query_provider_protocol import (
    QueryProviderFactory,
    QueryProviderRegistryPort,
    QueryProviderRouteError,
    SelectedQueryRoute,
)

from .query_facade import QueryFacade


class QueryCompositionError(RuntimeError):
    """A finite SDK query-capability selection or construction failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def select_query_facade(api, exchange_name, *, error_type, missing_dependency_error_type,
                        safe_exception_code):
    """Select one public SDK query facade without owning the caller's route state."""
    try:
        from bt_api_py.facade_protocol import QueryFacadeSelector
    except ImportError as exc:
        raise missing_dependency_error_type(
            "bt_api_py.facade_protocol.QueryFacadeSelector is required"
        ) from exc
    if not isinstance(api, QueryFacadeSelector):
        raise error_type("SDK facade does not expose the optional query selector")
    selector = getattr(api, "query_facade", None)
    if not callable(selector):
        raise error_type("SDK facade does not expose query_facade(exchange_name)")
    try:
        query = selector(exchange_name)
    except Exception as exc:
        if type(exc) is QueryCompositionError:
            raise
        code = safe_exception_code(exc, "sdk_query_facade_unavailable")
        raise error_type(f"SDK query facade is unavailable ({code})") from None
    if type(query) is not QueryFacade:
        raise error_type("SDK query_facade returned an invalid neutral facade")
    return query


QUERY_PROVIDER_ENTRY_POINT_GROUP = "bt_api.query_providers"


class QueryProviderRegistry(QueryProviderRegistryPort):
    """Exact route-to-provider-factory registry for query-port composition.

    Registrations are immutable for the lifetime of this registry. Duplicate
    registration fails rather than replacing an active provider capability.
    Call :meth:`create_facade` only with the SDK's already-selected route.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, QueryProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}
        self.__issued_facades: weakref.WeakKeyDictionary[
            QueryFacade, SelectedQueryRoute
        ] = weakref.WeakKeyDictionary()

    def register(self, exchange_name: str, factory: QueryProviderFactory) -> None:
        """Register one provider-owned query-port factory for an exact route."""

        self._register_factory(exchange_name, factory, entry_point_source=None)

    def has_capability(self, exchange_name: str) -> bool:
        """Return whether this exact route has a registered query factory."""

        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> QueryProviderFactory:
        """Return the exact registered factory or report capability absence."""

        _require_exchange_name(exchange_name)
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise QueryCompositionError("QUERY_PROVIDER_CAPABILITY_UNAVAILABLE")
        return factory

    def create_facade(
        self,
        route: SelectedQueryRoute,
        *,
        account_session_snapshot_validator: (
            Callable[[SelectedQueryRoute, AccountSessionSnapshot | None], bool] | None
        ) = None,
    ) -> QueryFacade:
        """Create a facade through the exact factory registered for ``route``."""

        if type(route) is not SelectedQueryRoute:
            raise QueryCompositionError("QUERY_PROVIDER_ROUTE_INVALID")
        factory = self.resolve(route.exchange_name)
        try:
            provider = factory(route)
        except Exception:
            raise QueryCompositionError("QUERY_PROVIDER_INITIALIZATION_FAILED") from None
        try:
            facade = QueryFacade(
                provider,
                selected_route=route,
                account_session_snapshot_validator=account_session_snapshot_validator,
                _composition_issuer=self if self is _DEFAULT_REGISTRY else None,
            )
        except Exception:
            raise QueryCompositionError("QUERY_PROVIDER_PORT_INCOMPATIBLE") from None
        if self is _DEFAULT_REGISTRY:
            with self._lock:
                self.__issued_facades[facade] = route
        return facade

    def _owns_facade(
        self,
        facade: QueryFacade,
        route: SelectedQueryRoute | None,
    ) -> bool:
        if type(facade) is not QueryFacade or type(route) is not SelectedQueryRoute:
            return False
        with self._lock:
            return self.__issued_facades.get(facade) is route

    def _register_factory(
        self,
        exchange_name: str,
        factory: QueryProviderFactory,
        *,
        entry_point_source: tuple[str, str, str] | None,
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise QueryCompositionError("QUERY_PROVIDER_FACTORY_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise QueryCompositionError("QUERY_PROVIDER_CAPABILITY_ALREADY_REGISTERED")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = entry_point_source

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: QueryProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        self._register_factory(exchange_name, factory, entry_point_source=source)

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class QueryRouter:
    """Load only the query-provider entry point for one selected route.

    Entry-point names are exact SDK ``exchange_name`` route keys in the
    ``bt_api.query_providers`` group. Metadata for the group is enumerated, but
    only the unique exact match is imported and invoked. Its registrar runs
    against an isolated registry; the router commits only the requested route
    after checking that the registrar did not register any other route.
    """

    def __init__(self, registry: QueryProviderRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = RLock()

    def create_facade(
        self,
        route: SelectedQueryRoute,
        *,
        account_session_snapshot_validator: (
            Callable[[SelectedQueryRoute, AccountSessionSnapshot | None], bool] | None
        ) = None,
    ) -> QueryFacade:
        """Load the selected provider capability, then create its facade."""

        if type(route) is not SelectedQueryRoute:
            raise QueryCompositionError("QUERY_PROVIDER_ROUTE_INVALID")
        self._load_selected_provider(route.exchange_name)
        return self._registry.create_facade(
            route,
            account_session_snapshot_validator=account_session_snapshot_validator,
        )

    def _load_selected_provider(self, exchange_name: str) -> None:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            raise QueryCompositionError("QUERY_PROVIDER_ENTRY_POINT_CONFLICT")
        if not entry_points:
            if self._registry.has_capability(exchange_name):
                return
            raise QueryCompositionError("QUERY_PROVIDER_CAPABILITY_UNAVAILABLE")

        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_capability(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise QueryCompositionError("QUERY_PROVIDER_CAPABILITY_CONFLICT")

            try:
                registrar = entry_point.load()
            except Exception:
                raise QueryCompositionError("QUERY_PROVIDER_IMPORT_FAILED") from None
            if not callable(registrar):
                raise QueryCompositionError("QUERY_PROVIDER_ENTRY_POINT_INVALID")

            isolated = QueryProviderRegistry()
            try:
                registrar(isolated)
            except QueryCompositionError as error:
                raise QueryCompositionError(
                    _provider_error_code(error, "QUERY_PROVIDER_REGISTRATION_FAILED")
                ) from None
            except Exception:
                raise QueryCompositionError("QUERY_PROVIDER_REGISTRATION_FAILED") from None

            if isolated._registered_routes() != (exchange_name,):
                raise QueryCompositionError("QUERY_PROVIDER_REGISTRATION_SCOPE_INVALID")
            factory = isolated.resolve(exchange_name)
            try:
                self._registry._register_entry_point_factory(
                    exchange_name,
                    factory,
                    source,
                )
            except QueryCompositionError:
                raise QueryCompositionError("QUERY_PROVIDER_CAPABILITY_CONFLICT") from None


_DEFAULT_REGISTRY = QueryProviderRegistry()


def register_query_provider_factory(
    exchange_name: str,
    factory: QueryProviderFactory,
    *,
    registry: QueryProviderRegistry | None = None,
) -> None:
    """Register an SDK provider-owned query capability for one exchange route.

    The provider owner supplies the factory. The parent SDK facade must not
    import the provider package or branch on venue names to construct it.
    """

    _select_registry(registry).register(exchange_name, factory)


def create_query_facade(
    route: SelectedQueryRoute,
    *,
    registry: QueryProviderRegistry | None = None,
    account_session_snapshot_validator: (
        Callable[[SelectedQueryRoute, AccountSessionSnapshot | None], bool] | None
    ) = None,
) -> QueryFacade:
    """Compose the selected route's registered provider port into QueryFacade.

    Missing route bindings and provider initialization failures are explicit.
    There is no direct-client or alternate-provider fallback.
    """

    if registry is None:
        return _DEFAULT_ROUTER.create_facade(
            route,
            account_session_snapshot_validator=account_session_snapshot_validator,
        )
    return QueryRouter(registry).create_facade(
        route,
        account_session_snapshot_validator=account_session_snapshot_validator,
    )


def default_query_provider_registry() -> QueryProviderRegistry:
    """Return the SDK process registry used by provider initialization."""

    return _DEFAULT_REGISTRY


_DEFAULT_ROUTER = QueryRouter(_DEFAULT_REGISTRY)


def _select_registry(registry: QueryProviderRegistry | None) -> QueryProviderRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not QueryProviderRegistry:
        raise QueryCompositionError("QUERY_PROVIDER_REGISTRY_INVALID")
    return registry


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", errors="strict")) > 256
    ):
        raise QueryCompositionError("QUERY_PROVIDER_ROUTE_INVALID")


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(discovered.select(group=QUERY_PROVIDER_ENTRY_POINT_GROUP))
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(QUERY_PROVIDER_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None) == QUERY_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise QueryCompositionError("QUERY_PROVIDER_DISCOVERY_FAILED") from None
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
        raise QueryCompositionError("QUERY_PROVIDER_ENTRY_POINT_INVALID")
    return (exchange_name, value, distribution_name)


def _provider_error_code(error: QueryCompositionError, fallback: str) -> str:
    code = error.code
    if (
        type(code) is str
        and 1 <= len(code) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in code
        )
    ):
        return code
    return fallback


__all__ = [
    "QueryCompositionError",
    "QueryProviderRegistry",
    "QueryProviderFactory",
    "QueryProviderRegistryPort",
    "QueryProviderRouteError",
    "QueryRouter",
    "QUERY_PROVIDER_ENTRY_POINT_GROUP",
    "SelectedQueryRoute",
    "create_query_facade",
    "default_query_provider_registry",
    "register_query_provider_factory",
    "select_query_facade",
]
