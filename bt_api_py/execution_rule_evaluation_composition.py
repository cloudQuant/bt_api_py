"""Exact-route composition for order-rule evaluators.

This capability returns a typed order decision. It is separate from the
instrument and commission snapshot port used by Comminfo; a snapshot never
stands in for an order decision, and neither port grants dispatch authority.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from importlib import metadata as importlib_metadata
from threading import RLock
from typing import Callable

from bt_api_base.execution_correlation_protocol import SelectedExecutionRoute
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.execution_rules_protocol import (
    ExecutionRuleDecision,
    ExecutionRuleRequest,
    ExecutionRulesPreparationPort,
    ExecutionRulesPreparationUnavailable,
    ExecutionRulesPort,
    ExecutionRulesProviderFactory,
    ExecutionRulesProviderRegistryPort,
    InstrumentExecutionRulesSnapshot,
)

from .execution_rules_composition import (
    _is_utc_datetime,
    _snapshot_facts_digest,
    _snapshot_is_current,
    _utc_now,
)

__all__ = (
    "ORDER_RULES_PROVIDER_ENTRY_POINT_GROUP",
    "OrderRulesCompositionError",
    "OrderRulesProviderRegistry",
    "OrderRulesProviderRouter",
    "OrderRulesRouteResolver",
    "OrderRulesUnavailablePort",
    "default_order_rules_provider_registry",
    "register_order_rules_provider_factory",
)


ORDER_RULES_PROVIDER_ENTRY_POINT_GROUP = "bt_api.execution_rules_providers"
OrderRulesRouteResolver = Callable[[str], SelectedExecutionRoute | None]
_RULES_HEDGE_CLASSES = frozenset({"speculation", "arbitrage", "hedge"})
_MAX_PREPARED_RULES = 128


class OrderRulesCompositionError(RuntimeError):
    """Finite error from order-rules route and provider selection."""

    _CODES = frozenset(
        {
            "order_rules_route_invalid",
            "order_rules_route_unavailable",
            "order_rules_route_changed",
            "order_rules_provider_unavailable",
            "order_rules_provider_registry_invalid",
            "order_rules_provider_factory_invalid",
            "order_rules_provider_already_registered",
            "order_rules_provider_entry_point_invalid",
            "order_rules_provider_discovery_failed",
            "order_rules_provider_projection_invalid",
            "order_rules_instrument_route_mismatch",
            "order_rules_hedge_class_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "order_rules_route_invalid"
        super().__init__(self.code)


class OrderRulesProviderRegistry(ExecutionRulesProviderRegistryPort):
    """Process registry for exact-route order-rules evaluator factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ExecutionRulesProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: ExecutionRulesProviderFactory, /) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise OrderRulesCompositionError("order_rules_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise OrderRulesCompositionError("order_rules_provider_already_registered")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ExecutionRulesProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise OrderRulesCompositionError("order_rules_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise OrderRulesCompositionError("order_rules_provider_already_registered")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> ExecutionRulesProviderFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = OrderRulesProviderRegistry()


def default_order_rules_provider_registry() -> OrderRulesProviderRegistry:
    """Return the SDK process registry; it has no built-in provider."""

    return _DEFAULT_REGISTRY


def register_order_rules_provider_factory(
    exchange_name: str,
    factory: ExecutionRulesProviderFactory,
    *,
    registry: OrderRulesProviderRegistry | None = None,
) -> None:
    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not OrderRulesProviderRegistry:
        raise OrderRulesCompositionError("order_rules_provider_registry_invalid")
    selected.register(exchange_name, factory)


class OrderRulesProviderRouter:
    """Per-SDK exact-route cache for the order evaluator capability."""

    def __init__(
        self,
        route_resolver: OrderRulesRouteResolver,
        *,
        registry: OrderRulesProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise OrderRulesCompositionError("order_rules_route_invalid")
        selected = _DEFAULT_REGISTRY if registry is None else registry
        if type(selected) is not OrderRulesProviderRegistry:
            raise OrderRulesCompositionError("order_rules_provider_registry_invalid")
        self._route_resolver = route_resolver
        self._registry = selected
        self._lock = RLock()
        self._providers: dict[str, tuple[SelectedExecutionRoute, ExecutionRulesPort | None]] = {}
        self._facades: dict[
            str,
            tuple[SelectedExecutionRoute, ExecutionRulesPort | None, _RouteBoundOrderRulesPort],
        ] = {}

    def execution_rules(self, exchange_name: str) -> ExecutionRulesPort:
        _require_exchange_name(exchange_name)
        route = self._resolve_route(exchange_name)
        if route is None:
            return OrderRulesUnavailablePort("order_rules_route_unavailable")
        provider = self._provider_for_route(route)
        if provider is None:
            return OrderRulesUnavailablePort("order_rules_provider_unavailable")
        with self._lock:
            cached = self._facades.get(exchange_name)
            if cached is not None and _same_route(cached[0], route) and cached[1] is provider:
                return cached[2]
            facade = _RouteBoundOrderRulesPort(
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

    def _provider_for_route(self, route: SelectedExecutionRoute) -> ExecutionRulesPort | None:
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
                provider = factory(route)
            except Exception:
                provider = None
            try:
                valid = provider is not None and isinstance(provider, ExecutionRulesPort)
            except Exception:
                valid = False
            if not valid:
                provider = None
            self._providers[route.exchange_name] = (route, provider)
            return provider

    def _load_selected_provider(self, exchange_name: str) -> bool:
        entry_points = _route_entry_points(exchange_name)
        if entry_points is None or len(entry_points) > 1:
            return False
        if not entry_points:
            return self._registry._resolve(exchange_name) is not None
        entry_point = entry_points[0]
        try:
            source = _entry_point_identity(entry_point, exchange_name)
        except OrderRulesCompositionError:
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
        isolated = OrderRulesProviderRegistry()
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
        except OrderRulesCompositionError:
            return self._registry._entry_point_source(exchange_name) == source
        return True


class OrderRulesUnavailablePort:
    """Typed fail-closed order-rules result for an unavailable route."""

    __slots__ = ("__reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.__reason_code = (
            reason_code
            if type(reason_code) is str and reason_code in OrderRulesCompositionError._CODES
            else "order_rules_provider_unavailable"
        )

    def evaluate_order_rules(self, request: ExecutionRuleRequest) -> ExecutionRuleDecision:
        if type(request) is not ExecutionRuleRequest:
            raise OrderRulesCompositionError("order_rules_route_invalid")
        reason = (
            "order_rules_hedge_class_unavailable"
            if request.hedge_class is None
            else self.__reason_code
        )
        return ExecutionRuleDecision(
            request=request,
            status="unavailable",
            reason_code=reason,
            facts=None,
            normalized_position_side=None,
            normalized_offset=None,
            normalized_time_in_force=None,
            commission_estimate=None,
            commission_currency=None,
            commission_charge_unit=None,
            position_evidence_validated=False,
        )

    def prepare_rules(
        self,
        instrument: MarketInstrumentKey,
        *,
        hedge_class: str,
    ) -> InstrumentExecutionRulesSnapshot:
        del instrument, hedge_class
        raise ExecutionRulesPreparationUnavailable(
            _preparation_unavailable_code(self.__reason_code)
        )

    def validate_prepared_rules(
        self,
        snapshot: InstrumentExecutionRulesSnapshot,
        *,
        now_utc: datetime,
    ) -> bool:
        del snapshot, now_utc
        return False

    def __repr__(self) -> str:
        return "<unavailable SDK order-rules port>"


class _RouteBoundOrderRulesPort:
    __slots__ = (
        "__route",
        "__route_resolver",
        "__provider_resolver",
        "__provider",
        "__prepared",
        "__prepared_lock",
    )

    def __init__(
        self,
        *,
        route: SelectedExecutionRoute,
        route_resolver: OrderRulesRouteResolver,
        provider_resolver: Callable[[SelectedExecutionRoute], ExecutionRulesPort | None],
        provider: ExecutionRulesPort,
    ) -> None:
        self.__route = route
        self.__route_resolver = route_resolver
        self.__provider_resolver = provider_resolver
        self.__provider = provider
        self.__prepared: OrderedDict[
            int,
            tuple[
                InstrumentExecutionRulesSnapshot,
                object,
                object,
                MarketInstrumentKey,
                str,
                str,
            ],
        ] = OrderedDict()
        self.__prepared_lock = RLock()

    def evaluate_order_rules(self, request: ExecutionRuleRequest) -> ExecutionRuleDecision:
        if type(request) is not ExecutionRuleRequest:
            raise OrderRulesCompositionError("order_rules_route_invalid")
        if request.hedge_class is None:
            return OrderRulesUnavailablePort("order_rules_hedge_class_unavailable").evaluate_order_rules(request)
        if (
            self.__route.environment is None
            or request.instrument.environment != self.__route.environment
        ):
            return OrderRulesUnavailablePort(
                "order_rules_instrument_route_mismatch"
            ).evaluate_order_rules(request)
        if not self.__require_current_route():
            return OrderRulesUnavailablePort("order_rules_route_changed").evaluate_order_rules(request)
        try:
            decision = self.__provider.evaluate_order_rules(request)
        except Exception:
            return OrderRulesUnavailablePort("order_rules_provider_unavailable").evaluate_order_rules(request)
        try:
            valid = (
                type(decision) is ExecutionRuleDecision
                and decision.request is request
                and (decision.facts is None or decision.facts.instrument == request.instrument)
                and (decision.facts is None or decision.facts.session_generation == request.session_generation)
                and (decision.facts is None or decision.facts.hedge_class == request.hedge_class)
            )
            if valid:
                ExecutionRuleDecision.__post_init__(decision)
        except Exception:
            valid = False
        if not valid:
            return OrderRulesUnavailablePort(
                "order_rules_provider_projection_invalid"
            ).evaluate_order_rules(request)
        return decision

    def prepare_rules(
        self,
        instrument: MarketInstrumentKey,
        *,
        hedge_class: str,
    ) -> InstrumentExecutionRulesSnapshot:
        if type(instrument) is not MarketInstrumentKey:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_projection_invalid"
            )
        if type(hedge_class) is not str or hedge_class not in _RULES_HEDGE_CLASSES:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_hedge_class_unavailable"
            )
        if (
            self.__route.environment is None
            or instrument.environment != self.__route.environment
        ):
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_instrument_route_mismatch"
            )
        if not self.__require_current_route():
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_route_changed"
            )
        if not isinstance(self.__provider, ExecutionRulesPreparationPort):
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_provider_unavailable"
            )
        try:
            snapshot = self.__provider.prepare_rules(
                instrument,
                hedge_class=hedge_class,
            )
        except ExecutionRulesPreparationUnavailable:
            raise
        except Exception:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_unavailable"
            ) from None
        if type(snapshot) is not InstrumentExecutionRulesSnapshot:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_projection_invalid"
            )
        try:
            InstrumentExecutionRulesSnapshot.__post_init__(snapshot)
            facts = snapshot.facts
            if (
                facts.instrument != instrument
                or facts.hedge_class != hedge_class
                or facts.status != "available"
                or facts.instrument.environment != self.__route.environment
            ):
                raise ValueError("prepared rules scope mismatch")
            digest = _snapshot_facts_digest(facts)
        except Exception:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_projection_invalid"
            ) from None
        trusted_now = _utc_now()
        if not _snapshot_is_current(snapshot, trusted_now):
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_not_current"
            )
        try:
            valid = self.__provider.validate_prepared_rules(
                snapshot,
                now_utc=trusted_now,
            )
        except Exception:
            valid = False
        if type(valid) is not bool or valid is not True:
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_not_current"
            )
        if not self.__require_current_route():
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_route_changed"
            )
        if not _snapshot_is_current(snapshot, _utc_now()):
            raise ExecutionRulesPreparationUnavailable(
                "execution_rules_preparation_not_current"
            )
        with self.__prepared_lock:
            self.__prepared[id(snapshot)] = (
                snapshot,
                snapshot.facts,
                snapshot.receipt,
                instrument,
                hedge_class,
                digest,
            )
            self.__prepared.move_to_end(id(snapshot))
            while len(self.__prepared) > _MAX_PREPARED_RULES:
                self.__prepared.popitem(last=False)
        return snapshot

    def validate_prepared_rules(
        self,
        snapshot: InstrumentExecutionRulesSnapshot,
        *,
        now_utc: datetime,
    ) -> bool:
        if (
            type(snapshot) is not InstrumentExecutionRulesSnapshot
            or not _is_utc_datetime(now_utc)
            or not isinstance(self.__provider, ExecutionRulesPreparationPort)
        ):
            return False
        with self.__prepared_lock:
            issued = self.__prepared.get(id(snapshot))
        if issued is None or issued[0] is not snapshot:
            return False
        if snapshot.facts is not issued[1] or snapshot.receipt is not issued[2]:
            return False
        try:
            InstrumentExecutionRulesSnapshot.__post_init__(snapshot)
            if (
                snapshot.facts.instrument != issued[3]
                or snapshot.facts.hedge_class != issued[4]
                or _snapshot_facts_digest(snapshot.facts) != issued[5]
            ):
                return False
        except Exception:
            return False
        trusted_now = _utc_now()
        if (
            not _snapshot_is_current(snapshot, trusted_now)
            or not self.__require_current_route()
        ):
            return False
        try:
            valid = self.__provider.validate_prepared_rules(
                snapshot,
                now_utc=trusted_now,
            )
        except Exception:
            return False
        return (
            type(valid) is bool
            and valid is True
            and self.__require_current_route()
            and _snapshot_is_current(snapshot, _utc_now())
        )

    def __require_current_route(self) -> bool:
        try:
            current = self.__route_resolver(self.__route.exchange_name)
        except Exception:
            current = None
        if not _same_route(current, self.__route):
            return False
        try:
            return self.__provider_resolver(self.__route) is self.__provider
        except Exception:
            return False

    def __repr__(self) -> str:
        return "<route-bound SDK order-rules port>"


def _preparation_unavailable_code(reason_code: str) -> str:
    """Map legacy evaluator unavailability into the preparation contract."""

    return {
        "order_rules_route_unavailable": "execution_rules_preparation_route_unavailable",
        "order_rules_route_changed": "execution_rules_preparation_route_changed",
        "order_rules_hedge_class_unavailable": "execution_rules_preparation_hedge_class_unavailable",
        "order_rules_instrument_route_mismatch": "execution_rules_preparation_instrument_route_mismatch",
        "order_rules_provider_unavailable": "execution_rules_preparation_provider_unavailable",
        "order_rules_provider_registry_invalid": "execution_rules_preparation_provider_unavailable",
        "order_rules_provider_factory_invalid": "execution_rules_preparation_provider_unavailable",
        "order_rules_provider_entry_point_invalid": "execution_rules_preparation_provider_unavailable",
        "order_rules_provider_discovery_failed": "execution_rules_preparation_provider_unavailable",
        "order_rules_provider_projection_invalid": "execution_rules_preparation_projection_invalid",
    }.get(reason_code, "execution_rules_preparation_unavailable")


def _route_entry_points(exchange_name: str) -> tuple[object, ...] | None:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(discovered.select(group=ORDER_RULES_PROVIDER_ENTRY_POINT_GROUP))
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(ORDER_RULES_PROVIDER_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                entry
                for entry in discovered
                if getattr(entry, "group", None) == ORDER_RULES_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        return None
    return tuple(entry for entry in candidates if getattr(entry, "name", None) == exchange_name)


def _entry_point_identity(entry_point: object, exchange_name: str) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(getattr(entry_point, "dist", None), "name", "")
    if (
        type(value) is not str
        or not value
        or type(distribution) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise OrderRulesCompositionError("order_rules_provider_entry_point_invalid")
    return exchange_name, value, distribution


def _require_exchange_name(value: object) -> None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise OrderRulesCompositionError("order_rules_route_invalid")


def _same_route(current: object, expected: SelectedExecutionRoute) -> bool:
    return (
        type(current) is SelectedExecutionRoute
        and current.exchange_name == expected.exchange_name
        and current.client is expected.client
        and current.environment == expected.environment
        and current.execution_context is expected.execution_context
    )
