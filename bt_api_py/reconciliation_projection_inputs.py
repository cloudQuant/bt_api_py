"""Validate SDK reconciliation projection inputs without owning BT state.

The injected route callback checks the caller's current local binding. The
provider remains responsible for evidence provenance and lease validation.
"""

from __future__ import annotations

from decimal import Decimal
import math
import time


def valid_reconciliation_text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def valid_reconciliation_decimal(value: object, *, positive: bool = False) -> bool:
    return type(value) is Decimal and value.is_finite() and (not positive or value > 0)


def reconciliation_order_identity(row):
    """Extract neutral order identities without asserting their provenance."""
    order = getattr(row, "order", None)
    client_order_id = getattr(row, "client_order_id", None)
    intent_ref = getattr(row, "intent_ref", None)
    if (
        order is None
        or not valid_reconciliation_text(client_order_id)
        or not valid_reconciliation_text(intent_ref)
    ):
        return None, "reconciliation_order_projection_invalid"
    symbol = getattr(order, "symbol", None)
    instrument_ref = getattr(order, "instrument_ref", None)
    order_id = getattr(order, "order_id", None)
    if (
        not valid_reconciliation_text(symbol)
        or not valid_reconciliation_text(instrument_ref)
        or not valid_reconciliation_text(order_id)
    ):
        return None, "reconciliation_order_identity_invalid"
    return (order, client_order_id, intent_ref, symbol, instrument_ref, order_id), None


def valid_reconciliation_order_facts(
    status, terminal_confirmed, side, quantity, filled_quantity, quantity_unit, price, average_price
):
    """Check neutral order fields without asserting their provider provenance."""
    return not (
        status
        not in {
            "open",
            "partially_filled",
            "completed",
            "canceled",
            "rejected",
            "expired",
        }
        or type(terminal_confirmed) is not bool
        or terminal_confirmed != (status in {"completed", "canceled", "rejected", "expired"})
        or side not in {"buy", "sell"}
        or not valid_reconciliation_decimal(quantity, positive=True)
        or not valid_reconciliation_decimal(filled_quantity)
        or filled_quantity < 0
        or filled_quantity > quantity
        or not valid_reconciliation_text(quantity_unit)
        or (price is not None and not valid_reconciliation_decimal(price))
        or (average_price is not None and not valid_reconciliation_decimal(average_price))
    )


def valid_reconciliation_fill_economics(quantity, price, commission):
    """Check neutral fill quantities and commission fields only."""
    return not (
        not valid_reconciliation_decimal(quantity, positive=True)
        or not valid_reconciliation_decimal(price, positive=True)
        or (
            commission is not None
            and (
                getattr(commission, "unit", None) != "currency"
                or not valid_reconciliation_text(getattr(commission, "currency", None))
                or not valid_reconciliation_decimal(getattr(commission, "amount", None))
            )
        )
    )


def valid_reconciliation_fill_binding(fill, parent):
    """Compare a fill with its neutral parent-order facts."""
    return not (
        getattr(fill, "client_order_id", None) != parent["client_order_id"]
        or getattr(fill, "intent_ref", None) != parent["intent_ref"]
        or getattr(fill, "symbol", None) != parent["order"].symbol
        or getattr(fill, "side", None) != parent["order"].side
        or getattr(fill, "quantity_unit", None) != parent["order"].quantity_unit
    )


def valid_reconciliation_position_side(position_side):
    """Check the neutral position-side vocabulary."""
    return position_side in {"net", "long", "short"}


def valid_reconciliation_position_economics(position_side, quantity, quantity_unit, average_price):
    """Check neutral position quantity, unit, and price fields."""
    return not (
        not valid_reconciliation_decimal(quantity)
        or (position_side != "net" and quantity < 0)
        or not valid_reconciliation_text(quantity_unit)
        or (average_price is not None and not valid_reconciliation_decimal(average_price))
    )


def reconciliation_projection_inputs(api, route, facade, result, verified, facade_is_current):
    from bt_api_py.facade_protocol import ReconciliationResult

    if type(result) is not ReconciliationResult:
        return None, "reconciliation_result_invalid"
    if (
        result.complete is not True
        or result.safe_to_resume is not True
        or result.evidence_verified is not True
        or result.lease_current is not True
        or result.position_consistency_verified is not True
    ):
        return None, "reconciliation_evidence_incomplete"
    if type(verified) is not bool or verified is not True:
        return None, "reconciliation_sdk_verifier_rejected"
    try:
        current = facade_is_current(api, route, facade)
    except Exception:
        current = False
    if type(current) is not bool or current is not True:
        return None, "reconciliation_session_or_route_changed"

    evidence = result.evidence
    if evidence is None:
        return None, "reconciliation_evidence_missing"
    orders = getattr(evidence, "orders", None)
    fills = getattr(evidence, "fills", None)
    positions = getattr(evidence, "positions", None)
    unresolved_orders = getattr(evidence, "unresolved_orders", None)
    unresolved_fills = getattr(evidence, "unresolved_fills", None)
    unresolved_positions = getattr(evidence, "unresolved_positions", None)
    if (
        type(orders) not in (tuple, list)
        or type(fills) not in (tuple, list)
        or type(positions) not in (tuple, list)
        or unresolved_orders not in ((), [])
        or unresolved_fills not in ((), [])
        or unresolved_positions not in ((), [])
    ):
        return None, "reconciliation_rows_incomplete"

    consistency_token = result.consistency_token
    snapshot_id = result.snapshot_id
    if (
        type(consistency_token) is not str
        or not consistency_token
        or type(snapshot_id) is not str
        or not snapshot_id
    ):
        return None, "reconciliation_proof_incomplete"

    return (orders, fills, positions, consistency_token, snapshot_id), None


def _is_sha256_hex(value):
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def has_clean_execution_projection(result):
    """Check a neutral legacy projection; do not assert its provenance."""
    if not isinstance(result, dict):
        return False
    if (
        result.get("evidence_complete") is not True
        or result.get("evidence_errors")
        or result.get("error_code")
    ):
        return False
    configured_value = result.get("configured_venues")
    reconciled_value = result.get("reconciled_venues")
    if type(configured_value) is not list or type(reconciled_value) is not list:
        return False
    configured = {str(item) for item in configured_value if item}
    reconciled = {str(item) for item in reconciled_value if item}
    if (
        not configured
        or len(configured) != len(configured_value)
        or len(reconciled) != len(reconciled_value)
        or reconciled != configured
    ):
        return False
    execution_summary = result.get("execution_summary")
    if not isinstance(execution_summary, dict):
        return False
    try:
        active_orders = execution_summary["active_orders"]
        generation = result["generation"]
        session_generation = result["session_generation"]
        summary_generation = execution_summary["generation"]
        summary_session_generation = execution_summary["session_generation"]
        fencing_epoch = result["fencing_epoch"]
        summary_fencing_epoch = execution_summary["fencing_epoch"]
        as_of_monotonic_ns = result["as_of_monotonic_ns"]
        summary_as_of_monotonic_ns = execution_summary["as_of_monotonic_ns"]
    except KeyError:
        return False
    exact_positive_ints = (
        generation,
        session_generation,
        summary_generation,
        summary_session_generation,
        fencing_epoch,
        summary_fencing_epoch,
        as_of_monotonic_ns,
        summary_as_of_monotonic_ns,
    )
    if any(type(value) is not int or value <= 0 for value in exact_positive_ints):
        return False
    if (
        type(active_orders) is not int
        or active_orders != 0
        or generation != session_generation
        or generation != summary_generation
        or generation != summary_session_generation
        or fencing_epoch != summary_fencing_epoch
        or as_of_monotonic_ns > time.monotonic_ns()
        or summary_as_of_monotonic_ns > as_of_monotonic_ns
        or execution_summary.get("session_enabled") is not True
        or execution_summary.get("evidence_complete") is not True
        or execution_summary.get("evidence_errors")
        or execution_summary.get("error_code")
    ):
        return False
    reconciliation_errors = execution_summary.get("reconciliation_errors")
    if type(reconciliation_errors) is not dict or reconciliation_errors:
        return False
    for key in ("unknown_ids", "fee_unresolved_orders", "funding_unresolved_orders"):
        value = execution_summary.get(key)
        if type(value) is not list or value:
            return False
    if type(result.get("unknown_ids")) is not list or result["unknown_ids"]:
        return False
    if (
        result.get("trading_blocked") is not False
        or execution_summary.get("trading_blocked") is not False
    ):
        return False
    open_orders = result.get("open_orders")
    positions = result.get("positions")
    if type(open_orders) is not list or open_orders:
        return False
    if type(positions) is not list:
        return False
    identity_hash = result.get("identity_binding_sha256")
    summary_identity_hash = execution_summary.get("identity_binding_sha256")
    return _is_sha256_hex(identity_hash) and identity_hash == summary_identity_hash


def has_flat_execution_projection(result, *, is_clean, zero_tolerance):
    """Check flat neutral positions after the caller's clean-execution predicate."""
    if not is_clean(result):
        return False
    positions = result.get("positions")
    if type(positions) is not list:
        return False
    for row in positions:
        if not isinstance(row, dict) or "quantity" not in row:
            return False
        if "quantity_known" in row and row.get("quantity_known") is not True:
            return False
        value = row["quantity"]
        if isinstance(value, bool):
            return False
        try:
            quantity = float(value)
            if not math.isfinite(quantity) or abs(quantity) > zero_tolerance:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    return type(result.get("open_orders")) is list


__all__ = (
    "has_clean_execution_projection",
    "has_flat_execution_projection",
    "reconciliation_order_identity",
    "reconciliation_projection_inputs",
    "valid_reconciliation_decimal",
    "valid_reconciliation_fill_binding",
    "valid_reconciliation_fill_economics",
    "valid_reconciliation_order_facts",
    "valid_reconciliation_position_economics",
    "valid_reconciliation_position_side",
    "valid_reconciliation_text",
)
