"""Managed provider-neutral composition for correlated order writes.

This module is the SDK-owned composition boundary between a durable neutral
order-correlation issuer, the managed execution gate, and one already selected
provider order port. It imports no venue implementation and loads only a
registered entry point for the exact selected route. A caller must inject a
trusted issuer and the selected route.

The issuer reservation, provider reservation, provider claim, authorization,
and raw provider response remain private to this object.  Backtrader receives
only the neutral correlation, an opaque one-shot claim handle, a small
immutable dispatch projection, and (only after provider verification) a typed
acknowledgement.  This in-memory composition does not itself make the issuer
and provider stores transactional, implement provider recovery, or prove that
an injected provider is authoritative.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata as importlib_metadata
import inspect
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Optional

from bt_api_base.execution_capacity_protocol import (
    OrderCapacitySnapshot,
    unavailable_order_capacity_snapshot,
)
from bt_api_base.execution_correlation_protocol import (
    OrderCorrelation,
    OrderCorrelationIssuer,
    SelectedExecutionRoute,
    TypedOrderProvider,
    TypedOrderProviderFactory,
    TypedOrderProviderRegistryPort,
    VerifiedOrderAcknowledgement,
)
from bt_api_py._contracts.models import CancelOrderRequest, OrderRequest
from bt_api_py.managed_execution import (
    ManagedExecutionFacade,
    ManagedExecutionOutcomeUnknown,
    ManagedExecutionUnavailable,
)
from bt_api_py.execution_capacity import ExecutionCapacityFacade
from bt_api_py.facade_protocol import CorrelatedOrderExecutionFacade

__all__ = (
    "CorrelatedExecutionCompositionError",
    "ManagedCorrelatedOrderExecutionFacade",
    "SelectedExecutionRoute",
    "TYPED_ORDER_PROVIDER_ENTRY_POINT_GROUP",
    "TypedOrderProviderFactory",
    "TypedOrderProviderRegistry",
    "TypedOrderRouteResolver",
    "TypedOrderProviderRouter",
    "default_typed_order_provider_registry",
    "register_typed_order_provider_factory",
)


ManagedOrderOperation = Literal["submit", "cancel"]
_DEFAULT_MAX_CORRELATIONS = 100_000
TYPED_ORDER_PROVIDER_ENTRY_POINT_GROUP = "bt_api.typed_order_providers"


class CorrelatedExecutionCompositionError(RuntimeError):
    """Finite provider-neutral failure from correlated execution composition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


TypedOrderRouteResolver = Callable[[str], Optional[SelectedExecutionRoute]]


class TypedOrderProviderRegistry(TypedOrderProviderRegistryPort):
    """Exact exchange-name registry for SDK-internal typed-order providers.

    Registrations are immutable. A provider package may either register an
    exact route directly or expose one registrar through the
    ``bt_api.typed_order_providers`` entry-point group. The router imports only
    the unique registrar matching the already-selected route. Neither a
    registry entry nor its returned call shape grants authorization; the
    provider must validate the correlation, authorization, claim and session.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, TypedOrderProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: TypedOrderProviderFactory) -> None:
        """Register one factory under an exact SDK exchange name."""

        _require_neutral_text(exchange_name, "exchange_name")
        if not callable(factory):
            raise CorrelatedExecutionCompositionError("typed_order_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise CorrelatedExecutionCompositionError(
                    "typed_order_provider_route_already_registered"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_neutral_text(exchange_name, "exchange_name")
        with self._lock:
            return exchange_name in self._factories

    def resolve(self, exchange_name: str) -> TypedOrderProviderFactory:
        _require_neutral_text(exchange_name, "exchange_name")
        with self._lock:
            factory = self._factories.get(exchange_name)
        if factory is None:
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_capability_unavailable"
            )
        return factory

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: TypedOrderProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_neutral_text(exchange_name, "exchange_name")
        if not callable(factory):
            raise CorrelatedExecutionCompositionError("typed_order_provider_factory_invalid")
        with self._lock:
            if exchange_name in self._factories:
                raise CorrelatedExecutionCompositionError(
                    "typed_order_provider_route_already_registered"
                )
            # Publish the exact entry-point factory and its source atomically.
            # Two BtApi instances can resolve the same route concurrently; a
            # second router must observe the same source instead of seeing a
            # transient registered-without-source state and reporting a false
            # capability conflict.
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


@dataclass(repr=False)
class _TypedProviderRouteState:
    route: SelectedExecutionRoute = field(repr=False)
    provider: TypedOrderProvider | None = field(default=None, repr=False)
    execution: CorrelatedOrderExecutionFacade | None = field(default=None, repr=False)
    capacity: ExecutionCapacityFacade | None = field(default=None, repr=False)
    failure_code: str | None = None
    loading: bool = False


class _UnavailableCorrelatedOrderExecutionFacade:
    """Shape-compatible fail-closed facade for an unavailable route."""

    __slots__ = ("__exchange_name", "__reason")

    def __init__(self, exchange_name: str, reason: str) -> None:
        self.__exchange_name = exchange_name
        self.__reason = reason

    @property
    def exchange_name(self) -> str:
        return self.__exchange_name

    def prepare_order_correlation(self, exchange_name: str, symbol: str) -> OrderCorrelation:
        raise ManagedExecutionUnavailable(self.__reason)

    def claim_correlated_order(
        self,
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        request: OrderRequest | CancelOrderRequest,
        *,
        authorization: object,
    ) -> object:
        raise ManagedExecutionUnavailable(self.__reason)

    async def submit_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: OrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        raise ManagedExecutionUnavailable(self.__reason)

    async def cancel_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: CancelOrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        raise ManagedExecutionUnavailable(self.__reason)

    def verify_order_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None:
        return None

    def verify_cancel_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None:
        return None

    def __repr__(self) -> str:
        return "<unavailable correlated order facade>"


class _RouteBoundCorrelatedOrderExecutionFacade:
    """Prevent a cached write facade from surviving a route identity change."""

    __slots__ = ("__exchange_name", "__route", "__route_resolver", "__delegate")

    def __init__(
        self,
        exchange_name: str,
        route: SelectedExecutionRoute,
        route_resolver: TypedOrderRouteResolver,
        delegate: CorrelatedOrderExecutionFacade,
    ) -> None:
        self.__exchange_name = exchange_name
        self.__route = route
        self.__route_resolver = route_resolver
        self.__delegate = delegate

    @property
    def exchange_name(self) -> str:
        return self.__exchange_name

    def prepare_order_correlation(self, exchange_name: str, symbol: str) -> OrderCorrelation:
        self.__require_current_route()
        return self.__delegate.prepare_order_correlation(exchange_name, symbol)

    def claim_correlated_order(
        self,
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        request: OrderRequest | CancelOrderRequest,
        *,
        authorization: object,
    ) -> object:
        self.__require_current_route()
        return self.__delegate.claim_correlated_order(
            operation, correlation, request, authorization=authorization
        )

    async def submit_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: OrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        self.__require_current_route()
        return await self.__delegate.submit_correlated_order(
            correlation, request, claim=claim, authorization=authorization
        )

    async def cancel_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: CancelOrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        self.__require_current_route()
        return await self.__delegate.cancel_correlated_order(
            correlation, request, claim=claim, authorization=authorization
        )

    def verify_order_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None:
        try:
            self.__require_current_route()
        except ManagedExecutionUnavailable:
            return None
        return self.__delegate.verify_order_response(correlation, response)

    def verify_cancel_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None:
        try:
            self.__require_current_route()
        except ManagedExecutionUnavailable:
            return None
        return self.__delegate.verify_cancel_response(correlation, response)

    def __require_current_route(self) -> None:
        try:
            current = self.__route_resolver(self.__exchange_name)
        except Exception:
            raise ManagedExecutionUnavailable("typed_order_route_resolution_failed") from None
        if not _same_selected_route(current, self.__route):
            raise ManagedExecutionUnavailable("typed_order_route_changed")

    def __repr__(self) -> str:
        return "<route-bound correlated order facade>"


class _RouteBoundCapacityProvider:
    """Recheck the selected route before reading one provider's capacity facts."""

    __slots__ = ("__exchange_name", "__route", "__route_resolver", "__provider")

    def __init__(
        self,
        exchange_name: str,
        route: SelectedExecutionRoute,
        route_resolver: TypedOrderRouteResolver,
        provider: TypedOrderProvider,
    ) -> None:
        self.__exchange_name = exchange_name
        self.__route = route
        self.__route_resolver = route_resolver
        self.__provider = provider

    def order_capacity_facts(self) -> OrderCapacitySnapshot:
        try:
            current = self.__route_resolver(self.__exchange_name)
        except Exception:
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_route_resolution_failed"
            )
        if not _same_selected_route(current, self.__route):
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_route_unavailable"
            )
        try:
            facts = self.__provider.order_capacity_facts()
        except Exception:
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_provider_unavailable"
            )
        if type(facts) is not OrderCapacitySnapshot:
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_projection_invalid"
            )
        return facts


class TypedOrderProviderRouter:
    """Cache one exact selected provider for route-bound execution and capacity.

    The route resolver and optional durable issuer are supplied by trusted SDK
    composition. A missing issuer keeps correlated writes unavailable before
    provider construction or correlation allocation. Capacity uses the same
    cached provider when one has been constructed; it never discovers a
    separate provider or falls back to a client method.
    """

    def __init__(
        self,
        route_resolver: TypedOrderRouteResolver,
        *,
        issuer: OrderCorrelationIssuer | None = None,
        registry: TypedOrderProviderRegistry | None = None,
        max_correlations: int = _DEFAULT_MAX_CORRELATIONS,
    ) -> None:
        if not callable(route_resolver):
            raise CorrelatedExecutionCompositionError("typed_order_route_resolver_invalid")
        if registry is None:
            registry = _DEFAULT_TYPED_ORDER_PROVIDER_REGISTRY
        elif type(registry) is not TypedOrderProviderRegistry:
            raise CorrelatedExecutionCompositionError("typed_order_provider_registry_invalid")
        if issuer is not None and not _has_methods(
            issuer,
            (
                "issue_order_correlation",
                "resolve_order_correlation",
                "validate_order_correlation",
            ),
        ):
            raise CorrelatedExecutionCompositionError("order_correlation_issuer_unavailable")
        if type(max_correlations) is not int or not 1 <= max_correlations <= 1_000_000:
            raise CorrelatedExecutionCompositionError("correlated_execution_capacity_invalid")
        self.__route_resolver = route_resolver
        self.__issuer = issuer
        self.__registry = registry
        self.__max_correlations = max_correlations
        self.__lock = threading.RLock()
        self.__states: dict[str, _TypedProviderRouteState] = {}
        self.__provider_owners: dict[int, tuple[object, str]] = {}
        self.__unavailable_execution: dict[str, _UnavailableCorrelatedOrderExecutionFacade] = {}

    def correlated_order_execution(
        self, exchange_name: str
    ) -> CorrelatedOrderExecutionFacade:
        """Select the cached exact-route correlated execution wrapper."""

        try:
            _require_neutral_text(exchange_name, "exchange_name")
        except Exception:
            raise CorrelatedExecutionCompositionError("typed_order_route_invalid") from None
        if self.__issuer is None:
            return self.__unavailable_facade(
                exchange_name, "order_correlation_issuer_unavailable"
            )
        route, reason = self.__resolve_route(exchange_name)
        if route is None:
            return self.__unavailable_facade(exchange_name, reason)
        state = self.__provider_state(route)
        if state.failure_code is not None or state.execution is None:
            return self.__unavailable_facade(
                exchange_name, state.failure_code or "typed_order_provider_unavailable"
            )
        return state.execution

    def order_capacity(self, exchange_name: str) -> ExecutionCapacityFacade:
        """Select capacity facts from the route's same cached provider object."""

        try:
            _require_neutral_text(exchange_name, "exchange_name")
        except Exception:
            raise CorrelatedExecutionCompositionError(
                "order_capacity_route_invalid"
            ) from None
        route, reason = self.__resolve_route(exchange_name)
        if route is None:
            return ExecutionCapacityFacade(
                exchange_name,
                unavailable_reason=_capacity_reason_from_route_error(reason),
            )
        state = self.__provider_state(route)
        if state.failure_code is not None or state.provider is None:
            return ExecutionCapacityFacade(
                exchange_name,
                unavailable_reason=_capacity_reason_from_provider_error(
                    state.failure_code
                ),
            )
        if not _has_methods(state.provider, ("order_capacity_facts",)):
            return ExecutionCapacityFacade(
                exchange_name, unavailable_reason="order_capacity_capability_unavailable"
            )
        with self.__lock:
            if state.capacity is None:
                state.capacity = ExecutionCapacityFacade(
                    exchange_name,
                    _RouteBoundCapacityProvider(
                        exchange_name,
                        route,
                        self.__route_resolver,
                        state.provider,
                    ),
                )
            return state.capacity

    def __resolve_route(
        self, exchange_name: str
    ) -> tuple[SelectedExecutionRoute | None, str]:
        try:
            route = self.__route_resolver(exchange_name)
        except Exception:
            return None, "typed_order_route_resolution_failed"
        if route is None:
            return None, "typed_order_route_unavailable"
        if type(route) is not SelectedExecutionRoute or route.exchange_name != exchange_name:
            return None, "typed_order_route_invalid"
        try:
            SelectedExecutionRoute.__post_init__(route)
        except Exception:
            return None, "typed_order_route_invalid"
        return route, ""

    def __provider_state(self, route: SelectedExecutionRoute) -> _TypedProviderRouteState:
        exchange_name = route.exchange_name
        with self.__lock:
            cached = self.__states.get(exchange_name)
            if cached is not None:
                if not _same_selected_route(route, cached.route):
                    cached.failure_code = "typed_order_route_changed"
                    cached.execution = None
                    cached.capacity = None
                    return cached
                if cached.provider is not None or cached.failure_code is not None:
                    return cached
                if cached.loading:
                    cached.failure_code = "typed_order_provider_initialization_reentrant"
                    return cached

            else:
                cached = _TypedProviderRouteState(route=route)
                self.__states[exchange_name] = cached

            cached.loading = True
            try:
                factory = self.__load_selected_factory(exchange_name)
                provider = factory(route, self.__issuer)
            except CorrelatedExecutionCompositionError as error:
                cached.failure_code = _finite_provider_error_code(error.code)
                return cached
            except Exception:
                cached.failure_code = "typed_order_provider_factory_failed"
                return cached
            finally:
                cached.loading = False

            if cached.failure_code is not None:
                return cached

            if not _has_methods(
                provider,
                (
                    "bind_order_reservation",
                    "resolve_order_reservation",
                    "claim_correlated_order",
                    "submit_correlated_order",
                    "cancel_correlated_order",
                    "verify_order_response",
                    "verify_cancel_response",
                ),
            ):
                cached.failure_code = "typed_order_provider_invalid"
                return cached
            prior_owner = self.__provider_owners.get(id(provider))
            if prior_owner is not None and prior_owner[0] is provider:
                if prior_owner[1] != exchange_name:
                    cached.failure_code = "typed_order_provider_instance_reused"
                    return cached
            else:
                self.__provider_owners[id(provider)] = (provider, exchange_name)
            cached.provider = provider
            if self.__issuer is not None:
                try:
                    cached.execution = ManagedCorrelatedOrderExecutionFacade(
                        exchange_name=exchange_name,
                        issuer=self.__issuer,
                        provider=provider,
                        max_correlations=self.__max_correlations,
                    )
                except Exception:
                    cached.failure_code = "typed_order_execution_facade_unavailable"
                    cached.provider = None
                    return cached
                cached.execution = _RouteBoundCorrelatedOrderExecutionFacade(
                    exchange_name,
                    route,
                    self.__route_resolver,
                    cached.execution,
                )
            return cached

    def __load_selected_factory(self, exchange_name: str) -> TypedOrderProviderFactory:
        candidates = _typed_order_entry_points(exchange_name)
        if len(candidates) > 1:
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_entry_point_conflict"
            )
        if not candidates:
            if self.__registry.has_capability(exchange_name):
                return self.__registry.resolve(exchange_name)
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_capability_unavailable"
            )

        entry_point = candidates[0]
        source = _typed_order_entry_point_identity(entry_point, exchange_name)
        if self.__registry.has_capability(exchange_name):
            if self.__registry._entry_point_source(exchange_name) == source:
                return self.__registry.resolve(exchange_name)
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_capability_conflict"
            )
        try:
            registrar = entry_point.load()
        except Exception:
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_import_failed"
            ) from None
        if not callable(registrar):
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_entry_point_invalid"
            )
        isolated = TypedOrderProviderRegistry()
        try:
            registrar(isolated)
        except Exception:
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_registration_failed"
            ) from None
        if isolated._registered_routes() != (exchange_name,):
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_registration_scope_invalid"
            )
        factory = isolated.resolve(exchange_name)
        try:
            self.__registry._register_entry_point_factory(exchange_name, factory, source)
        except CorrelatedExecutionCompositionError:
            # Another per-BtApi router may have loaded and registered this
            # exact entry point after our first registry check. Accept only an
            # identical source identity; direct registration or a different
            # distribution/value remains a hard conflict.
            if self.__registry._entry_point_source(exchange_name) == source:
                return self.__registry.resolve(exchange_name)
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_capability_conflict"
            ) from None
        return factory

    def __unavailable_facade(
        self, exchange_name: str, reason: str
    ) -> _UnavailableCorrelatedOrderExecutionFacade:
        with self.__lock:
            facade = self.__unavailable_execution.get(exchange_name)
            if facade is None:
                facade = _UnavailableCorrelatedOrderExecutionFacade(exchange_name, reason)
                self.__unavailable_execution[exchange_name] = facade
            return facade


_DEFAULT_TYPED_ORDER_PROVIDER_REGISTRY = TypedOrderProviderRegistry()


def default_typed_order_provider_registry() -> TypedOrderProviderRegistry:
    """Return the process SDK registry used by registered provider packages."""

    return _DEFAULT_TYPED_ORDER_PROVIDER_REGISTRY


def register_typed_order_provider_factory(
    exchange_name: str,
    factory: TypedOrderProviderFactory,
    *,
    registry: TypedOrderProviderRegistry | None = None,
) -> None:
    """Register one provider factory under an exact route; never a fallback."""

    selected = _DEFAULT_TYPED_ORDER_PROVIDER_REGISTRY if registry is None else registry
    if type(selected) is not TypedOrderProviderRegistry:
        raise CorrelatedExecutionCompositionError("typed_order_provider_registry_invalid")
    selected.register(exchange_name, factory)


class _OpaqueClaimHandle:
    """SDK-issued public claim token; authority is its private registry row."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<SDK correlated claim>"


@dataclass(frozen=True, repr=False)
class _ProviderCommand:
    operation: ManagedOrderOperation
    correlation: OrderCorrelation
    request: OrderRequest | CancelOrderRequest
    issuer_reservation: object = field(repr=False, compare=False)
    provider_reservation: object = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<private correlated provider command>"


@dataclass(frozen=True, repr=False)
class _ProviderClaim:
    command: _ProviderCommand = field(repr=False, compare=False)
    authorization: object = field(repr=False, compare=False)
    value: object = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<private provider claim>"


@dataclass(frozen=True, repr=False)
class _ProviderDispatchResult:
    command: _ProviderCommand = field(repr=False, compare=False)
    value: object = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<private provider dispatch result>"


@dataclass(repr=False)
class _ClaimRecord:
    operation: ManagedOrderOperation
    request: OrderRequest | CancelOrderRequest = field(repr=False)
    request_fingerprint: str = field(repr=False)
    authorization: object = field(repr=False)
    command: _ProviderCommand = field(repr=False)
    provider_claim: _ProviderClaim | None = field(repr=False)
    public_claim: _OpaqueClaimHandle = field(repr=False)
    dispatch_state: str = "claimed"
    response_mapping: Mapping[str, object] | None = field(default=None, repr=False)
    provider_response: object = field(default=None, repr=False)
    verified_acknowledgement: VerifiedOrderAcknowledgement | None = field(
        default=None, repr=False
    )
    verification_in_progress: bool = False


@dataclass(repr=False)
class _CorrelationRecord:
    correlation: OrderCorrelation = field(repr=False)
    issuer_reservation: object = field(repr=False)
    provider_reservation: object = field(repr=False)
    claims: dict[ManagedOrderOperation, _ClaimRecord] = field(default_factory=dict, repr=False)


class _TypedProviderAuthority:
    """Adapt the internal provider port to ManagedExecutionFacade.

    ManagedExecutionFacade remains the only dispatch caller.  This adapter
    only opens the typed provider port with the private reservation and wraps
    all provider-owned returns before they can cross the public facade.
    """

    def __init__(self, provider: TypedOrderProvider) -> None:
        self._provider = provider

    def claim(
        self,
        operation: ManagedOrderOperation,
        request: object,
        *,
        authorization: object,
    ) -> object:
        if type(request) is not _ProviderCommand or request.operation != operation:
            raise ManagedExecutionUnavailable("correlated_order_command_invalid")
        try:
            value = self._provider.claim_correlated_order(
                operation,
                request.correlation,
                request.request,
                issuer_reservation=request.issuer_reservation,
                provider_reservation=request.provider_reservation,
                authorization=authorization,
            )
        except Exception:
            # Provider claim is contractually pre-dispatch.  Its implementation
            # must never call a native write from this method.
            raise ManagedExecutionUnavailable("correlated_order_claim_rejected") from None
        if value is None or type(value) is bool:
            raise ManagedExecutionOutcomeUnknown("correlated_order_claim_outcome_unknown")
        return _ProviderClaim(
            command=request,
            authorization=authorization,
            value=value,
        )

    async def submit(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object:
        return await self._dispatch(
            "submit", request, claim=claim, authorization=authorization
        )

    async def cancel(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object:
        return await self._dispatch(
            "cancel", request, claim=claim, authorization=authorization
        )

    async def _dispatch(
        self,
        operation: ManagedOrderOperation,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object:
        if (
            type(request) is not _ProviderCommand
            or request.operation != operation
            or type(claim) is not _ProviderClaim
            or claim.command is not request
            or claim.authorization is not authorization
        ):
            raise ManagedExecutionOutcomeUnknown("correlated_order_claim_binding_invalid")
        try:
            if operation == "submit":
                if type(request.request) is not OrderRequest:
                    raise ManagedExecutionOutcomeUnknown("correlated_order_request_invalid")
                value = await self._provider.submit_correlated_order(
                    request.correlation,
                    request.request,
                    issuer_reservation=request.issuer_reservation,
                    provider_reservation=request.provider_reservation,
                    claim=claim.value,
                    authorization=authorization,
                )
            else:
                if type(request.request) is not CancelOrderRequest:
                    raise ManagedExecutionOutcomeUnknown("correlated_cancel_request_invalid")
                value = await self._provider.cancel_correlated_order(
                    request.correlation,
                    request.request,
                    issuer_reservation=request.issuer_reservation,
                    provider_reservation=request.provider_reservation,
                    claim=claim.value,
                    authorization=authorization,
                )
        except asyncio.CancelledError:
            # Cancellation during or after entry to provider dispatch leaves
            # the outcome uncertain.  The public owner latches UNKNOWN.
            raise
        except Exception:
            raise ManagedExecutionOutcomeUnknown(
                "correlated_order_dispatch_outcome_unknown"
            ) from None
        if value is None or type(value) is bool:
            raise ManagedExecutionOutcomeUnknown(
                "correlated_order_dispatch_outcome_unknown"
            )
        return _ProviderDispatchResult(command=request, value=value)


class ManagedCorrelatedOrderExecutionFacade(CorrelatedOrderExecutionFacade):
    """Route-bound, reservation-backed facade for managed typed order writes.

    ``issuer`` and ``provider`` must be selected by trusted SDK composition.
    The constructor checks only that the required call shape is present; that
    shape is not an authority proof.  Missing capabilities fail construction
    before a correlation row is allocated.  Correlations are append-only in
    the issuer and this runtime keeps a bounded, non-evicting in-memory map.
    When the limit is reached it rejects new writes rather than forgetting a
    live order binding.

    A provider bind error latches new reservations closed because the provider
    may have durably created that reservation before the error surfaced.
    Existing correlations continue to require the provider's own exact claim
    validation.  A claim or dispatch is never retried here.  A dispatch
    exception or cancellation is UNKNOWN; it cannot be followed by another
    submit/cancel on that claim.
    The facade does not restore state across process restarts.  An SDK runtime
    must recover and validate issuer/provider records before constructing a
    new capable facade; absent that recovery, this capability must remain
    unavailable.
    """

    def __init__(
        self,
        *,
        exchange_name: str,
        issuer: OrderCorrelationIssuer,
        provider: TypedOrderProvider,
        max_correlations: int = _DEFAULT_MAX_CORRELATIONS,
    ) -> None:
        _require_neutral_text(exchange_name, "exchange_name")
        if type(max_correlations) is not int or not 1 <= max_correlations <= 1_000_000:
            raise CorrelatedExecutionCompositionError("correlated_execution_capacity_invalid")
        if not _has_methods(
            issuer,
            (
                "issue_order_correlation",
                "resolve_order_correlation",
                "validate_order_correlation",
            ),
        ):
            raise CorrelatedExecutionCompositionError("order_correlation_issuer_unavailable")
        if not _has_methods(
            provider,
            (
                "bind_order_reservation",
                "resolve_order_reservation",
                "claim_correlated_order",
                "submit_correlated_order",
                "cancel_correlated_order",
                "verify_order_response",
                "verify_cancel_response",
            ),
        ):
            raise CorrelatedExecutionCompositionError("typed_order_provider_unavailable")
        self.__exchange_name = exchange_name
        self.__issuer = issuer
        self.__provider = provider
        self.__capacity = max_correlations
        self.__lock = threading.RLock()
        self.__records: dict[str, _CorrelationRecord] = {}
        self.__preparations = 0
        self.__binding_failed_closed = False
        self.__authority = _TypedProviderAuthority(provider)
        self.__managed = ManagedExecutionFacade(provider_authority=self.__authority)

    def prepare_order_correlation(self, exchange_name: str, symbol: str) -> OrderCorrelation:
        """Durably issue and provider-bind one neutral order identity."""

        _require_neutral_text(exchange_name, "exchange_name")
        _require_neutral_text(symbol, "symbol")
        if exchange_name != self.__exchange_name:
            raise ManagedExecutionUnavailable("correlated_order_route_mismatch")
        with self.__lock:
            if self.__binding_failed_closed:
                raise CorrelatedExecutionCompositionError(
                    "correlated_order_provider_binding_unavailable"
                )
            if len(self.__records) + self.__preparations >= self.__capacity:
                raise CorrelatedExecutionCompositionError(
                    "correlated_order_capacity_exhausted"
                )
            self.__preparations += 1

        provider_bind_started = False
        try:
            issued = self.__issuer.issue_order_correlation(exchange_name, symbol)
            if type(issued) is not tuple or len(issued) != 2:
                raise CorrelatedExecutionCompositionError(
                    "order_correlation_issuer_result_invalid"
                )
            correlation, issuer_reservation = issued
            if (
                type(correlation) is not OrderCorrelation
                or correlation.exchange_name != exchange_name
                or correlation.symbol != symbol
                or issuer_reservation is None
                or type(issuer_reservation) is bool
            ):
                raise CorrelatedExecutionCompositionError(
                    "order_correlation_issuer_result_invalid"
                )
            try:
                OrderCorrelation.__post_init__(correlation)
            except (TypeError, ValueError):
                raise CorrelatedExecutionCompositionError(
                    "order_correlation_issuer_result_invalid"
                ) from None
            if not _issuer_reservation_valid(
                self.__issuer, correlation, issuer_reservation
            ):
                raise CorrelatedExecutionCompositionError(
                    "order_correlation_issuer_validation_failed"
                )

            provider_bind_started = True
            try:
                provider_reservation = self.__provider.bind_order_reservation(
                    correlation, issuer_reservation=issuer_reservation
                )
            except BaseException as error:
                self.__latch_provider_binding_failure()
                if isinstance(error, Exception):
                    raise CorrelatedExecutionCompositionError(
                        "correlated_order_provider_binding_unknown"
                    ) from None
                raise
            if provider_reservation is None or type(provider_reservation) is bool:
                self.__latch_provider_binding_failure()
                raise CorrelatedExecutionCompositionError(
                    "correlated_order_provider_binding_unknown"
                )
            if not _issuer_reservation_valid(
                self.__issuer, correlation, issuer_reservation
            ):
                self.__latch_provider_binding_failure()
                raise CorrelatedExecutionCompositionError(
                    "order_correlation_issuer_validation_failed"
                )

            record = _CorrelationRecord(
                correlation=correlation,
                issuer_reservation=issuer_reservation,
                provider_reservation=provider_reservation,
            )
            with self.__lock:
                if self.__binding_failed_closed:
                    raise CorrelatedExecutionCompositionError(
                        "correlated_order_provider_binding_unavailable"
                    )
                if correlation.reservation_ref in self.__records:
                    self.__binding_failed_closed = True
                    raise CorrelatedExecutionCompositionError(
                        "order_correlation_duplicate_reservation"
                    )
                self.__records[correlation.reservation_ref] = record
            return correlation
        except CorrelatedExecutionCompositionError:
            if provider_bind_started:
                self.__latch_provider_binding_failure()
            raise
        except BaseException as error:
            if provider_bind_started:
                self.__latch_provider_binding_failure()
            if isinstance(error, Exception):
                code = (
                    "correlated_order_provider_binding_unknown"
                    if provider_bind_started
                    else "order_correlation_issuer_unavailable"
                )
                raise CorrelatedExecutionCompositionError(code) from None
            else:
                raise
        finally:
            with self.__lock:
                self.__preparations -= 1

    def claim_correlated_order(
        self,
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        request: OrderRequest | CancelOrderRequest,
        *,
        authorization: object,
    ) -> object:
        """Acquire a one-shot pre-dispatch provider claim for an exact request."""

        _require_operation(operation)
        record = self.__record_for(correlation)
        self.__validate_request(operation, correlation, request)
        request_fingerprint = _request_fingerprint(request)
        _require_opaque(authorization, "correlated_order_authorization_required")
        if not _issuer_reservation_valid(
            self.__issuer, correlation, record.issuer_reservation
        ):
            raise ManagedExecutionUnavailable("order_correlation_issuer_validation_failed")

        with self.__lock:
            self.__require_record_current(correlation, record)
            if operation in record.claims:
                raise ManagedExecutionUnavailable("correlated_order_claim_already_used")
            if operation == "submit" and record.claims:
                raise ManagedExecutionUnavailable("correlated_order_submit_already_claimed")
            if operation == "cancel":
                submit = record.claims.get("submit")
                if submit is None or submit.dispatch_state not in {
                    "dispatching",
                    "dispatch_returned",
                    "acknowledged",
                }:
                    raise ManagedExecutionUnavailable(
                        "correlated_order_cancel_target_not_submitted"
                    )
                if submit.dispatch_state == "dispatching":
                    raise ManagedExecutionUnavailable(
                        "correlated_order_submit_still_dispatching"
                    )
                if (
                    submit.verified_acknowledgement is not None
                    and submit.verified_acknowledgement.disposition != "accepted"
                ):
                    raise ManagedExecutionUnavailable(
                        "correlated_order_submit_not_accepted"
                    )

            command = _ProviderCommand(
                operation=operation,
                correlation=correlation,
                request=request,
                issuer_reservation=record.issuer_reservation,
                provider_reservation=record.provider_reservation,
            )
            # Reserve the local claim slot before provider I/O.  A concurrent
            # call cannot race a second claim while the SDK owner is waiting.
            pending = _ClaimRecord(
                operation=operation,
                request=request,
                request_fingerprint=request_fingerprint,
                authorization=authorization,
                command=command,
                provider_claim=None,
                public_claim=_OpaqueClaimHandle(),
                dispatch_state="claiming",
            )
            record.claims[operation] = pending

        try:
            provider_claim = self.__managed.claim(
                operation,
                command,
                authorization=authorization,
            )
            if type(provider_claim) is not _ProviderClaim or provider_claim.command is not command:
                raise ManagedExecutionOutcomeUnknown("correlated_order_claim_invalid")
            if _request_fingerprint(request) != request_fingerprint:
                raise ManagedExecutionOutcomeUnknown("correlated_order_request_changed")
        except ManagedExecutionUnavailable:
            with self.__lock:
                pending.dispatch_state = "claim_failed"
            raise
        except BaseException as error:
            with self.__lock:
                pending.dispatch_state = "claim_unknown"
            if isinstance(error, Exception):
                raise ManagedExecutionUnavailable(
                    "correlated_order_claim_outcome_unknown"
                ) from None
            raise

        with self.__lock:
            try:
                self.__require_record_current(correlation, record)
            except BaseException:
                pending.dispatch_state = "claim_unknown"
                raise
            pending.provider_claim = provider_claim
            pending.dispatch_state = "claimed"
        return pending.public_claim

    async def submit_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: OrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        """Dispatch the exact claimed request once through ManagedExecutionFacade."""

        return await self.__dispatch(
            "submit", correlation, request, claim=claim, authorization=authorization
        )

    async def cancel_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: CancelOrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        """Dispatch one provider-validated cancel target through the managed gate."""

        return await self.__dispatch(
            "cancel", correlation, request, claim=claim, authorization=authorization
        )

    async def __dispatch(
        self,
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        request: OrderRequest | CancelOrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]:
        record = self.__record_for(correlation)
        self.__validate_request(operation, correlation, request)
        try:
            request_fingerprint = _request_fingerprint(request)
        except Exception:
            with self.__lock:
                claim_record = record.claims.get(operation)
                if claim_record is not None and claim_record.public_claim is claim:
                    claim_record.dispatch_state = "claim_invalidated"
            raise ManagedExecutionUnavailable("correlated_order_request_invalid") from None
        claim_valid = _issuer_reservation_valid(
            self.__issuer, correlation, record.issuer_reservation
        )
        _require_opaque(authorization, "correlated_order_authorization_required")
        with self.__lock:
            self.__require_record_current(correlation, record)
            claim_record = record.claims.get(operation)
            if (
                claim_record is None
                or claim_record.dispatch_state != "claimed"
                or type(claim_record.provider_claim) is not _ProviderClaim
                or claim_record.provider_claim.command is not claim_record.command
                or claim_record.public_claim is not claim
                or claim_record.request is not request
                or claim_record.request_fingerprint != request_fingerprint
                or claim_record.authorization is not authorization
            ):
                raise ManagedExecutionUnavailable("correlated_order_claim_binding_invalid")
            if claim_valid is not True:
                claim_record.dispatch_state = "claim_invalidated"
                raise ManagedExecutionUnavailable(
                    "order_correlation_issuer_validation_failed"
                )
            claim_record.dispatch_state = "dispatching"

        try:
            if operation == "submit":
                if type(request) is not OrderRequest:
                    raise ManagedExecutionUnavailable("correlated_order_request_invalid")
                result = self.__managed.submit(
                    claim_record.command,
                    claim=claim_record.provider_claim,
                    authorization=authorization,
                )
            else:
                if type(request) is not CancelOrderRequest:
                    raise ManagedExecutionUnavailable("correlated_cancel_request_invalid")
                result = self.__managed.cancel(
                    claim_record.command,
                    claim=claim_record.provider_claim,
                    authorization=authorization,
                )
            if not inspect.isawaitable(result):
                raise ManagedExecutionOutcomeUnknown(
                    "correlated_order_dispatch_result_invalid"
                )
            provider_result = await result
            if type(provider_result) is not _ProviderDispatchResult:
                raise ManagedExecutionOutcomeUnknown(
                    "correlated_order_dispatch_result_invalid"
                )
            if provider_result.command is not claim_record.command:
                raise ManagedExecutionOutcomeUnknown(
                    "correlated_order_dispatch_binding_invalid"
                )
        except asyncio.CancelledError:
            with self.__lock:
                claim_record.dispatch_state = "dispatch_unknown"
            raise
        except BaseException as error:
            with self.__lock:
                claim_record.dispatch_state = "dispatch_unknown"
            if isinstance(error, Exception):
                raise ManagedExecutionOutcomeUnknown(
                    "correlated_order_dispatch_outcome_unknown"
                ) from None
            raise

        response_mapping = _dispatch_projection(correlation, operation)
        with self.__lock:
            claim_record.provider_response = provider_result.value
            claim_record.response_mapping = response_mapping
            claim_record.dispatch_state = "dispatch_returned"
        return response_mapping

    def verify_order_response(
        self,
        correlation: OrderCorrelation,
        response: Mapping[str, object],
    ) -> VerifiedOrderAcknowledgement | None:
        """Return an acknowledgement only from the provider's typed verifier."""

        return self.__verify("submit", correlation, response)

    def verify_cancel_response(
        self,
        correlation: OrderCorrelation,
        response: Mapping[str, object],
    ) -> VerifiedOrderAcknowledgement | None:
        """Return a cancel acknowledgement only from the provider's typed verifier."""

        return self.__verify("cancel", correlation, response)

    def __verify(
        self,
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        response: Mapping[str, object],
    ) -> VerifiedOrderAcknowledgement | None:
        try:
            record = self.__record_for(correlation)
        except ManagedExecutionUnavailable:
            return None
        with self.__lock:
            claim_record = record.claims.get(operation)
            if (
                claim_record is None
                or claim_record.dispatch_state not in {"dispatch_returned", "acknowledged"}
                or claim_record.response_mapping is not response
                or claim_record.verification_in_progress
            ):
                return None
            if claim_record.verified_acknowledgement is not None:
                return claim_record.verified_acknowledgement
            claim_record.verification_in_progress = True
            provider_response = claim_record.provider_response

        try:
            if operation == "submit":
                acknowledgement = self.__provider.verify_order_response(
                    correlation,
                    provider_response,
                    issuer_reservation=record.issuer_reservation,
                    provider_reservation=record.provider_reservation,
                )
            else:
                acknowledgement = self.__provider.verify_cancel_response(
                    correlation,
                    provider_response,
                    issuer_reservation=record.issuer_reservation,
                    provider_reservation=record.provider_reservation,
                )
        except Exception:
            acknowledgement = None
        finally:
            with self.__lock:
                claim_record.verification_in_progress = False

        if (
            type(acknowledgement) is not VerifiedOrderAcknowledgement
            or acknowledgement.correlation is not correlation
            or acknowledgement.operation != operation
        ):
            return None
        try:
            VerifiedOrderAcknowledgement.__post_init__(acknowledgement)
        except (TypeError, ValueError):
            return None
        with self.__lock:
            if claim_record.response_mapping is not response:
                return None
            claim_record.verified_acknowledgement = acknowledgement
            claim_record.dispatch_state = "acknowledged"
        return acknowledgement

    def __record_for(self, correlation: OrderCorrelation) -> _CorrelationRecord:
        if type(correlation) is not OrderCorrelation:
            raise ManagedExecutionUnavailable("order_correlation_untyped")
        try:
            OrderCorrelation.__post_init__(correlation)
        except (TypeError, ValueError):
            raise ManagedExecutionUnavailable("order_correlation_invalid") from None
        with self.__lock:
            record = self.__records.get(correlation.reservation_ref)
            if record is None or record.correlation is not correlation:
                raise ManagedExecutionUnavailable("order_correlation_unavailable")
            return record

    def __require_record_current(
        self,
        correlation: OrderCorrelation,
        record: _CorrelationRecord,
    ) -> None:
        if (
            self.__records.get(correlation.reservation_ref) is not record
            or record.correlation is not correlation
        ):
            raise ManagedExecutionUnavailable("order_correlation_unavailable")

    @staticmethod
    def __validate_request(
        operation: ManagedOrderOperation,
        correlation: OrderCorrelation,
        request: object,
    ) -> None:
        if operation == "submit":
            if type(request) is not OrderRequest:
                raise ManagedExecutionUnavailable("correlated_order_request_untyped")
        elif operation == "cancel":
            if type(request) is not CancelOrderRequest:
                raise ManagedExecutionUnavailable("correlated_cancel_request_untyped")
        else:
            raise ManagedExecutionUnavailable("correlated_order_operation_unsupported")
        if (
            request.symbol != correlation.symbol
            or request.client_order_id != correlation.client_order_id
        ):
            raise ManagedExecutionUnavailable("correlated_order_request_mismatch")

    def __latch_provider_binding_failure(self) -> None:
        with self.__lock:
            self.__binding_failed_closed = True


def _dispatch_projection(
    correlation: OrderCorrelation,
    operation: ManagedOrderOperation,
) -> Mapping[str, object]:
    """Create a neutral receipt that does not claim provider acknowledgement."""

    return MappingProxyType(
        {
            "kind": "correlated_order_dispatch",
            "exchange_name": correlation.exchange_name,
            "symbol": correlation.symbol,
            "client_order_id": correlation.client_order_id,
            "operation": operation,
            "verification": "unverified",
        }
    )


def _request_fingerprint(request: OrderRequest | CancelOrderRequest) -> str:
    """Bind all typed request fields without exposing account/native values."""

    if type(request) not in {OrderRequest, CancelOrderRequest}:
        raise ManagedExecutionUnavailable("correlated_order_request_untyped")
    try:
        payload = request.to_dict()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except Exception:
        raise ManagedExecutionUnavailable("correlated_order_request_invalid") from None
    return hashlib.sha256(encoded).hexdigest()


def _has_methods(value: object, method_names: tuple[str, ...]) -> bool:
    if value is None:
        return False
    try:
        return all(callable(getattr(value, name, None)) for name in method_names)
    except Exception:
        return False


def _issuer_reservation_valid(
    issuer: OrderCorrelationIssuer,
    correlation: OrderCorrelation,
    issuer_reservation: object,
) -> bool:
    try:
        return (
            issuer.validate_order_correlation(
                correlation,
                issuer_reservation=issuer_reservation,
            )
            is True
        )
    except Exception:
        return False


def _require_neutral_text(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ManagedExecutionUnavailable("correlated_order_{}_invalid".format(field_name))
    try:
        if len(value.encode("utf-8", errors="strict")) > 256:
            raise ManagedExecutionUnavailable(
                "correlated_order_{}_invalid".format(field_name)
            )
    except UnicodeEncodeError:
        raise ManagedExecutionUnavailable(
            "correlated_order_{}_invalid".format(field_name)
        ) from None


def _require_operation(operation: object) -> None:
    if type(operation) is not str or operation not in {"submit", "cancel"}:
        raise ManagedExecutionUnavailable("correlated_order_operation_unsupported")


def _require_opaque(value: object, code: str) -> None:
    if value is None or type(value) is bool:
        raise ManagedExecutionUnavailable(code)


def _same_selected_route(current: object, expected: SelectedExecutionRoute) -> bool:
    if type(current) is not SelectedExecutionRoute:
        return False
    try:
        SelectedExecutionRoute.__post_init__(current)
    except Exception:
        return False
    return (
        current.exchange_name == expected.exchange_name
        and current.client is expected.client
        and current.environment == expected.environment
        and current.execution_context is expected.execution_context
    )


def _capacity_reason_from_route_error(code: str) -> str:
    if code == "typed_order_route_resolution_failed":
        return "order_capacity_route_resolution_failed"
    if code == "typed_order_route_unavailable":
        return "order_capacity_route_unavailable"
    return "order_capacity_route_unregistered"


def _capacity_reason_from_provider_error(code: str | None) -> str:
    if code in {
        "typed_order_provider_capability_unavailable",
        "typed_order_provider_entry_point_conflict",
        "typed_order_provider_capability_conflict",
        "typed_order_provider_registration_scope_invalid",
    }:
        return "order_capacity_route_unregistered"
    if code in {
        "typed_order_provider_discovery_failed",
        "typed_order_provider_import_failed",
        "typed_order_provider_entry_point_invalid",
        "typed_order_provider_registration_failed",
        "typed_order_provider_factory_failed",
    }:
        return "order_capacity_factory_failed"
    if code == "typed_order_route_changed":
        return "order_capacity_route_unavailable"
    if code == "typed_order_provider_invalid":
        return "order_capacity_projection_invalid"
    return "order_capacity_provider_unavailable"


def _finite_provider_error_code(code: object) -> str:
    allowed = {
        "typed_order_provider_entry_point_conflict",
        "typed_order_provider_capability_unavailable",
        "typed_order_provider_capability_conflict",
        "typed_order_provider_discovery_failed",
        "typed_order_provider_entry_point_invalid",
        "typed_order_provider_import_failed",
        "typed_order_provider_registration_failed",
        "typed_order_provider_registration_scope_invalid",
    }
    if type(code) is str and code in allowed:
        return code
    return "typed_order_provider_factory_failed"


def _typed_order_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=TYPED_ORDER_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(TYPED_ORDER_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == TYPED_ORDER_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        raise CorrelatedExecutionCompositionError(
            "typed_order_provider_discovery_failed"
        ) from None
    selected = []
    for entry_point in candidates:
        try:
            name = getattr(entry_point, "name", None)
        except Exception:
            raise CorrelatedExecutionCompositionError(
                "typed_order_provider_entry_point_invalid"
            ) from None
        if name == exchange_name:
            selected.append(entry_point)
    return tuple(selected)


def _typed_order_entry_point_identity(
    entry_point: object,
    exchange_name: str,
) -> tuple[str, str, str]:
    try:
        value = getattr(entry_point, "value", None)
        name = getattr(entry_point, "name", None)
        distribution = getattr(entry_point, "dist", None)
        distribution_name = (
            getattr(distribution, "name", "") if distribution is not None else ""
        )
    except Exception:
        raise CorrelatedExecutionCompositionError(
            "typed_order_provider_entry_point_invalid"
        ) from None
    if (
        type(value) is not str
        or not value
        or type(name) is not str
        or name != exchange_name
        or type(distribution_name) is not str
    ):
        raise CorrelatedExecutionCompositionError(
            "typed_order_provider_entry_point_invalid"
        )
    return exchange_name, value, distribution_name
