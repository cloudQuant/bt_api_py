"""Legacy neutral broker-event envelope projection for SDK consumers.

Provider adapters remain responsible for interpreting native payloads. This
module preserves the existing generic envelope flattening only; it does not
authenticate, normalize, or establish the provenance of any event.
"""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


_INSTRUMENT_IDENTITY_FIELDS = (
    "provider", "venue", "environment", "product_type", "instrument_id", "native_symbol",
)


def verified_submit_ack_error(response):
    """Classify an already verified neutral submit envelope.

    The caller must separately establish the response's provenance; the marker
    in a mapping is not itself an acknowledgement or execution proof.
    """
    if not isinstance(response, Mapping):
        return "order_ack_unverified", "SDK acknowledgement is unavailable"
    if (
        response.get("kind") != "verified_order_ack"
        or response.get("verified_order_ack") is not True
        or response.get("operation") != "submit"
    ):
        return "order_ack_unverified", "SDK acknowledgement is not verified"
    if response.get("status") == "accepted":
        return None
    if response.get("status") == "rejected":
        return "remote_submit_rejected", "The SDK verified that the order was rejected"
    return "order_ack_invalid", "SDK acknowledgement has an unsupported disposition"


def verified_submit_rejection_code(response):
    """Return the existing bounded rejection code from a neutral envelope."""
    if (
        not isinstance(response, Mapping)
        or response.get("kind") != "verified_order_ack"
        or response.get("verified_order_ack") is not True
        or response.get("operation") != "submit"
        or response.get("status") != "rejected"
    ):
        return None
    value = response.get("error_code")
    if type(value) is str and 0 < len(value) <= 128:
        if all(character.isalnum() or character in "._:-" for character in value):
            return value
    return None


def neutral_instrument_fields(instrument):
    """Project the existing neutral instrument-key shape without proving identity."""
    if isinstance(instrument, Mapping):
        if set(instrument) != set(_INSTRUMENT_IDENTITY_FIELDS):
            return None
        values = {name: instrument.get(name) for name in _INSTRUMENT_IDENTITY_FIELDS}
    else:
        values = {name: getattr(instrument, name, None) for name in _INSTRUMENT_IDENTITY_FIELDS}
    if any(
        type(value) is not str or not value or value != value.strip()
        for value in values.values()
    ):
        return None
    return values


def neutral_instrument_identity(instrument):
    """Return the legacy case-folded comparison key for a neutral instrument."""
    fields = neutral_instrument_fields(instrument)
    if fields is None:
        return None
    return tuple(fields[name].casefold() for name in _INSTRUMENT_IDENTITY_FIELDS)



def resolve_neutral_position_route(item, alias_map):
    """Resolve a neutral position row only through its typed instrument key."""
    if not alias_map:
        return None
    instrument_key = (
        item.get("instrument")
        if isinstance(item, Mapping)
        else getattr(item, "instrument", None)
    )
    if instrument_key is None:
        return None

    identity = neutral_instrument_identity(instrument_key)
    data_name = item.get("data_name") if isinstance(item, Mapping) else None
    if type(data_name) is not str or not data_name or data_name != data_name.strip():
        return None
    candidates = set(alias_map["aliases"].get(data_name, ()))
    exact_routes = set(alias_map["identities"].get(identity, ()))
    candidates &= exact_routes
    if len(candidates) != 1:
        if len(candidates) > 1:
            raise ValueError("neutral position matches multiple configured feeds")
        return None
    route = next(iter(candidates))
    route_identities = alias_map["route_identities"].get(route, ())
    if route_identities and identity not in route_identities:
        raise ValueError("typed position instrument conflicts with configured feed")
    return route

def neutral_record_value(record, name, default=None):
    """Read a field from a neutral DTO or mapping without inspecting raw data."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def normalize_neutral_code_text(value):
    """Preserve the existing SDK-neutral textual code comparison."""
    text = str(value).strip().lower().replace("-", "_")
    numeric_text = text.replace(",", "")
    try:
        number = float(numeric_text)
    except (TypeError, ValueError, OverflowError):
        return text
    if number.is_integer():
        return str(int(number))
    return text


def normalize_neutral_quantity_unit(value):
    """Normalize the existing neutral quantity-unit spelling."""
    if type(value) is not str:
        return None
    return value.strip().lower().replace("-", "_")


def neutral_position_direction(item):
    """Read the direction of a normalized position mapping, never an order side alone."""
    if not isinstance(item, Mapping):
        return ""
    position_side = item.get("position_side")
    if position_side not in (None, ""):
        value = position_side
    elif "qty_unit" in item:
        value = item.get("side")
    else:
        return ""
    if type(value) is not str:
        return ""
    text = value.strip().lower()
    return text if text in {"net", "long", "short"} else ""


def neutral_decimal(value, *, positive=False):
    """Convert a supplied neutral number without inferring its source."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or (positive and result <= 0):
        return None
    return result


def neutral_record_side(record, *, read_value=neutral_record_value):
    """Return a canonical buy/sell side supplied by a neutral record."""
    value = read_value(record, "side")
    value = getattr(value, "value", value)
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in {"buy", "sell"} else None


def neutral_reconciliation_order_fields(record, *, read_value, read_side):
    """Validate neutral order fields before a framework-specific projection.

    This does not establish the record's provenance or bind it to an order.
    """
    order_id = read_value(record, "order_id")
    symbol = read_value(record, "symbol")
    status = read_value(record, "status")
    terminal_confirmed = read_value(record, "terminal_confirmed")
    side = read_side(record)
    quantity = read_value(record, "quantity")
    filled_quantity = read_value(record, "filled_quantity")
    quantity_unit = read_value(record, "quantity_unit")
    price = read_value(record, "price")
    if (
        order_id in (None, "")
        or symbol in (None, "")
        or side is None
        or quantity is None
        or filled_quantity is None
        or type(quantity_unit) is not str
        or not quantity_unit
        or quantity_unit != quantity_unit.strip()
    ):
        return None
    try:
        from bt_api_py.reconciliation import validate_reconciliation_order_status

        validate_reconciliation_order_status(status, terminal_confirmed)
    except (ImportError, TypeError, ValueError):
        return None
    if status == "unknown":
        return None
    return {
        "order_id": order_id, "symbol": symbol, "status": status,
        "terminal_confirmed": terminal_confirmed, "side": side, "quantity": quantity,
        "filled_quantity": filled_quantity, "quantity_unit": quantity_unit, "price": price,
    }


def neutral_reconciliation_fill_fields(fill, *, read_value, read_side):
    """Validate neutral fill and fee fields without attributing them locally."""
    fill_id = read_value(fill, "fill_id")
    order_id = read_value(fill, "order_id")
    symbol = read_value(fill, "symbol")
    side = read_side(fill)
    quantity = read_value(fill, "quantity")
    quantity_unit = read_value(fill, "quantity_unit")
    price = read_value(fill, "price")
    fee = read_value(fill, "fee")
    fee_currency = read_value(fill, "fee_currency")
    fee_unit = read_value(fill, "fee_unit")
    if (
        fill_id in (None, "")
        or order_id in (None, "")
        or symbol in (None, "")
        or side is None
        or quantity is None
        or type(quantity_unit) is not str
        or not quantity_unit
        or quantity_unit != quantity_unit.strip()
        or price is None
    ):
        return None
    try:
        from bt_api_py.reconciliation import parse_reported_commission
    except ImportError:
        return None
    _commission, fee_error = parse_reported_commission(fee, fee_currency, fee_unit)
    if fee_error not in (None, "fee_unavailable"):
        return None
    return {
        "fill_id": fill_id, "order_id": order_id, "symbol": symbol, "side": side,
        "quantity": quantity, "quantity_unit": quantity_unit, "price": price,
        "fee": fee, "fee_currency": fee_currency, "fee_unit": fee_unit,
    }


def neutral_reconciliation_position_fields(row, *, read_value, read_decimal):
    """Read a neutral position projection without binding framework positions."""
    data_name = read_value(row, "data_name")
    instrument_ref = read_value(row, "instrument_ref")
    side = read_value(row, "position_side")
    quantity = read_decimal(read_value(row, "quantity"))
    unit = read_value(row, "quantity_unit")
    average_price = read_value(row, "average_price")
    if (
        type(data_name) is not str
        or not data_name
        or data_name != data_name.strip()
        or type(instrument_ref) is not str
        or not instrument_ref
        or type(unit) is not str
        or not unit
        or unit != unit.strip()
        or side not in {"net", "long", "short"}
        or quantity is None
        or (side != "net" and quantity < 0)
    ):
        return None
    return data_name, instrument_ref, side, quantity, unit, average_price


def iter_broker_event_rows(update):
    """Yield the existing flat row representation of a broker event."""
    if not isinstance(update, dict):
        return

    data = update.get("data")
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, (list, tuple)):
        rows = [item for item in data if isinstance(item, dict)]
    else:
        yield update
        return

    if not rows:
        yield update
        return

    envelope = {key: value for key, value in update.items() if key not in {"data", "id"}}
    if update.get("id") not in (None, ""):
        envelope["message_id"] = update.get("id")
    for row in rows:
        flat = dict(envelope)
        flat.update(row)
        if "kind" not in flat and update.get("kind") not in (None, ""):
            flat["kind"] = update.get("kind")
        yield flat


def broker_event_value(update, *keys):
    """Read a top-level or detail payload field from a broker update."""
    details = update.get("details") or {}
    for key in keys:
        value = update.get(key)
        if value not in (None, ""):
            return value
        value = details.get(key)
        if value not in (None, ""):
            return value
    return None


def broker_event_external_order_id(update, *, extract_value=broker_event_value):
    """Read the existing normalized order identity from an event envelope."""
    if not isinstance(update, Mapping):
        return None
    # Keep the previous kind coercion while both neutral event types use order_id.
    str(update.get("kind") or "").strip().lower()
    return extract_value(update, "order_id")


def broker_event_client_order_id(update, *, extract_value=broker_event_value):
    """Read the existing normalized client order identity from an event envelope."""
    if not isinstance(update, Mapping):
        return None
    return extract_value(update, "client_order_id")


def broker_event_trade_side(update, *, extract_value=broker_event_value):
    """Return whether a trade supplied a side and its normalized direction."""
    side = extract_value(update, "side")
    if side in (None, ""):
        return False, None

    value = getattr(side, "value", side)
    text = str(value).strip().lower()
    if text == "buy":
        return True, True
    if text == "sell":
        return True, False
    return True, None


def canonical_broker_order_status(status):
    """Accept only the SDK facade's canonical order-state vocabulary."""
    if type(status) is not str:
        return "unknown"
    value = status.strip().lower()
    if value in {
        "accepted",
        "partial",
        "completed",
        "canceled",
        "cancel_rejected",
        "rejected",
        "expired",
    }:
        return value
    return "unknown"


def confirmed_terminal_observation_status(observation):
    """Project a terminal marker without establishing observation provenance."""
    if not isinstance(observation, dict):
        return None
    if observation.get("execution_unknown") is True:
        return None
    if observation.get("terminal_confirmed") is not True:
        service_state = observation.get("service_execution_state")
        if service_state in (None, "UNKNOWN"):
            return None
    status = canonical_broker_order_status(observation.get("status"))
    if status not in frozenset({"completed", "canceled", "rejected", "expired"}):
        return None
    return status


__all__ = [
    "verified_submit_ack_error",
    "verified_submit_rejection_code",
    "resolve_neutral_position_route",
    "neutral_record_value",
    "normalize_neutral_code_text",
    "normalize_neutral_quantity_unit",
    "neutral_position_direction",
    "neutral_decimal",
    "neutral_record_side",
    "neutral_reconciliation_order_fields",
    "neutral_reconciliation_fill_fields",
    "neutral_reconciliation_position_fields",
    "iter_broker_event_rows",
    "broker_event_value",
    "broker_event_external_order_id",
    "broker_event_client_order_id",
    "broker_event_trade_side",
    "canonical_broker_order_status",
    "confirmed_terminal_observation_status",
]
