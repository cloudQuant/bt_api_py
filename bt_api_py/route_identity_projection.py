"""Provider-neutral projections of explicitly configured SDK route names.

These projections do not establish provider, account, or write authority.
The caller owns its lifecycle and can supply its own boundary error type.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping


def write_route_names(routes, *, error_type, error_message="SDK route identity is unavailable"):
    """Return normalized exchange roots from route keys or route values."""
    try:
        if isinstance(routes, Mapping):
            routes = routes.keys()
        if isinstance(routes, str):
            routes = (routes,)
        return {
            str(route or "").strip().upper().partition("___")[0]
            for route in routes
            if str(route or "").strip()
        }
    except Exception as exc:
        raise error_type(error_message) from exc


def write_route_keys(routes, *, error_type, error_message="SDK route identity is unavailable"):
    """Return normalized full route keys for snapshot comparison."""
    try:
        if isinstance(routes, Mapping):
            routes = routes.keys()
        if isinstance(routes, str):
            routes = (routes,)
        return {
            str(route or "").strip().upper()
            for route in routes
            if str(route or "").strip()
        }
    except Exception as exc:
        raise error_type(error_message) from exc


def facade_exchange_names(api, *, error_type):
    """Read the facade's public route-name projection without config values."""
    list_exchanges = getattr(api, "list_exchanges", None)
    if not callable(list_exchanges) or inspect.iscoroutinefunction(list_exchanges):
        raise error_type(
            "The bt_api_py facade does not expose synchronous list_exchanges()"
        )
    try:
        names = list_exchanges()
    except Exception:
        raise error_type(
            "The bt_api_py facade exchange-name projection is unavailable"
        ) from None
    if type(names) is not list or any(
        type(name) is not str or not name.strip() for name in names
    ):
        raise error_type(
            "The bt_api_py facade exchange-name projection is malformed"
        )
    return list(names)


def select_exact_reconciliation_route(live_names, configured_names, exchange_name, *, error_type):
    """Select one exact configured route without merging account scopes."""
    if (
        not live_names
        or len(set(live_names)) != len(live_names)
        or any(name != name.strip() for name in live_names)
        or set(live_names) != set(configured_names)
    ):
        raise error_type("SDK reconciliation route-name set is unavailable")
    if exchange_name is None:
        if len(live_names) != 1:
            raise error_type("multi-route reconciliation requires a coherent SDK snapshot")
        return live_names[0]
    if type(exchange_name) is not str or exchange_name not in live_names:
        raise error_type("SDK reconciliation route is not an exact configured key")
    return exchange_name


def select_exact_live_route(live_names, exchange_name, *, error_type):
    """Select one exact named live route; route selection is not authority."""
    if type(exchange_name) is not str or not exchange_name or exchange_name != exchange_name.strip():
        raise ValueError("exchange_name must be non-empty trimmed text")
    exact = [name for name in live_names if type(name) is str and name == exchange_name]
    if len(exact) != 1:
        raise error_type("SDK exact route is missing or ambiguous")
    return exact[0]


def assert_route_sets_current(
    *, expected, snapshot, snapshot_keys, live_routes, live_keys,
    require_live_route, api_present, lazy_owned_api_pending, error_type,
):
    """Check supplied route sets without deriving session or account authority."""
    if require_live_route and not lazy_owned_api_pending and (not api_present or not live_routes):
        raise error_type(
            "Store/API route identity is unavailable; write dispatch is disabled"
        )
    if live_routes is not None:
        if (snapshot_keys and snapshot_keys != live_keys) or (snapshot and snapshot != live_routes):
            raise error_type("Store/API route identity changed; write dispatch is disabled")
        if expected and expected != live_routes:
            raise error_type("Store/API route identity mismatch; write dispatch is disabled")
    elif require_live_route and api_present:
        raise error_type("Store/API route identity is unavailable; write dispatch is disabled")
    elif snapshot and expected and snapshot != expected:
        raise error_type("Store/API route identity mismatch; write dispatch is disabled")


__all__ = (
    "assert_route_sets_current",
    "facade_exchange_names",
    "select_exact_live_route",
    "select_exact_reconciliation_route",
    "write_route_keys",
    "write_route_names",
)
