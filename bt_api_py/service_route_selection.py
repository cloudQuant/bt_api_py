"""SDK-neutral route and account-scope selection for service clients.

Selection checks supplied bindings; it does not grant capabilities
or prove the provider, account, or session is current.
"""

from __future__ import annotations

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


__all__ = ("strategy_account_book_scope", "unique_active_route")
