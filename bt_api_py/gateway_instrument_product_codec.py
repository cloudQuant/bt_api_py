"""Strict detached wire values for an observed native product identifier.

Remaining TTL is measured by the sender during the reply, not from receipt.
It does not establish future source continuity or permission to trade.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime

from bt_api_base.market_protocol import MarketInstrumentKey

SCHEMA = "sdk.gateway.instrument_product.observation/1"
BEGIN = "instrument.product.begin.v1"
STATUS = "instrument.product.status.v1"
CANCEL = "instrument.product.cancel.v1"
OPERATIONS = frozenset({BEGIN, STATUS, CANCEL})
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "instrument",
        "product_id",
        "session_generation",
        "connection_generation",
        "trading_day",
        "completed_at_utc",
        "remaining_seconds",
    }
)


def _text(value):
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", "strict")) > 256
    ):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    return value


def validate_snapshot(value, *, expected_instrument=None):
    if (
        not isinstance(value, Mapping)
        or set(value) != _SNAPSHOT_FIELDS
        or value["schema"] != SCHEMA
    ):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    try:
        if not isinstance(value["instrument"], Mapping):
            raise ValueError
        instrument = MarketInstrumentKey.from_dict(dict(value["instrument"]))
        if expected_instrument is not None and instrument != expected_instrument:
            raise ValueError
        product = _text(value["product_id"])
        generation = _text(value["session_generation"])
        connection = value["connection_generation"]
        if type(connection) is not int or not 0 < connection < 2**63:
            raise ValueError
        day = value["trading_day"]
        if type(day) is not str or date.fromisoformat(day).isoformat() != day:
            raise ValueError
        stamp = value["completed_at_utc"]
        if (
            type(stamp) is not str
            or re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", stamp
            )
            is None
        ):
            raise ValueError
        parsed = datetime.fromisoformat(stamp[:-1] + "+00:00")
        if parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError
        remaining = value["remaining_seconds"]
        if (
            type(remaining) not in (int, float)
            or not math.isfinite(remaining)
            or not 0 < remaining <= 300
        ):
            raise ValueError
        result = {
            "schema": SCHEMA,
            "instrument": instrument.to_dict(),
            "product_id": product,
            "session_generation": generation,
            "connection_generation": connection,
            "trading_day": day,
            "completed_at_utc": stamp,
            "remaining_seconds": remaining,
        }
        if (
            len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
            > 16384
        ):
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE") from None


def validate_response(value, *, expected_operation_id, expected_instrument=None):
    if not isinstance(value, Mapping) or set(value) != {
        "operation_id",
        "status",
        "code",
        "snapshot",
    }:
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    state, code = value["status"], value["code"]
    if (
        type(expected_operation_id) is not str
        or re.fullmatch(r"[0-9a-f]{32}", expected_operation_id) is None
        or type(value["operation_id"]) is not str
        or value["operation_id"] != expected_operation_id
        or type(state) is not str
        or state not in {"pending", "ready", "failed", "cancelled", "not_found"}
        or (code is not None and type(code) is not str)
    ):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    if (
        state == "failed" and code not in {"SOURCE_UNAVAILABLE", "SOURCE_EXPIRED", "QUERY_TIMEOUT"}
    ) or (state != "failed" and code is not None):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    snapshot = value["snapshot"]
    if state == "ready":
        snapshot = validate_snapshot(snapshot, expected_instrument=expected_instrument)
    elif snapshot is not None:
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_RESPONSE")
    return {
        "operation_id": expected_operation_id,
        "status": state,
        "code": code,
        "snapshot": snapshot,
    }
