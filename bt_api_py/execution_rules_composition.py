"""Exact-route SDK composition for source-bound instrument rule snapshots.

The provider owns the facts and their receipt. This module only selects and
route-binds that owner; it never derives instrument terms, fee values, session
identity, or authorization.
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
from bt_api_base.execution_rules_protocol import (
    ExecutionRulesSnapshotPort,
    ExecutionRulesSnapshotProviderFactory,
    ExecutionRulesSnapshotProviderRegistryPort,
    InstrumentExecutionRulesSnapshot,
)
from bt_api_base.market_protocol import MarketInstrumentKey

__all__ = (
    "EXECUTION_RULES_SNAPSHOT_PROVIDER_ENTRY_POINT_GROUP",
    "ExecutionRulesCompositionError",
    "ExecutionRulesSnapshotProviderRegistry",
    "ExecutionRulesSnapshotProviderRouter",
    "ExecutionRulesSnapshotRouteResolver",
    "ExecutionRulesSnapshotUnavailablePort",
    "default_execution_rules_snapshot_provider_registry",
    "register_execution_rules_snapshot_provider_factory",
)


EXECUTION_RULES_SNAPSHOT_PROVIDER_ENTRY_POINT_GROUP = (
    "bt_api.execution_rules_snapshot_providers"
)
ExecutionRulesSnapshotRouteResolver = Callable[[str], SelectedExecutionRoute | None]
_UTC_ZERO = timedelta(0)
_HEDGE_CLASSES = frozenset({"speculation", "arbitrage", "hedge"})


class ExecutionRulesCompositionError(RuntimeError):
    """Finite error from exact-route rules snapshot selection or validation."""

    _CODES = frozenset(
        {
            "execution_rules_route_invalid",
            "execution_rules_route_unavailable",
            "execution_rules_route_changed",
            "execution_rules_provider_unavailable",
            "execution_rules_provider_registry_invalid",
            "execution_rules_provider_factory_invalid",
            "execution_rules_provider_already_registered",
            "execution_rules_provider_entry_point_invalid",
            "execution_rules_provider_discovery_failed",
            "execution_rules_snapshot_invalid",
            "execution_rules_snapshot_unavailable",
            "execution_rules_snapshot_expired",
            "execution_rules_snapshot_not_issued_by_route",
            "execution_rules_snapshot_validation_unavailable",
            "execution_rules_instrument_route_mismatch",
            "execution_rules_hedge_class_unsupported",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "execution_rules_route_invalid"
        super().__init__(self.code)


class ExecutionRulesSnapshotProviderRegistry(ExecutionRulesSnapshotProviderRegistryPort):
    """Process registry for exact-route rules snapshot factories."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ExecutionRulesSnapshotProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        factory: ExecutionRulesSnapshotProviderFactory,
        /,
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionRulesCompositionError("execution_rules_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionRulesCompositionError("execution_rules_provider_already_registered")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ExecutionRulesSnapshotProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionRulesCompositionError("execution_rules_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionRulesCompositionError("execution_rules_provider_already_registered")
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _resolve(self, exchange_name: str) -> ExecutionRulesSnapshotProviderFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)


_DEFAULT_REGISTRY = ExecutionRulesSnapshotProviderRegistry()


def default_execution_rules_snapshot_provider_registry() -> ExecutionRulesSnapshotProviderRegistry:
    """Return the SDK process registry; no provider is installed by default."""

    return _DEFAULT_REGISTRY


def register_execution_rules_snapshot_provider_factory(
    exchange_name: str,
    factory: ExecutionRulesSnapshotProviderFactory,
    *,
    registry: ExecutionRulesSnapshotProviderRegistry | None = None,
) -> None:
    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not ExecutionRulesSnapshotProviderRegistry:
        raise ExecutionRulesCompositionError("execution_rules_provider_registry_invalid")
    selected.register(exchange_name, factory)


class ExecutionRulesSnapshotProviderRouter:
    """Per-SDK-instance exact-route provider cache and facade selector."""

    def __init__(
        self,
        route_resolver: ExecutionRulesSnapshotRouteResolver,
        *,
        registry: ExecutionRulesSnapshotProviderRegistry | None = None,
        max_issued_snapshots: int = 128,
    ) -> None:
        if not callable(route_resolver):
            raise ExecutionRulesCompositionError("execution_rules_route_invalid")
        selected = _DEFAULT_REGISTRY if registry is None else registry
        if type(selected) is not ExecutionRulesSnapshotProviderRegistry:
            raise ExecutionRulesCompositionError("execution_rules_provider_registry_invalid")
        if type(max_issued_snapshots) is not int or max_issued_snapshots <= 0:
            raise ExecutionRulesCompositionError("execution_rules_route_invalid")
        self._route_resolver = route_resolver
        self._registry = selected
        self._max_issued_snapshots = max_issued_snapshots
        self._lock = RLock()
        self._providers: dict[
            str,
            tuple[SelectedExecutionRoute, ExecutionRulesSnapshotPort | None],
        ] = {}
        self._facades: dict[
            str,
            tuple[
                SelectedExecutionRoute,
                ExecutionRulesSnapshotPort | None,
                _RouteBoundExecutionRulesSnapshotPort,
            ],
        ] = {}

    def execution_rules_snapshot(self, exchange_name: str) -> ExecutionRulesSnapshotPort:
        _require_exchange_name(exchange_name)
        route = self._resolve_route(exchange_name)
        if route is None:
            return ExecutionRulesSnapshotUnavailablePort("execution_rules_route_unavailable")
        provider = self._provider_for_route(route)
        if provider is None:
            return ExecutionRulesSnapshotUnavailablePort("execution_rules_provider_unavailable")
        with self._lock:
            cached = self._facades.get(exchange_name)
            if (
                cached is not None
                and _same_route(cached[0], route)
                and cached[1] is provider
            ):
                return cached[2]
            facade = _RouteBoundExecutionRulesSnapshotPort(
                route=route,
                route_resolver=self._route_resolver,
                provider_resolver=self._provider_for_route,
                provider=provider,
                max_issued_snapshots=self._max_issued_snapshots,
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
    ) -> ExecutionRulesSnapshotPort | None:
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
                valid = provider is not None and isinstance(provider, ExecutionRulesSnapshotPort)
            except Exception:
                valid = False
            if not valid:
                provider = None
            self._providers[route.exchange_name] = (route, provider)
            return provider

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
        except ExecutionRulesCompositionError:
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
        isolated = ExecutionRulesSnapshotProviderRegistry()
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
        except ExecutionRulesCompositionError:
            return self._registry._entry_point_source(exchange_name) == source
        return True


class ExecutionRulesSnapshotUnavailablePort:
    """Typed fail-closed facade for a route with no trusted snapshot owner."""

    __slots__ = ("__reason_code",)

    def __init__(self, reason_code: str) -> None:
        if type(reason_code) is not str or reason_code not in ExecutionRulesCompositionError._CODES:
            reason_code = "execution_rules_provider_unavailable"
        self.__reason_code = reason_code

    def execution_rules_snapshot(
        self,
        instrument: MarketInstrumentKey,
        *,
        hedge_class: str,
    ) -> InstrumentExecutionRulesSnapshot:
        if type(instrument) is not MarketInstrumentKey:
            raise ExecutionRulesCompositionError("execution_rules_route_invalid")
        if type(hedge_class) is not str or hedge_class not in _HEDGE_CLASSES:
            raise ExecutionRulesCompositionError("execution_rules_hedge_class_unsupported")
        raise ExecutionRulesCompositionError("execution_rules_snapshot_unavailable")

    def validate_execution_rules_snapshot(
        self,
        snapshot: InstrumentExecutionRulesSnapshot,
        *,
        now_utc: datetime,
    ) -> bool:
        return False

    def __repr__(self) -> str:
        return "<unavailable SDK execution-rules snapshot port>"


class _RouteBoundExecutionRulesSnapshotPort:
    __slots__ = (
        "__route",
        "__route_resolver",
        "__provider_resolver",
        "__provider",
        "__issued",
        "__max_issued",
        "__lock",
    )

    def __init__(
        self,
        *,
        route: SelectedExecutionRoute,
        route_resolver: ExecutionRulesSnapshotRouteResolver,
        provider_resolver: Callable[
            [SelectedExecutionRoute], ExecutionRulesSnapshotPort | None
        ],
        provider: ExecutionRulesSnapshotPort,
        max_issued_snapshots: int,
    ) -> None:
        self.__route = route
        self.__route_resolver = route_resolver
        self.__provider_resolver = provider_resolver
        self.__provider = provider
        self.__issued: OrderedDict[
            int,
            tuple[
                InstrumentExecutionRulesSnapshot,
                object,
                object,
                str,
            ],
        ] = OrderedDict()
        self.__max_issued = max_issued_snapshots
        self.__lock = RLock()

    def execution_rules_snapshot(
        self,
        instrument: MarketInstrumentKey,
        *,
        hedge_class: str,
    ) -> InstrumentExecutionRulesSnapshot:
        if type(instrument) is not MarketInstrumentKey:
            raise ExecutionRulesCompositionError("execution_rules_snapshot_invalid")
        if type(hedge_class) is not str or hedge_class not in _HEDGE_CLASSES:
            raise ExecutionRulesCompositionError("execution_rules_hedge_class_unsupported")
        if (
            self.__route.environment is None
            or instrument.environment != self.__route.environment
        ):
            raise ExecutionRulesCompositionError("execution_rules_instrument_route_mismatch")
        if not self.__require_current_route():
            raise ExecutionRulesCompositionError("execution_rules_route_changed")
        try:
            snapshot = self.__provider.execution_rules_snapshot(
                instrument,
                hedge_class=hedge_class,
            )
        except ExecutionRulesCompositionError:
            raise
        except Exception:
            raise ExecutionRulesCompositionError("execution_rules_snapshot_unavailable") from None
        if (
            type(snapshot) is not InstrumentExecutionRulesSnapshot
            or snapshot.facts.instrument != instrument
            or snapshot.facts.hedge_class != hedge_class
            or snapshot.facts.status != "available"
        ):
            raise ExecutionRulesCompositionError("execution_rules_snapshot_invalid")
        InstrumentExecutionRulesSnapshot.__post_init__(snapshot)
        trusted_now = _utc_now()
        if not _snapshot_is_current(snapshot, trusted_now):
            raise ExecutionRulesCompositionError("execution_rules_snapshot_expired")
        try:
            valid = self.__provider.validate_execution_rules_snapshot(
                snapshot,
                now_utc=trusted_now,
            )
        except Exception:
            valid = None
        if type(valid) is not bool or valid is not True:
            raise ExecutionRulesCompositionError("execution_rules_snapshot_validation_unavailable")
        try:
            digest = _snapshot_facts_digest(snapshot.facts)
        except Exception:
            raise ExecutionRulesCompositionError("execution_rules_snapshot_invalid") from None
        with self.__lock:
            self.__issued[id(snapshot)] = (
                snapshot,
                snapshot.facts,
                snapshot.receipt,
                digest,
            )
            self.__issued.move_to_end(id(snapshot))
            while len(self.__issued) > self.__max_issued:
                self.__issued.popitem(last=False)
        return snapshot

    def validate_execution_rules_snapshot(
        self,
        snapshot: InstrumentExecutionRulesSnapshot,
        *,
        now_utc: datetime,
    ) -> bool:
        if type(snapshot) is not InstrumentExecutionRulesSnapshot or not _is_utc_datetime(now_utc):
            return False
        with self.__lock:
            issued = self.__issued.get(id(snapshot))
            if issued is None or issued[0] is not snapshot:
                return False
        if snapshot.facts is not issued[1] or snapshot.receipt is not issued[2]:
            return False
        try:
            InstrumentExecutionRulesSnapshot.__post_init__(snapshot)
            content_digest = _snapshot_facts_digest(snapshot.facts)
        except Exception:
            return False
        if content_digest != issued[3]:
            return False
        if self.__require_current_route() is False:
            return False
        trusted_now = _utc_now()
        if not _snapshot_is_current(snapshot, trusted_now):
            return False
        try:
            current_provider = self.__provider_resolver(self.__route)
        except Exception:
            current_provider = None
        if current_provider is not self.__provider:
            return False
        try:
            result = self.__provider.validate_execution_rules_snapshot(
                snapshot,
                now_utc=trusted_now,
            )
        except Exception:
            return False
        return type(result) is bool and result is True

    def __require_current_route(self) -> bool:
        try:
            current = self.__route_resolver(self.__route.exchange_name)
        except Exception:
            current = None
        if not _same_route(current, self.__route):
            return False
        try:
            current_provider = self.__provider_resolver(current)
        except Exception:
            current_provider = None
        return current_provider is self.__provider

    def __repr__(self) -> str:
        return "<route-bound SDK execution-rules snapshot port>"


def _snapshot_is_current(
    snapshot: InstrumentExecutionRulesSnapshot,
    now_utc: datetime,
) -> bool:
    facts = snapshot.facts
    return (
        _is_utc_datetime(now_utc)
        and facts.observed_at_utc is not None
        and facts.expires_at_utc is not None
        and facts.observed_at_utc <= now_utc < facts.expires_at_utc
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_utc_datetime(value: object) -> bool:
    if type(value) is not datetime or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() == _UTC_ZERO
    except Exception:
        return False


def _same_route(current: object, expected: SelectedExecutionRoute) -> bool:
    return (
        type(current) is SelectedExecutionRoute
        and current.exchange_name == expected.exchange_name
        and current.client is expected.client
        and current.environment == expected.environment
        and current.execution_context is expected.execution_context
    )


def _route_entry_points(exchange_name: str) -> tuple[object, ...] | None:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=EXECUTION_RULES_SNAPSHOT_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(EXECUTION_RULES_SNAPSHOT_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == EXECUTION_RULES_SNAPSHOT_PROVIDER_ENTRY_POINT_GROUP
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
        raise ExecutionRulesCompositionError("execution_rules_provider_entry_point_invalid")
    return (exchange_name, value, distribution_name)


def _require_exchange_name(value: object) -> None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise ExecutionRulesCompositionError("execution_rules_route_invalid")


def _snapshot_facts_digest(facts: object) -> str:
    """Fingerprint every projected neutral fact to detect in-process mutation."""

    from bt_api_base.execution_rules_protocol import InstrumentExecutionFacts

    if type(facts) is not InstrumentExecutionFacts:
        raise ValueError("execution_rules_snapshot_invalid")
    InstrumentExecutionFacts.__post_init__(facts)
    instrument = facts.instrument
    content = {
        "instrument": {
            "provider": instrument.provider,
            "venue": instrument.venue,
            "environment": instrument.environment,
            "product_type": instrument.product_type,
            "instrument_id": instrument.instrument_id,
            "native_symbol": instrument.native_symbol,
        },
        "facts": {
            "status": facts.status,
            "reason_code": facts.reason_code,
            "session_generation": facts.session_generation,
            "source_ref": facts.source_ref,
            "observed_at_utc": (
                facts.observed_at_utc.isoformat() if facts.observed_at_utc is not None else None
            ),
            "expires_at_utc": (
                facts.expires_at_utc.isoformat() if facts.expires_at_utc is not None else None
            ),
            "asset_kind": facts.asset_kind,
            "price_tick": facts.price_tick,
            "contract_multiplier": facts.contract_multiplier,
            "price_currency": facts.price_currency,
            "price_currency_status": facts.price_currency_status,
            "price_currency_source_ref": facts.price_currency_source_ref,
            "price_currency_session_generation": facts.price_currency_session_generation,
            "option_right": facts.option_right,
            "option_strike": facts.option_strike,
            "option_expiry": facts.option_expiry.isoformat() if facts.option_expiry else None,
            "option_premium_style": facts.option_premium_style,
            "option_underlying_instrument_id": getattr(
                facts, "option_underlying_instrument_id", None
            ),
            "option_underlying_multiple": getattr(
                facts, "option_underlying_multiple", None
            ),
            "hedge_class": facts.hedge_class,
            "commission_rules": [
                {
                    "role": rule.role,
                    "by_money_rate": rule.by_money_rate,
                    "by_volume_amount": rule.by_volume_amount,
                    "currency": rule.currency,
                    "currency_status": rule.currency_status,
                    "source_ref": rule.source_ref,
                    "currency_source_ref": rule.currency_source_ref,
                    "session_generation": rule.session_generation,
                    "currency_session_generation": rule.currency_session_generation,
                }
                for rule in facts.commission_rules
            ],
        },
    }
    encoded = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("ascii")).hexdigest()
