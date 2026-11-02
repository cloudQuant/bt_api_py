"""Provider-neutral composition for an explicitly selected direct order route.

No provider is built in. A trusted registered provider owns session identity,
correlation issuance, one-shot claims, dispatch and acknowledgement proof.
This module neither grants authorization nor falls back from a managed route.
"""

from __future__ import annotations

import threading
from importlib import metadata as importlib_metadata
from typing import Callable

from bt_api_base.execution_correlation_protocol import (
    DirectCorrelatedOrderExecutionPort,
    DirectOrderProviderFactory,
    DirectOrderProviderRegistryPort,
    SelectedExecutionRoute,
)

from .correlated_execution import (
    _RouteBoundCorrelatedOrderExecutionFacade,
    _same_selected_route,
)


DIRECT_ORDER_PROVIDER_ENTRY_POINT_GROUP = "bt_api.direct_order_providers"


class DirectOrderCompositionError(RuntimeError):
    """Finite failure while selecting one provider-owned direct route."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DirectOrderProviderRegistry(DirectOrderProviderRegistryPort):
    """Exact-route registry with immutable, source-bound registrations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, DirectOrderProviderFactory] = {}
        self._sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: DirectOrderProviderFactory) -> None:
        self._register(exchange_name, factory, source=None)

    def _register(self, exchange_name, factory, *, source) -> None:
        _require_route(exchange_name)
        if not callable(factory):
            raise DirectOrderCompositionError("direct_order_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise DirectOrderCompositionError("direct_order_route_already_registered")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = source

    def _get(self, exchange_name):
        with self._lock:
            return self._factories.get(exchange_name), self._sources.get(exchange_name)

    def _registered_routes(self):
        with self._lock:
            return tuple(sorted(self._factories))


_DEFAULT_REGISTRY = DirectOrderProviderRegistry()


def register_direct_order_provider_factory(exchange_name: str, factory: DirectOrderProviderFactory) -> None:
    _DEFAULT_REGISTRY.register(exchange_name, factory)


class DirectOrderProviderRouter:
    """Select one direct provider per exact route with current-mode fencing."""

    def __init__(
        self,
        route_resolver: Callable[[str], SelectedExecutionRoute | None],
        *,
        direct_mode_current: Callable[[], bool],
        registry: DirectOrderProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver) or not callable(direct_mode_current):
            raise DirectOrderCompositionError("direct_order_route_resolver_invalid")
        if registry is not None and type(registry) is not DirectOrderProviderRegistry:
            raise DirectOrderCompositionError("direct_order_registry_invalid")
        self._resolve = route_resolver
        self._direct_mode_current = direct_mode_current
        self._registry = _DEFAULT_REGISTRY if registry is None else registry
        self._lock = threading.RLock()
        self._states: dict[str, tuple[SelectedExecutionRoute, object | None, str | None]] = {}
        self._owners: dict[int, tuple[object, str]] = {}

    def correlated_order_execution(self, exchange_name: str) -> DirectCorrelatedOrderExecutionPort:
        """Return one retained route-bound port or a finite unavailable error."""
        _require_route(exchange_name)
        route = self._current_route(exchange_name)
        if route is None:
            raise DirectOrderCompositionError("direct_order_route_unavailable")
        with self._lock:
            state = self._states.get(exchange_name)
            if state is not None:
                previous_route, port, failure = state
                if not _same_selected_route(route, previous_route):
                    raise DirectOrderCompositionError("direct_order_route_changed")
                if failure is not None or port is None:
                    raise DirectOrderCompositionError(failure or "direct_order_provider_unavailable")
                return port
            # Retain an unavailable state before calling any factory: a failed
            # construction may have created provider state and is never retried.
            self._states[exchange_name] = (route, None, "direct_order_provider_initialization_failed")
            try:
                factory = self._select_factory(exchange_name)
                provider = factory(route)
                required = (
                    "prepare_order_correlation",
                    "claim_correlated_order",
                    "submit_correlated_order",
                    "cancel_correlated_order",
                    "verify_order_response",
                    "verify_cancel_response",
                )
                if not isinstance(provider, DirectCorrelatedOrderExecutionPort) or any(
                    not callable(getattr(provider, name, None)) for name in required
                ):
                    raise DirectOrderCompositionError("direct_order_provider_invalid")
                previous_owner = self._owners.get(id(provider))
                if previous_owner is not None and previous_owner[0] is provider:
                    if previous_owner[1] != exchange_name:
                        raise DirectOrderCompositionError("direct_order_provider_instance_reused")
                else:
                    self._owners[id(provider)] = (provider, exchange_name)
                if not _same_selected_route(self._current_route(exchange_name), route):
                    raise DirectOrderCompositionError("direct_order_route_changed")
                port = _RouteBoundCorrelatedOrderExecutionFacade(
                    exchange_name, route, self._current_route, provider
                )
            except DirectOrderCompositionError as exc:
                self._states[exchange_name] = (route, None, exc.code)
                raise
            except Exception:
                raise DirectOrderCompositionError("direct_order_provider_initialization_failed") from None
            self._states[exchange_name] = (route, port, None)
            return port

    def _current_route(self, exchange_name):
        try:
            if self._direct_mode_current() is not True:
                return None
            route = self._resolve(exchange_name)
            if type(route) is not SelectedExecutionRoute:
                return None
            SelectedExecutionRoute.__post_init__(route)
            if route.exchange_name != exchange_name or route.execution_context is not None:
                return None
            return route
        except Exception:
            return None

    def _select_factory(self, exchange_name):
        candidates = _entry_points(exchange_name)
        factory, existing_source = self._registry._get(exchange_name)
        if len(candidates) > 1:
            raise DirectOrderCompositionError("direct_order_entry_point_conflict")
        if not candidates:
            if factory is None:
                raise DirectOrderCompositionError("direct_order_provider_unavailable")
            return factory
        entry = candidates[0]
        source = _entry_point_identity(entry, exchange_name)
        if factory is not None:
            if existing_source == source:
                return factory
            raise DirectOrderCompositionError("direct_order_provider_conflict")
        try:
            registrar = entry.load()
        except Exception:
            raise DirectOrderCompositionError("direct_order_provider_import_failed") from None
        if not callable(registrar):
            raise DirectOrderCompositionError("direct_order_entry_point_invalid")
        isolated = DirectOrderProviderRegistry()
        try:
            registrar(isolated)
        except Exception:
            raise DirectOrderCompositionError("direct_order_registration_failed") from None
        if isolated._registered_routes() != (exchange_name,):
            raise DirectOrderCompositionError("direct_order_registration_scope_invalid")
        selected, _ = isolated._get(exchange_name)
        try:
            self._registry._register(exchange_name, selected, source=source)
        except DirectOrderCompositionError:
            registered, registered_source = self._registry._get(exchange_name)
            if registered is None or registered_source != source:
                raise DirectOrderCompositionError("direct_order_provider_conflict") from None
            return registered
        return selected


def _require_route(value):
    if type(value) is not str or not value or value != value.strip():
        raise DirectOrderCompositionError("direct_order_route_invalid")


def _entry_points(exchange_name):
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(discovered.select(group=DIRECT_ORDER_PROVIDER_ENTRY_POINT_GROUP))
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(DIRECT_ORDER_PROVIDER_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                item for item in discovered
                if getattr(item, "group", None) == DIRECT_ORDER_PROVIDER_ENTRY_POINT_GROUP
            )
        return tuple(item for item in candidates if getattr(item, "name", None) == exchange_name)
    except Exception:
        raise DirectOrderCompositionError("direct_order_discovery_failed") from None


def _entry_point_identity(entry, exchange_name):
    try:
        name = getattr(entry, "name", None)
        value = getattr(entry, "value", None)
        distribution = getattr(entry, "dist", None)
        distribution_name = getattr(distribution, "name", "") if distribution is not None else ""
    except Exception:
        raise DirectOrderCompositionError("direct_order_entry_point_invalid") from None
    if name != exchange_name or type(value) is not str or not value or type(distribution_name) is not str:
        raise DirectOrderCompositionError("direct_order_entry_point_invalid")
    return exchange_name, value, distribution_name


__all__ = (
    "DIRECT_ORDER_PROVIDER_ENTRY_POINT_GROUP",
    "DirectOrderCompositionError",
    "DirectOrderProviderRegistry",
    "DirectOrderProviderRouter",
    "register_direct_order_provider_factory",
)
