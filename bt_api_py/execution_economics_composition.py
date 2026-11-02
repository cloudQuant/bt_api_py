"""Exact-route composition for SDK-owned order-economics evidence.

Economics decisions are read-only evidence.  This module does not estimate
amounts, reserve funds, authorize writes, or turn a decision into a dispatch
claim.  A trusted SDK composition must explicitly resolve the selected route
to its economics owner; missing owners produce a typed unavailable decision.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import metadata as importlib_metadata
from threading import RLock
from typing import Optional

from bt_api_base.execution_correlation_protocol import SelectedExecutionRoute
from bt_api_base.execution_economics_protocol import (
    EconomicsRevalidationStage,
    ExecutionEconomicsDecision,
    ExecutionEconomicsPort,
    ExecutionEconomicsProviderFactory,
    ExecutionEconomicsProviderRegistryPort,
    ExecutionEconomicsRequest,
)

__all__ = (
    "ExecutionEconomicsCompositionError",
    "ExecutionEconomicsOwnerResolver",
    "ExecutionEconomicsProviderRegistry",
    "ExecutionEconomicsProviderRouter",
    "ExecutionEconomicsRouteRouter",
    "ExecutionEconomicsUnavailablePort",
    "default_execution_economics_owner_resolver",
    "default_execution_economics_provider_registry",
    "register_execution_economics_provider_factory",
)


EXECUTION_ECONOMICS_PROVIDER_ENTRY_POINT_GROUP = "bt_api.execution_economics_providers"


ExecutionEconomicsRouteResolver = Callable[[str], Optional[SelectedExecutionRoute]]
ExecutionEconomicsOwnerResolver = Callable[
    [SelectedExecutionRoute], Optional[ExecutionEconomicsPort]
]


class ExecutionEconomicsCompositionError(RuntimeError):
    """Finite exact-route economics selection failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ExecutionEconomicsProviderRegistry(ExecutionEconomicsProviderRegistryPort):
    """Exact-route registry for economics owner factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ExecutionEconomicsProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: ExecutionEconomicsProviderFactory) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionEconomicsCompositionError(
                "execution_economics_provider_factory_invalid"
            )
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionEconomicsCompositionError(
                    "execution_economics_provider_already_registered"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ExecutionEconomicsProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionEconomicsCompositionError(
                "execution_economics_provider_factory_invalid"
            )
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionEconomicsCompositionError(
                    "execution_economics_provider_already_registered"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> ExecutionEconomicsProviderFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = ExecutionEconomicsProviderRegistry()


class ExecutionEconomicsProviderRouter:
    """Load one exact provider factory and cache its owner for a route identity."""

    def __init__(
        self,
        registry: ExecutionEconomicsProviderRegistry | None = None,
    ) -> None:
        if registry is None:
            registry = _DEFAULT_REGISTRY
        if type(registry) is not ExecutionEconomicsProviderRegistry:
            raise ExecutionEconomicsCompositionError(
                "execution_economics_provider_registry_invalid"
            )
        self._registry = registry
        self._lock = RLock()
        self._owner_cache: dict[
            str,
            tuple[SelectedExecutionRoute, ExecutionEconomicsPort | None],
        ] = {}

    def resolve_owner(self, route: SelectedExecutionRoute) -> ExecutionEconomicsPort | None:
        """Return the stable trusted owner for this exact selected route."""

        if type(route) is not SelectedExecutionRoute:
            return None
        with self._lock:
            cached = self._owner_cache.get(route.exchange_name)
            if cached is not None and _same_route(cached[0], route):
                return cached[1]
            if not self._load_selected_provider(route.exchange_name):
                self._owner_cache[route.exchange_name] = (route, None)
                return None
            factory = self._registry._resolve(route.exchange_name)
            if factory is None:
                self._owner_cache[route.exchange_name] = (route, None)
                return None
            try:
                owner = factory(route)
            except Exception:
                owner = None
            try:
                valid = owner is not None and isinstance(owner, ExecutionEconomicsPort)
            except Exception:
                valid = False
            if not valid:
                owner = None
            self._owner_cache[route.exchange_name] = (route, owner)
            return owner

    def _load_selected_provider(self, exchange_name: str) -> bool:
        entry_points = _route_entry_points(exchange_name)
        if len(entry_points) > 1:
            return False
        if not entry_points:
            return self._registry._resolve(exchange_name) is not None
        entry_point = entry_points[0]
        source = _entry_point_identity(entry_point, exchange_name)
        registered_source = self._registry._entry_point_source(exchange_name)
        if self._registry._resolve(exchange_name) is not None:
            return registered_source == source
        try:
            registrar = entry_point.load()
        except Exception:
            return False
        if not callable(registrar):
            return False
        isolated = ExecutionEconomicsProviderRegistry()
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
            self._registry._register_entry_point_factory(
                exchange_name,
                factory,
                source,
            )
        except ExecutionEconomicsCompositionError:
            return self._registry._entry_point_source(exchange_name) == source
        return True


def register_execution_economics_provider_factory(
    exchange_name: str,
    factory: ExecutionEconomicsProviderFactory,
    *,
    registry: ExecutionEconomicsProviderRegistry | None = None,
) -> None:
    """Register one provider-owned factory for an exact exchange route."""

    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not ExecutionEconomicsProviderRegistry:
        raise ExecutionEconomicsCompositionError(
            "execution_economics_provider_registry_invalid"
        )
    selected.register(exchange_name, factory)


def default_execution_economics_provider_registry() -> ExecutionEconomicsProviderRegistry:
    """Return the SDK process registry for economics provider factories."""

    return _DEFAULT_REGISTRY


def default_execution_economics_owner_resolver(
    route: SelectedExecutionRoute,
) -> ExecutionEconomicsPort | None:
    """Resolve the selected route through its registered provider owner."""

    return _DEFAULT_PROVIDER_ROUTER.resolve_owner(route)


_DEFAULT_PROVIDER_ROUTER = ExecutionEconomicsProviderRouter(_DEFAULT_REGISTRY)


class ExecutionEconomicsUnavailablePort:
    """Typed fail-closed port for an unavailable route or owner."""

    __slots__ = ("__exchange_name", "__reason_code")

    def __init__(self, exchange_name: str, reason_code: str) -> None:
        _require_exchange_name(exchange_name)
        _require_reason_code(reason_code)
        self.__exchange_name = exchange_name
        self.__reason_code = reason_code

    def evaluate_order_economics(
        self,
        request: ExecutionEconomicsRequest,
    ) -> ExecutionEconomicsDecision:
        if type(request) is not ExecutionEconomicsRequest:
            raise ExecutionEconomicsCompositionError(
                "execution_economics_request_invalid"
            )
        return _unavailable_decision(request, self.__reason_code)

    def revalidate_order_economics(
        self,
        request: ExecutionEconomicsRequest,
        decision: ExecutionEconomicsDecision,
        *,
        stage: EconomicsRevalidationStage,
    ) -> bool:
        return False

    def __repr__(self) -> str:
        return "<unavailable SDK execution-economics port>"


class _RouteBoundExecutionEconomicsPort:
    """Private facade that never exposes its injected economics owner."""

    __slots__ = (
        "__exchange_name",
        "__selected_route",
        "__route_resolver",
        "__owner_resolver",
        "__owner",
    )

    def __init__(
        self,
        selected_route: SelectedExecutionRoute,
        owner: ExecutionEconomicsPort,
        route_resolver: ExecutionEconomicsRouteResolver,
        owner_resolver: ExecutionEconomicsOwnerResolver,
    ) -> None:
        self.__exchange_name = selected_route.exchange_name
        self.__selected_route = selected_route
        self.__route_resolver = route_resolver
        self.__owner_resolver = owner_resolver
        self.__owner = owner

    def evaluate_order_economics(
        self,
        request: ExecutionEconomicsRequest,
    ) -> ExecutionEconomicsDecision:
        if type(request) is not ExecutionEconomicsRequest:
            raise ExecutionEconomicsCompositionError(
                "execution_economics_request_invalid"
            )
        reason = self.__route_request_error(request)
        if reason is not None:
            return _unavailable_decision(request, reason)
        try:
            decision = self.__owner.evaluate_order_economics(request)
        except Exception:
            return _unavailable_decision(
                request,
                "execution_economics_provider_unavailable",
            )
        if type(decision) is not ExecutionEconomicsDecision or decision.request is not request:
            return _unavailable_decision(
                request,
                "execution_economics_projection_invalid",
            )
        try:
            ExecutionEconomicsDecision.__post_init__(decision)
        except Exception:
            return _unavailable_decision(
                request,
                "execution_economics_projection_invalid",
            )
        return decision

    def revalidate_order_economics(
        self,
        request: ExecutionEconomicsRequest,
        decision: ExecutionEconomicsDecision,
        *,
        stage: EconomicsRevalidationStage,
    ) -> bool:
        if (
            type(request) is not ExecutionEconomicsRequest
            or type(decision) is not ExecutionEconomicsDecision
            or decision.request is not request
            or type(stage) is not str
            or stage not in {"claim", "dispatch"}
            or self.__route_request_error(request) is not None
        ):
            return False
        try:
            result = self.__owner.revalidate_order_economics(
                request,
                decision,
                stage=stage,
            )
        except Exception:
            return False
        return type(result) is bool and result is True

    def __route_request_error(self, request: ExecutionEconomicsRequest) -> str | None:
        try:
            current = self.__route_resolver(self.__exchange_name)
        except Exception:
            current = None
        if not _same_route(current, self.__selected_route):
            return "execution_economics_route_changed"
        try:
            current_owner = self.__owner_resolver(current)
        except Exception:
            current_owner = None
        if current_owner is not self.__owner:
            return "execution_economics_owner_changed"
        if (
            self.__selected_route.environment is None
            or request.instrument.environment != self.__selected_route.environment
        ):
            return "execution_economics_instrument_route_mismatch"
        return None

    def __repr__(self) -> str:
        return "<route-bound SDK execution-economics port>"


class ExecutionEconomicsRouteRouter:
    """Select a private economics owner for one exact SDK-selected route.

    ``owner_resolver`` is injected only by trusted SDK composition.  It must
    return the same owner instance for the same route on each call. That
    owner's own evidence issuer validates source, balance and freshness
    references. A route or owner is never inferred from a feed shape, venue
    name, or caller-provided economics values.
    """

    def __init__(
        self,
        route_resolver: ExecutionEconomicsRouteResolver,
        *,
        owner_resolver: ExecutionEconomicsOwnerResolver | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise ExecutionEconomicsCompositionError(
                "execution_economics_route_resolver_invalid"
            )
        if owner_resolver is not None and not callable(owner_resolver):
            raise ExecutionEconomicsCompositionError(
                "execution_economics_owner_resolver_invalid"
            )
        self._route_resolver = route_resolver
        self._owner_resolver = owner_resolver
        self._lock = RLock()
        self._facades: dict[
            str,
            tuple[
                SelectedExecutionRoute,
                ExecutionEconomicsPort,
                _RouteBoundExecutionEconomicsPort,
            ],
        ] = {}

    def execution_economics(self, exchange_name: str) -> ExecutionEconomicsPort:
        """Return the selected route's facade or a typed unavailable port."""

        _require_exchange_name(exchange_name)
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            route = None
        if type(route) is not SelectedExecutionRoute or route.exchange_name != exchange_name:
            return ExecutionEconomicsUnavailablePort(
                exchange_name,
                "execution_economics_route_unavailable",
            )
        owner_resolver = self._owner_resolver
        if owner_resolver is None:
            return ExecutionEconomicsUnavailablePort(
                exchange_name,
                "execution_economics_owner_unavailable",
            )
        try:
            owner = owner_resolver(route)
        except Exception:
            owner = None
        try:
            owner_valid = owner is not None and isinstance(owner, ExecutionEconomicsPort)
        except Exception:
            owner_valid = False
        if not owner_valid:
            return ExecutionEconomicsUnavailablePort(
                exchange_name,
                "execution_economics_owner_unavailable",
            )

        with self._lock:
            existing = self._facades.get(exchange_name)
            if (
                existing is not None
                and _same_route(existing[0], route)
                and existing[1] is owner
            ):
                return existing[2]
            facade = _RouteBoundExecutionEconomicsPort(
                route,
                owner,
                self._route_resolver,
                owner_resolver,
            )
            self._facades[exchange_name] = (route, owner, facade)
            return facade


def _same_route(
    current: object,
    expected: SelectedExecutionRoute,
) -> bool:
    return (
        type(current) is SelectedExecutionRoute
        and current.exchange_name == expected.exchange_name
        and current.client is expected.client
        and current.environment == expected.environment
        and current.execution_context is expected.execution_context
    )


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=EXECUTION_ECONOMICS_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(EXECUTION_ECONOMICS_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == EXECUTION_ECONOMICS_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        return ()
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
        or type(getattr(entry_point, "name", None)) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise ExecutionEconomicsCompositionError(
            "execution_economics_provider_entry_point_invalid"
        )
    return (exchange_name, value, distribution_name)


def _unavailable_decision(
    request: ExecutionEconomicsRequest,
    reason_code: str,
) -> ExecutionEconomicsDecision:
    _require_reason_code(reason_code)
    return ExecutionEconomicsDecision(
        request=request,
        status="unavailable",
        reason_code=reason_code,
        requirement_kind=None,
        instrument=request.instrument,
        evaluated_quantity=None,
        quantity_unit=None,
        required_amount=None,
        available_amount=None,
        currency=None,
        amount_unit=None,
        fee_included=False,
        source_ref=None,
        account_balance_source_ref=None,
        account_scope_ref=None,
        session_generation=request.rule_request.session_generation,
        observed_at_utc=None,
        expires_at_utc=None,
        lease_ref=None,
    )


def _require_exchange_name(value: object) -> None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise ExecutionEconomicsCompositionError(
            "execution_economics_route_invalid"
        )
    try:
        encoded_size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ExecutionEconomicsCompositionError(
            "execution_economics_route_invalid"
        ) from None
    if encoded_size > 256:
        raise ExecutionEconomicsCompositionError(
            "execution_economics_route_invalid"
        )


def _require_reason_code(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or not value.isascii()
        or not all(character.isalnum() or character in "._:-" for character in value)
    ):
        raise ExecutionEconomicsCompositionError(
            "execution_economics_reason_invalid"
        )
