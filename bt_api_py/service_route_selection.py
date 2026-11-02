"""SDK-neutral route and account-scope selection for service clients.

Selection checks supplied bindings; it does not grant capabilities
or prove the provider, account, or session is current.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def strategy_account_book_scope(binding: Any, wire_scope_type: type, *, error_type: type) -> Any:
    scope = getattr(binding, "scope", None)
    if type(scope) is not wire_scope_type:
        raise error_type("ACCOUNT_BOOK_SCOPE_INVALID")
    for name in ("strategy_ref", "account_ref"):
        value = getattr(scope, name, None)
        if type(value) is not str or not value or value != value.strip():
            raise error_type("ACCOUNT_BOOK_SCOPE_INVALID")
    if any(
        getattr(scope, name, None)
        for name in ("account_refs", "manual_book_ref", "portfolio_ref", "group_id", "leg_id")
    ):
        raise error_type("ACCOUNT_BOOK_SCOPE_INVALID")
    return scope


def unique_active_route(
    bindings: tuple[Any, ...], mount: str, scope_ref: str, operations: frozenset[str], *, error_type: type
) -> Any:
    matches = tuple(
        binding
        for binding in bindings
        if getattr(binding, "active", False) is True
        and getattr(binding, "mount", None) == mount
        and getattr(binding, "scope_ref", None) == scope_ref
    )
    if len(matches) != 1:
        raise error_type("RUNTIME_ROUTE_MISSING_OR_AMBIGUOUS")
    binding = matches[0]
    if getattr(binding, "required", None) is not True:
        raise error_type("RUNTIME_ROUTE_NOT_REQUIRED")
    granted = getattr(binding, "operations", None)
    if type(granted) not in (frozenset, set) or not operations <= granted:
        raise error_type("RUNTIME_ROUTE_OPERATION_MISSING")
    return binding


def coerce_strategy_group_routes(
    primary_runtime: Any,
    routes: tuple[Any, ...],
    runtime_type: type,
    *,
    error_type: type,
    runtime_has_external_owner,
) -> tuple[Any, ...]:
    if type(routes) is not tuple:
        raise error_type("INVALID_GROUP_ROUTES")
    if not routes:
        return ()

    try:
        from bt_api_py.service_contracts import WireIdentity, WireRole
        from bt_api_py.service_group_rpc_client import ServiceGroupRoute
    except ImportError as error:
        raise error_type("GROUP_SDK_UNAVAILABLE") from error

    seen_keys = set()
    runtime_modes = {id(primary_runtime): True}
    runtime_strategies = {}
    for route in routes:
        if type(route) is not ServiceGroupRoute:
            raise error_type("INVALID_GROUP_ROUTE")
        if (
            any(
                type(value) is not str or not value or value != value.strip()
                for value in (
                    route.strategy_ref,
                    route.portfolio_ref,
                    route.group_scope_ref,
                    route.monitor_scope_ref,
                )
            )
            or type(route.account_refs) is not tuple
            or not route.account_refs
            or any(
                type(value) is not str or not value or value != value.strip()
                for value in route.account_refs
            )
            or tuple(sorted(set(route.account_refs))) != route.account_refs
            or type(route.owned) is not bool
            or not isinstance(route.runtime, runtime_type)
        ):
            raise error_type("INVALID_GROUP_ROUTE")
        key = (route.strategy_ref, route.portfolio_ref)
        if key in seen_keys:
            raise error_type("GROUP_ROUTE_AMBIGUOUS")
        seen_keys.add(key)

        identity = getattr(route.runtime, "identity", None)
        if (
            type(identity) is not WireIdentity
            or identity.role is not WireRole.STRATEGY_CLIENT
            or identity.actor_ref != route.strategy_ref
        ):
            raise error_type("GROUP_RUNTIME_STRATEGY_MISMATCH")
        previous_strategy = runtime_strategies.get(id(route.runtime))
        if previous_strategy is not None and previous_strategy != route.strategy_ref:
            raise error_type("GROUP_RUNTIME_STRATEGY_AMBIGUOUS")
        runtime_strategies[id(route.runtime)] = route.strategy_ref

        runtime_id = id(route.runtime)
        previous_mode = runtime_modes.get(runtime_id)
        if previous_mode is not None and previous_mode != route.owned:
            raise error_type("RUNTIME_OWNERSHIP_MODE_MISMATCH")
        runtime_modes[runtime_id] = route.owned
        if route.runtime is primary_runtime:
            if not route.owned:
                raise error_type("PRIMARY_RUNTIME_MUST_BE_OWNED")
            continue
        expected_state = "new" if route.owned else "registered"
        if route.runtime.state != expected_state:
            raise error_type("GROUP_RUNTIME_STATE_INVALID")
        if not route.owned and runtime_has_external_owner(route.runtime):
            raise error_type("GROUP_RUNTIME_ALREADY_OWNED")
        try:
            route.runtime.next_maintenance_delay()
        except Exception:
            raise error_type("WRONG_RUNTIME_THREAD") from None
    return routes


@dataclass(frozen=True)
class ServiceStrategyAccountBookRoute:
    """One explicit strategy/account read route for a managed client run.

    ``owned=True`` transfers this runtime's start, renewal and close lifecycle
    to the session. A borrowed runtime must already be registered; its external
    owner remains responsible for renewal and close on the creating thread.
    The primary execution runtime in the SDK assembly is always owned by that
    assembly.
    """

    strategy_ref: str
    account_ref: str
    runtime: Any
    mount: str
    scope_ref: str
    owned: bool = True


def coerce_strategy_account_book_routes(
    primary_runtime: Any,
    routes: tuple[ServiceStrategyAccountBookRoute, ...],
    runtime_type: type,
    *, error_type: type, runtime_has_external_owner,
) -> tuple[ServiceStrategyAccountBookRoute, ...]:
    if type(routes) is not tuple:
        raise error_type("INVALID_ACCOUNT_BOOK_ROUTES")
    seen_pairs = set()
    runtime_modes = {}
    for route in routes:
        if type(route) is not ServiceStrategyAccountBookRoute:
            raise error_type("INVALID_ACCOUNT_BOOK_ROUTE")
        if any(
            type(value) is not str or not value or value != value.strip()
            for value in (
                route.strategy_ref,
                route.account_ref,
                route.mount,
                route.scope_ref,
            )
        ):
            raise error_type("INVALID_ACCOUNT_BOOK_ROUTE")
        if type(route.owned) is not bool or not isinstance(route.runtime, runtime_type):
            raise error_type("INVALID_ACCOUNT_BOOK_ROUTE")
        pair = (route.strategy_ref, route.account_ref)
        if pair in seen_pairs:
            raise error_type("ACCOUNT_BOOK_ROUTE_AMBIGUOUS")
        seen_pairs.add(pair)

        runtime = route.runtime
        runtime_id = id(runtime)
        previous_mode = runtime_modes.get(runtime_id)
        if previous_mode is not None and previous_mode != route.owned:
            raise error_type("ACCOUNT_BOOK_RUNTIME_OWNERSHIP_MISMATCH")
        runtime_modes[runtime_id] = route.owned
        if runtime is primary_runtime:
            if not route.owned:
                raise error_type("PRIMARY_RUNTIME_MUST_BE_OWNED")
            continue
        expected_state = "new" if route.owned else "registered"
        if runtime.state != expected_state:
            raise error_type("ACCOUNT_BOOK_RUNTIME_STATE_INVALID")
        if not route.owned and runtime_has_external_owner(runtime):
            raise error_type("ACCOUNT_BOOK_RUNTIME_ALREADY_OWNED")
        try:
            runtime.next_maintenance_delay()
        except Exception:
            raise error_type("WRONG_RUNTIME_THREAD") from None
    return routes


def unique_owned_runtimes(
    primary_runtime: Any,
    routes: tuple[Any, ...],
    *, error_type: type,
) -> tuple[Any, ...]:
    ordered = [primary_runtime]
    seen = {id(primary_runtime)}
    modes = {id(primary_runtime): True}
    for route in routes:
        runtime_id = id(route.runtime)
        previous_mode = modes.get(runtime_id)
        if previous_mode is not None and previous_mode != route.owned:
            raise error_type("RUNTIME_OWNERSHIP_MODE_MISMATCH")
        modes[runtime_id] = route.owned
        if route.owned and runtime_id not in seen:
            ordered.append(route.runtime)
            seen.add(runtime_id)
    return tuple(ordered)


def resolve_strategy_account_book_routes(
    primary_runtime, query_mount, query_scope_ref, routes, *, error_type: type,
):
    """Resolve registered neutral routes to their exact runtime and scope.

    This validates the supplied service bindings, not provider/account facts
    or a framework Strategy object's ownership.
    """
    from bt_api_py.service_contracts import WireScope

    operation = frozenset({"execution.account_books.query"})
    resolved = {}
    try:
        primary_bindings = primary_runtime.bindings
    except Exception:
        raise error_type("RUNTIME_BINDINGS_UNAVAILABLE") from None
    if type(primary_bindings) is not tuple:
        raise error_type("RUNTIME_BINDINGS_INVALID")
    try:
        primary_binding = unique_active_route(
            primary_bindings, query_mount, query_scope_ref, operation, error_type=error_type,
        )
    except error_type as error:
        if error.code not in {
            "RUNTIME_ROUTE_OPERATION_MISSING",
            "RUNTIME_ROUTE_MISSING_OR_AMBIGUOUS",
        }:
            raise
    else:
        scope = strategy_account_book_scope(primary_binding, WireScope, error_type=error_type)
        resolved[(scope.strategy_ref, scope.account_ref)] = (
            primary_runtime, query_mount, query_scope_ref,
        )

    for route in routes:
        if route.runtime.state != "registered":
            raise error_type("ACCOUNT_BOOK_RUNTIME_NOT_REGISTERED")
        try:
            bindings = route.runtime.bindings
        except Exception:
            raise error_type("ACCOUNT_BOOK_RUNTIME_BINDINGS_UNAVAILABLE") from None
        if type(bindings) is not tuple:
            raise error_type("RUNTIME_BINDINGS_INVALID")
        try:
            binding = unique_active_route(
                bindings, route.mount, route.scope_ref, operation, error_type=error_type,
            )
        except error_type as error:
            raise error_type("ACCOUNT_BOOK_ROUTE_UNAVAILABLE") from error
        scope = strategy_account_book_scope(binding, WireScope, error_type=error_type)
        if scope.strategy_ref != route.strategy_ref or scope.account_ref != route.account_ref:
            raise error_type("ACCOUNT_BOOK_SCOPE_MISMATCH")
        key = (route.strategy_ref, route.account_ref)
        if key in resolved:
            raise error_type("ACCOUNT_BOOK_ROUTE_AMBIGUOUS")
        resolved[key] = (route.runtime, route.mount, route.scope_ref)
    return resolved


__all__ = (
    "strategy_account_book_scope", "unique_active_route", "coerce_strategy_group_routes",
    "ServiceStrategyAccountBookRoute", "coerce_strategy_account_book_routes",
    "unique_owned_runtimes", "resolve_strategy_account_book_routes",
)
