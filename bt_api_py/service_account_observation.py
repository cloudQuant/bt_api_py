"""Validate a service account observation against caller-selected scope.

The supplied position callback checks the consuming application's local
bindings. This function does not issue an observation or establish provider
authority, freshness, valuation, or execution permission.
"""

from __future__ import annotations

from typing import Any, Callable

from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountOpenOrder,
    AccountPosition,
    AccountResourceKey,
)


def validate_service_account_client(account_client: Any) -> tuple[AccountKey, tuple[AccountResourceKey, ...]]:
    """Validate an injected account facade's declared scope, without proving it."""
    if not callable(getattr(account_client, "refresh", None)) or not callable(
        getattr(account_client, "snapshot", None)
    ):
        raise TypeError("account_client must provide refresh() and snapshot()")
    if not callable(getattr(account_client, "close", None)):
        raise TypeError("account_client must provide close()")
    if getattr(account_client, "require_open_orders", None) is not True:
        raise ValueError("account_client must require complete open orders")
    expected_account = getattr(account_client, "expected_account", None)
    if type(expected_account) is not AccountKey:
        raise TypeError("account_client.expected_account must be an AccountKey")
    coverage = getattr(account_client, "required_coverage", None)
    if type(coverage) is not tuple or not coverage:
        raise TypeError("account_client.required_coverage must be a non-empty tuple")
    if any(type(item) is not AccountResourceKey for item in coverage):
        raise TypeError("account coverage entries must be AccountResourceKey values")
    if len(set(coverage)) != len(coverage):
        raise ValueError("account coverage must not contain duplicates")
    return expected_account, coverage


def read_current_service_account_observation(
    account_client: Any, *, validate_observation: Callable[[Any], None]
) -> AccountObservation:
    """Read the current snapshot, refreshing only when snapshot access fails."""
    try:
        view = account_client.snapshot()
    except Exception:
        view = account_client.refresh()
    observation = getattr(view, "observation", None)
    validate_observation(observation)
    return observation


def service_account_valuation(observation: Any, *, currency: str, error_type: type[Exception]):
    """Return explicit same-currency valuation values without proving their source."""
    valuation = observation.valuation
    if (
        valuation is None
        or valuation.currency != currency
        or valuation.available_cash is None
        or valuation.equity is None
    ):
        raise error_type("VALUATION_UNAVAILABLE")
    return valuation.available_cash, valuation.equity


def service_account_positions(observation: Any, *, error_type: type[Exception]):
    """Return the supplied positions only when the observation marks them complete."""
    if not observation.positions_complete:
        raise error_type("POSITIONS_INCOMPLETE")
    return observation.positions


def require_service_account_position(position: Any, *, error_type: type[Exception]) -> None:
    """Require one exact neutral position record."""
    if type(position) is not AccountPosition:
        raise error_type("POSITION_INVALID")


def service_account_open_orders(observation: Any, *, error_type: type[Exception]):
    """Return the supplied open orders only when marked complete."""
    if observation.open_orders is None or not observation.open_orders_complete:
        raise error_type("OPEN_ORDERS_INCOMPLETE")
    return observation.open_orders


def require_service_account_open_order(order: Any, *, error_type: type[Exception]) -> None:
    """Require one exact neutral open-order record."""
    if type(order) is not AccountOpenOrder:
        raise error_type("OPEN_ORDER_INVALID")


def require_service_account_quantity_unit(
    actual: Any, expected: Any, *, error_type: type[Exception], code: str
) -> None:
    """Require a caller's configured quantity unit to match the neutral record."""
    if actual != expected:
        raise error_type(code)


def validate_service_account_observation(
    observation: Any,
    *,
    expected_account: Any,
    required_coverage: Any,
    valuation_currency: str,
    validate_position: Callable[[Any, Any], Any],
    error_type: type[Exception],
) -> None:
    """Require neutral account identity, coverage, completeness and currency."""
    if type(observation) is not AccountObservation:
        raise error_type("ACCOUNT_OBSERVATION_INVALID")
    if observation.account_key != expected_account:
        raise error_type("ACCOUNT_IDENTITY_MISMATCH")
    if set(observation.coverage) != required_coverage:
        raise error_type("ACCOUNT_COVERAGE_MISMATCH")
    if observation.balances_complete is not True:
        raise error_type("BALANCES_INCOMPLETE")
    if observation.positions_complete is not True:
        raise error_type("POSITIONS_INCOMPLETE")
    for item in observation.positions:
        validate_position(item.resource, item.instrument)
    if observation.valuation is not None and observation.valuation.currency != valuation_currency:
        raise error_type("VALUATION_CURRENCY_MISMATCH")


__all__ = (
    "read_current_service_account_observation",
    "require_service_account_open_order",
    "require_service_account_position",
    "require_service_account_quantity_unit",
    "service_account_open_orders",
    "service_account_positions",
    "service_account_valuation",
    "validate_service_account_client",
    "validate_service_account_observation",
)
