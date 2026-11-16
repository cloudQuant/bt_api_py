"""Finite wire contract for Gateway instrument-status diagnostics.

A CURRENT projection is a local observation only; it is not continuity proof,
product mapping, a calendar input, or permission to trade.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.instrument_status_forwarding import (
    INSTRUMENT_STATUS_SCHEMA,
    MAX_EVENT_BYTES,
    GatewayInstrumentStatusProjection,
)

OPERATION = "direct.instrument_status"
OPERATIONS = frozenset({OPERATION})
SCHEMA = "sdk.gateway.instrument_status.query/1"
REJECTION_CODES = frozenset({"SCOPE_DENIED", "INVALID_REQUEST", "INSTRUMENT_STATUS_UNAVAILABLE"})
_MAX_RESPONSE_BYTES = 8192
_PROJECTION_FIELDS = frozenset(GatewayInstrumentStatusProjection.__dataclass_fields__)
_RESPONSE_FIELDS = frozenset(
    {
        "status",
        "schema",
        "instrument",
        "registration_id",
        "service_epoch",
        "owner_generation",
        "projection",
        "decision_eligible",
        "continuity_proven",
        "authorizes_trading",
        "product_mapping",
    }
)
_CURRENT_REASONS = {
    "before_trading": "instrument_status_before_trading",
    "no_trading": "instrument_status_no_trading",
    "continuous": "instrument_status_continuous",
    "auction_ordering": "instrument_status_auction_ordering",
    "auction_balance": "instrument_status_auction_balance",
    "auction_match": "instrument_status_auction_match",
    "closed": "instrument_status_closed",
    "transaction_processing": "instrument_status_transaction_processing",
}


def _ref(value):
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", errors="strict")) <= 256
    )


def _mapping(value):
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError("INVALID_INSTRUMENT_STATUS_WIRE")
    return dict(value)


def _bounded(value, limit):
    try:
        size = len(
            json.dumps(
                value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        )
    except Exception:
        raise ValueError("INVALID_INSTRUMENT_STATUS_WIRE") from None
    if size > limit:
        raise ValueError("INVALID_INSTRUMENT_STATUS_WIRE")


def decode_query(arguments):
    """Decode an exact one-field request into an immutable six-part key."""
    try:
        raw = _mapping(arguments)
        if set(raw) != {"instrument"}:
            raise ValueError
        key = MarketInstrumentKey.from_dict(raw["instrument"])
        _bounded({"instrument": key.to_dict()}, 4096)
        return key
    except Exception:
        raise ValueError("INVALID_INSTRUMENT_STATUS_REQUEST") from None


def _projection(value):
    raw = _mapping(value)
    if set(raw) != _PROJECTION_FIELDS | {"schema"} or raw["schema"] != INSTRUMENT_STATUS_SCHEMA:
        raise ValueError("INVALID_INSTRUMENT_STATUS_PROJECTION")
    # Reject aliases, unknown fields and values that the Gateway type rejects.
    data = {name: raw[name] for name in _PROJECTION_FIELDS}
    projection = GatewayInstrumentStatusProjection(**data)
    if projection.state == "CURRENT" and (
        projection.session_generation is None
        or projection.trading_day is None
        or _CURRENT_REASONS.get(projection.status_category) != projection.reason_code
        or projection.observed_status_category not in (None, projection.status_category)
    ):
        raise ValueError("INVALID_INSTRUMENT_STATUS_PROJECTION")
    if any(
        type(value) is int and not 0 < value < 2**63
        for value in (projection.source_sequence, projection.received_at)
    ):
        raise ValueError("INVALID_INSTRUMENT_STATUS_PROJECTION")
    if projection.payload() != raw:
        raise ValueError("INVALID_INSTRUMENT_STATUS_PROJECTION")
    _bounded(raw, MAX_EVENT_BYTES)
    return projection


def encode_observation(*, instrument, registration_id, service_epoch, owner_generation, projection):
    """Encode only an existing validated Gateway projection and explicit identity."""
    if (
        type(instrument) is not MarketInstrumentKey
        or type(projection) is not GatewayInstrumentStatusProjection
    ):
        raise ValueError("INVALID_INSTRUMENT_STATUS_OBSERVATION")
    value = {
        "status": "OBSERVED",
        "schema": SCHEMA,
        "instrument": instrument.to_dict(),
        "registration_id": registration_id,
        "service_epoch": service_epoch,
        "owner_generation": owner_generation,
        "projection": projection.payload(),
        "decision_eligible": False,
        "continuity_proven": False,
        "authorizes_trading": False,
        "product_mapping": "NOT_PROVEN",
    }
    return validate_response(
        value,
        expected_instrument=instrument,
        expected_registration_id=registration_id,
        expected_service_epoch=service_epoch,
    )


def validate_response(
    value, *, expected_instrument=None, expected_registration_id=None, expected_service_epoch=None
):
    """Validate exact fields and return a detached plain response."""
    try:
        raw = _mapping(value)
        if raw.get("status") == "REQUEST_REJECTED":
            if set(raw) != {"status", "code"} or raw["code"] not in REJECTION_CODES:
                raise ValueError
            return {"status": "REQUEST_REJECTED", "code": raw["code"]}
        if set(raw) != _RESPONSE_FIELDS or raw["status"] != "OBSERVED" or raw["schema"] != SCHEMA:
            raise ValueError
        key = MarketInstrumentKey.from_dict(raw["instrument"])
        if expected_instrument is not None and (
            type(expected_instrument) is not MarketInstrumentKey or key != expected_instrument
        ):
            raise ValueError
        if any(
            not _ref(raw[name]) for name in ("registration_id", "service_epoch", "owner_generation")
        ):
            raise ValueError
        if (
            expected_registration_id is not None
            and raw["registration_id"] != expected_registration_id
            or expected_service_epoch is not None
            and raw["service_epoch"] != expected_service_epoch
            or any(
                raw[name] is not False
                for name in ("decision_eligible", "continuity_proven", "authorizes_trading")
            )
            or raw["product_mapping"] != "NOT_PROVEN"
        ):
            raise ValueError
        projection = _projection(raw["projection"])
        if projection.venue != key.venue or projection.instrument != key.native_symbol:
            raise ValueError
        detached = dict(raw, instrument=key.to_dict(), projection=projection.payload())
        _bounded(detached, _MAX_RESPONSE_BYTES)
        return detached
    except Exception:
        raise ValueError("INVALID_INSTRUMENT_STATUS_RESPONSE") from None
