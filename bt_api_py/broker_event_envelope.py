"""Legacy neutral broker-event envelope projection for SDK consumers.

Provider adapters remain responsible for interpreting native payloads. This
module preserves the existing generic envelope flattening only; it does not
authenticate, normalize, or establish the provenance of any event.
"""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


def neutral_record_value(record, name, default=None):
    """Read a field from a neutral DTO or mapping without inspecting raw data."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


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


__all__ = [
    "neutral_record_value",
    "neutral_decimal",
    "neutral_record_side",
    "iter_broker_event_rows",
    "broker_event_value",
    "broker_event_trade_side",
    "canonical_broker_order_status",
]
