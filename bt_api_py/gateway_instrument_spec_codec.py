"""Bounded, provider-independent wire values for Gateway limit-order rules.

This codec carries metadata only; it does not establish freshness, provenance,
readiness or authority.  The caller selects and authorizes the remote route.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from bt_api_py._contracts.models import Freshness, InstrumentSpec

_SPEC_FIELDS = frozenset(
    {
        "exchange_name",
        "symbol",
        "asset_type",
        "base_currency",
        "quote_currency",
        "contract_type",
        "linear",
        "contract_value",
        "contract_multiplier",
        "price_tick",
        "quantity_step",
        "min_quantity",
        "max_quantity",
        "min_notional",
        "quantity_unit",
        "status",
        "freshness",
        "raw_rule_fingerprint",
        "source",
        "available",
        "unavailable_reason",
    }
)
_NUMERIC_FIELDS = (
    "contract_value",
    "contract_multiplier",
    "price_tick",
    "quantity_step",
    "min_quantity",
    "max_quantity",
    "min_notional",
)
_REQUIRED_NUMERIC_FIELDS = frozenset(_NUMERIC_FIELDS[:5])
_REQUIRED_TEXT_FIELDS = ("exchange_name", "symbol", "source")
_METADATA_TEXT_FIELDS = ("asset_type", "base_currency", "quote_currency", "contract_type", "status")
_UNITS = frozenset({"base", "contracts", "lots", "native"})
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?\Z", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)\Z",
    re.ASCII,
)
_MAX_WIRE_BYTES = 64 * 1024
_MAX_TEXT_BYTES = 256
_MAX_DECIMAL_BYTES = 128
_MAX_DIGITS = 64
_MAX_EXPONENT = 100


def _invalid() -> None:
    raise ValueError("INVALID_GATEWAY_INSTRUMENT_SPEC")


def _mapping(value: object, fields: frozenset[str]) -> Mapping[str, Any]:
    # Wire decoding also accepts the immutable mappings returned by SDK RPCs.
    if (
        type(value) not in (dict, MappingProxyType)
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        _invalid()
    return value


def _text(value: object, *, empty: bool = False) -> str:
    if type(value) is not str or (not value and not empty) or value != value.strip():
        _invalid()
    try:
        if len(value.encode("utf-8", "strict")) > _MAX_TEXT_BYTES or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            _invalid()
    except UnicodeError:
        _invalid()
    return value


def _number(value: object, *, nonnegative: bool, wire: bool) -> Decimal | None:
    if value is None:
        return None
    if wire:
        if (
            type(value) is not str
            or len(value) > _MAX_DECIMAL_BYTES
            or not _DECIMAL.fullmatch(value)
        ):
            _invalid()
        try:
            number = Decimal(value)
        except InvalidOperation:
            _invalid()
    else:
        if type(value) is not Decimal:
            _invalid()
        number = value
    if not number.is_finite():
        _invalid()
    parts = number.as_tuple()
    if (
        len(parts.digits) > _MAX_DIGITS
        or abs(parts.exponent) > _MAX_EXPONENT
        or abs(number.adjusted()) > _MAX_EXPONENT
        or len(str(number)) > _MAX_DECIMAL_BYTES
        or (number < 0 if nonnegative else number <= 0)
    ):
        _invalid()
    return number


def _timestamp(value: object, *, wire: bool) -> datetime:
    if wire:
        if type(value) is not str or not _TIMESTAMP.fullmatch(value):
            _invalid()
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _invalid()
    else:
        if type(value) is not datetime:
            _invalid()
        observed = value
    try:
        if observed.utcoffset() is None:
            _invalid()
        return observed.astimezone(UTC)
    except (OverflowError, ValueError, TypeError):
        _invalid()


def _freshness(value: object, *, wire: bool) -> Freshness:
    if wire:
        fields = _mapping(value, frozenset({"source", "observed_at", "stale", "stale_reason"}))
        source, observed, stale, reason = (
            fields["source"],
            fields["observed_at"],
            fields["stale"],
            fields["stale_reason"],
        )
    else:
        if type(value) is not Freshness:
            _invalid()
        source, observed, stale, reason = (
            value.source,
            value.observed_at,
            value.stale,
            value.stale_reason,
        )
    _text(source)
    if type(stale) is not bool:
        _invalid()
    if reason is not None:
        _text(reason)
    return Freshness(
        source=source, observed_at=_timestamp(observed, wire=wire), stale=stale, stale_reason=reason
    )


def _validated_fields(values: Mapping[str, Any], *, wire: bool) -> dict[str, Any]:
    available = values["available"]
    if type(available) is not bool or type(values["linear"]) is not bool:
        _invalid()
    result: dict[str, Any] = {"available": available, "linear": values["linear"]}
    for name in _REQUIRED_TEXT_FIELDS:
        result[name] = _text(values[name])
    for name in _METADATA_TEXT_FIELDS:
        result[name] = _text(values[name], empty=not available)
    unit = _text(values["quantity_unit"])
    if unit not in _UNITS:
        _invalid()
    result["quantity_unit"] = unit
    fingerprint = _text(values["raw_rule_fingerprint"], empty=not available)
    if fingerprint and not _FINGERPRINT.fullmatch(fingerprint):
        _invalid()
    if available and not fingerprint:
        _invalid()
    result["raw_rule_fingerprint"] = fingerprint
    reason = values["unavailable_reason"]
    if reason is not None:
        reason = _text(reason)
    if (available and reason is not None) or (not available and reason is None):
        _invalid()
    result["unavailable_reason"] = reason
    result["freshness"] = _freshness(values["freshness"], wire=wire)
    for name in _NUMERIC_FIELDS:
        number = _number(values[name], nonnegative=name == "min_notional", wire=wire)
        if available and name in _REQUIRED_NUMERIC_FIELDS and number is None:
            _invalid()
        result[name] = number
    minimum, maximum = result["min_quantity"], result["max_quantity"]
    if minimum is not None and maximum is not None and minimum > maximum:
        _invalid()
    return result


def _check_size(payload: dict[str, Any]) -> None:
    try:
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
            "ascii"
        )
    except (TypeError, ValueError, UnicodeError):
        _invalid()
    if len(raw) > _MAX_WIRE_BYTES:
        _invalid()


def encode_gateway_instrument_spec(spec: InstrumentSpec) -> dict[str, Any]:
    """Encode one exact limit-rule DTO; provider raw metadata is never serialized."""
    if type(spec) is not InstrumentSpec or not isinstance(spec.raw, Mapping) or spec.raw:
        _invalid()
    values = _validated_fields({name: getattr(spec, name) for name in _SPEC_FIELDS}, wire=False)
    fresh = values["freshness"]
    values["freshness"] = {
        "source": fresh.source,
        "observed_at": fresh.observed_at.isoformat().replace("+00:00", "Z"),
        "stale": fresh.stale,
        "stale_reason": fresh.stale_reason,
    }
    for name in _NUMERIC_FIELDS:
        if values[name] is not None:
            values[name] = str(values[name])
    payload = {"schema_version": 1, "order_type": "limit", "spec": values}
    _check_size(payload)
    return payload


def decode_gateway_instrument_spec(
    payload: Mapping[str, Any], *, expected_exchange_name: str, expected_symbol: str
) -> InstrumentSpec:
    """Decode an exact, bounded limit-rule payload and bind it to the requested route."""
    _text(expected_exchange_name)
    _text(expected_symbol)
    outer = _mapping(payload, frozenset({"schema_version", "order_type", "spec"}))
    if (
        type(outer["schema_version"]) is not int
        or outer["schema_version"] != 1
        or type(outer["order_type"]) is not str
        or outer["order_type"] != "limit"
    ):
        _invalid()
    fields = _mapping(outer["spec"], _SPEC_FIELDS)
    values = _validated_fields(fields, wire=True)
    if values["exchange_name"] != expected_exchange_name or values["symbol"] != expected_symbol:
        _invalid()
    # Validate a detached JSON form, including wire size, without trusting raw data.
    detached = {"schema_version": 1, "order_type": "limit", "spec": dict(fields)}
    detached["spec"]["freshness"] = dict(fields["freshness"])
    _check_size(detached)
    try:
        return InstrumentSpec(**values, raw=MappingProxyType({}))
    except (TypeError, ValueError):
        _invalid()
