"""Pure, structural CTP close planning.

This module deliberately sits below the SDK execution session.  It consumes a
snapshot shaped like the strict CTP position evidence and produces a bounded
set of close actions.  A caller supplied snapshot is useful for deterministic
planning tests, but it is not an issuer capability and can never make the
result execution eligible.

The implementation has no clock, environment, filesystem, network, account,
or journal access.  Both current clock readings are explicit arguments so a
session can validate the same plan against its own source immediately before
any future integration is attempted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import Any, NoReturn

SCHEMA_VERSION = "ctp_close_plan.v1"
PLANNER_CAPABILITY = "STRUCTURAL_ONLY"
EXECUTION_BLOCK_REASON = "BLOCKED_POLICY_APPLICABILITY"
MAX_SOURCE_TTL_SECONDS = 5
MAX_LEGS = 3
MAX_ACTIONS = 6

_MISSING = object()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIRECTION_TO_SIDE = {"long": ("2", "sell"), "short": ("3", "buy")}
_DIRECTION_VALUES = frozenset(_DIRECTION_TO_SIDE.values())
_HEDGE_VALUES = frozenset(("1", "2", "3"))
_POSITION_DATES = frozenset(("1", "2"))
_SPLIT_POLICY_ID = "SYNTHETIC-SPLIT-v1"
_GENERIC_POLICY_ID = "SYNTHETIC-GENERIC-v1"
_POLICY_SOURCE_SHA256 = "ec5dfea45f52e85a484332a2cd7890cedf9d8369946cbcb07b7c203c24d8d15a"
_SPLIT_EXCHANGES = frozenset(("SHFE", "INE"))
_GENERIC_EXCHANGES = frozenset(("CZCE", "DCE"))
_KNOWN_EXCHANGES = _SPLIT_EXCHANGES | _GENERIC_EXCHANGES
_ZERO_FREEZE_FIELDS = (
    "LongFrozen",
    "ShortFrozen",
    "CombLongFrozen",
    "CombShortFrozen",
    "StrikeFrozen",
    "AbandonFrozen",
    "YdStrikeFrozen",
    "CombPosition",
)
_IDENTITY_FIELDS = (
    "InstrumentID",
    "ExchangeID",
    "PosiDirection",
    "HedgeFlag",
    "PositionDate",
    "TradingDay",
)
_QUANTITY_FIELDS = ("Position", "TodayPosition", "YdPosition")
_REQUIRED_ROW_FIELDS = (*_IDENTITY_FIELDS, *_QUANTITY_FIELDS, *_ZERO_FREEZE_FIELDS)
_ACTION_KEYS = (
    "action_id",
    "leg_id",
    "InstrumentID",
    "ExchangeID",
    "HedgeFlag",
    "side",
    "position_side",
    "offset",
    "quantity",
    "quantity_unit",
    "source_row_keys",
    "source_hash",
    "policy_id",
    "policy_version",
    "policy_sha256",
    "candidate_id",
    "candidate_sha256",
    "cycle_id",
    "execution_role",
    "age_allocation",
    "expires_at_utc",
    "expires_monotonic_ns",
)
_PLAN_KEYS = (
    "schema_version",
    "status",
    "planner_capability",
    "execution_eligible",
    "execution_block_reason",
    "source_binding",
    "policy_binding",
    "request_binding",
    "effective_expiry",
    "limits",
    "actions",
    "fee_roles_required",
    "plan_sha256",
)


class CtpClosePlanError(ValueError):
    """Stable fail-closed error raised by :func:`build_ctp_close_plan`."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.error_code = code
        super().__init__(f"{code}: {message or code}")


def _error(code: str, message: str | None = None) -> NoReturn:
    raise CtpClosePlanError(code, message)


def _read(value: Any, names: Sequence[str], default: Any = _MISSING) -> Any:
    """Read one exact field from a mapping or a strict evidence object."""

    found: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        found.extend((name, value[name]) for name in names if name in value)
    else:
        for name in names:
            try:
                item = getattr(value, name)
            except AttributeError:
                continue
            found.append((name, item))
    if not found:
        if default is _MISSING:
            return _MISSING
        return default
    first = found[0][1]
    for name, item in found[1:]:
        if item != first:
            _error("O3B_POSITION_EVIDENCE_INCONSISTENT", f"conflicting fields: {name}")
    return first


def _read_consistent(value: Any, names: Sequence[str], label: str) -> Any:
    result = _read(value, names)
    if result is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", label)
    return result


def _strict_text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _error("O3B_POSITION_ROW_INVALID", f"{field} requires exact nonempty text")
    return value


def _strict_metadata_text(value: Any, field: str, code: str = "O3B_REQUEST_INVALID") -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _error(code, f"{field} requires exact nonempty text")
    return value


def _strict_hex(value: Any, field: str, code: str = "O3B_REQUEST_INVALID") -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _error(code, f"{field} requires a lowercase SHA-256 digest")
    return value


def _strict_int(value: Any, field: str, *, positive: bool = False) -> int:
    if type(value) is not int or (positive and value <= 0) or (not positive and value < 0):
        _error("O3B_QUANTITY_INVALID", f"{field} requires an exact integer")
    return value


def _valid_day(value: Any) -> bool:
    if type(value) is not str or len(value) != 8 or not value.isdigit():
        return False
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return parsed.strftime("%Y%m%d") == value


def _parse_utc(value: Any, field: str, code: str = "O3B_CLOCK_INVALID") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CtpClosePlanError(code, f"{field} is not an ISO timestamp") from exc
    else:
        _error(code, f"{field} requires an aware UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _error(code, f"{field} cannot be naive")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clock_ns(value: Any, field: str, *, seconds: bool = False) -> int:
    """Convert an explicitly typed clock value to integer nanoseconds.

    Public plan fields use integer nanoseconds.  O3a evidence exposes legacy
    monotonic values in seconds, so those fields are accepted only through the
    ``seconds=True`` path and are converted with ``Decimal(str(...))``.
    """

    if type(value) is int:
        if value < 0:
            _error("O3B_CLOCK_INVALID", f"{field} cannot be negative")
        return value
    if not seconds or type(value) is not float or not isfinite(value) or value < 0:
        _error("O3B_CLOCK_INVALID", f"{field} requires a finite clock value")
    try:
        scaled = Decimal(str(value)) * Decimal(1_000_000_000)
    except (InvalidOperation, ValueError) as exc:
        raise CtpClosePlanError("O3B_CLOCK_INVALID", field) from exc
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        _error("O3B_CLOCK_INVALID", f"{field} is not representable in nanoseconds")
    return int(scaled)


def _clock_from(
    value: Any, ns_names: Sequence[str], seconds_names: Sequence[str], label: str
) -> int:
    ns_item = _read(value, ns_names)
    seconds_item = _read(value, seconds_names)
    if ns_item is not _MISSING and seconds_item is not _MISSING:
        if _clock_ns(ns_item, label) != _clock_ns(seconds_item, label, seconds=True):
            _error("O3B_CLOCK_INVALID", f"conflicting clock aliases for {label}")
        return _clock_ns(ns_item, label)
    if ns_item is not _MISSING:
        return _clock_ns(ns_item, label)
    if seconds_item is not _MISSING:
        return _clock_ns(seconds_item, label, seconds=True)
    _error("O3B_REQUIRED_FIELD_MISSING", label)


def _snapshot(value: Any) -> Any:
    """Recursively freeze built-in containers without invoking user coercion."""

    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                _error("O3B_POSITION_ROW_INVALID", "mapping keys must be exact strings")
            copied[key] = _snapshot(item)
        return MappingProxyType(copied)
    if type(value) is list or type(value) is tuple:
        return tuple(_snapshot(item) for item in value)
    if type(value) in (str, int, float, bool) or value is None or isinstance(value, datetime):
        return value
    if type(value) is Decimal:
        return value
    # Do not call str(), int(), repr(), or custom copy hooks.  The strict
    # scalar validators below will reject this value before a plan is emitted.
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(value[key]) for key in sorted(value)}
    if type(value) is tuple or type(value) is list:
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return _format_utc(value)
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("non-finite value")
        return value
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        return str(value)
    raise TypeError(f"unsupported canonical value: {type(value)!r}")


def canonical_ctp_close_plan_json(value: Mapping[str, Any]) -> bytes:
    """Serialize a plan payload using the frozen UTF-8 canonical contract."""

    try:
        canonical = _json_value(value)
        return json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CtpClosePlanError("O3B_CANONICAL_VALUE_INVALID") from exc


@dataclass(frozen=True, slots=True)
class _PositionRow:
    raw: Mapping[str, Any]
    identity: tuple[str, str, str, str, str, str]
    instrument: str
    exchange: str
    direction: str
    hedge: str
    position_date: str
    position: int
    today_position: int
    yd_position: int
    source_row_keys: tuple[tuple[str, Any], ...]


def _raw_row(row: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    fields = _read(row, ("fields",), default=_MISSING)
    if isinstance(row, Mapping) and "raw_record" in row and "fields" in row:
        raw = row["raw_record"]
    else:
        raw = _read(row, ("raw_record",), default=_MISSING)
        if raw is _MISSING:
            raw = row
    if not isinstance(raw, Mapping):
        _error("O3B_REQUIRED_FIELD_MISSING", "raw position record")
    snap = _snapshot(raw)
    if not isinstance(snap, Mapping):
        _error("O3B_REQUIRED_FIELD_MISSING", "raw position record")
    if fields is _MISSING or fields is None:
        return snap, None
    if not isinstance(fields, Mapping):
        _error("O3B_POSITION_ROW_INVALID", "fields must be a mapping")
    return snap, fields


def _validate_field_state(fields: Mapping[str, Any], name: str) -> None:
    field = fields.get(name, _MISSING)
    if field is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", name)
    present = _read(field, ("present",), default=_MISSING)
    state = _read(field, ("state",), default=_MISSING)
    if present is not True or state != "value":
        if present is False or state == "missing":
            _error("O3B_REQUIRED_FIELD_MISSING", name)
        _error("O3B_POSITION_ROW_INVALID", f"{name} is not a known value")


def _scalar_matches_raw(raw: Any, typed: Any, *, numeric: bool) -> bool:
    """Compare a typed evidence value with its frozen raw scalar.

    CTP quantity fields may be represented by an integer, a decimal string, or
    a ``Decimal`` by the public collector.  Identity fields remain exact text;
    no arbitrary ``str``/``int`` coercion is allowed here.
    """

    if type(raw) is bool or type(typed) is bool:
        return False
    if numeric:
        if type(raw) not in (int, float, str, Decimal) or type(typed) not in (
            int,
            float,
            str,
            Decimal,
        ):
            return False
        try:
            raw_decimal = Decimal(str(raw))
            typed_decimal = Decimal(str(typed))
        except (InvalidOperation, ValueError, TypeError):
            return False
        return (
            raw_decimal.is_finite() and typed_decimal.is_finite() and raw_decimal == typed_decimal
        )
    return type(raw) is type(typed) and raw == typed


def _validate_typed_fields(raw: Mapping[str, Any], fields: Mapping[str, Any]) -> None:
    """Require every supplied typed field to describe the same raw snapshot."""

    numeric_names = frozenset(
        (
            *_QUANTITY_FIELDS,
            *_ZERO_FREEZE_FIELDS,
            "LongFrozenAmount",
            "ShortFrozenAmount",
            "StrikeFrozenAmount",
            "FrozenMargin",
            "FrozenCash",
            "FrozenCommission",
        )
    )
    for key, field in fields.items():
        if type(key) is not str:
            _error("O3B_POSITION_ROW_INVALID", "typed field names must be strings")
        field_name = _read(field, ("name",), default=_MISSING)
        if field_name is _MISSING or field_name != key:
            _error("O3B_POSITION_ROW_INVALID", f"typed field name mismatch: {key}")
        present = _read(field, ("present",), default=_MISSING)
        state = _read(field, ("state",), default=_MISSING)
        if type(present) is not bool or state not in {"value", "missing", "unknown"}:
            _error("O3B_POSITION_ROW_INVALID", f"invalid typed field state: {key}")
        raw_present = key in raw
        if present != raw_present:
            _error("O3B_POSITION_ROW_INVALID", f"typed presence differs from raw: {key}")
        if not raw_present:
            if state != "missing":
                _error(
                    "O3B_POSITION_ROW_INVALID",
                    f"missing raw field is not missing: {key}",
                )
            continue
        if state != "value":
            _error("O3B_POSITION_ROW_INVALID", f"raw field is not typed as value: {key}")
        raw_value = _read(field, ("raw_value",), default=_MISSING)
        typed_value = _read(field, ("value",), default=_MISSING)
        if raw_value is _MISSING or typed_value is _MISSING:
            _error("O3B_POSITION_ROW_INVALID", f"typed value is incomplete: {key}")
        if not _scalar_matches_raw(raw[key], raw_value, numeric=key in numeric_names):
            _error("O3B_POSITION_ROW_INVALID", f"typed raw value differs: {key}")
        if not _scalar_matches_raw(raw[key], typed_value, numeric=key in numeric_names):
            _error("O3B_POSITION_ROW_INVALID", f"typed value differs: {key}")


def _parse_row(
    row: Any, trading_day: str, supported_exchanges: frozenset[str] | None
) -> _PositionRow:
    raw, fields = _raw_row(row)
    for name in _REQUIRED_ROW_FIELDS:
        if name not in raw:
            _error("O3B_REQUIRED_FIELD_MISSING", name)
        # O3a's legacy ``fields`` mapping predates CombPosition, while its raw
        # mapping can still retain that native field.  The raw presence and
        # exact integer validator below are the contract for this one field;
        # every other required field needs an explicit strict field state when
        # a typed row supplies ``fields``.
        if fields is not None and (name != "CombPosition" or name in fields):
            _validate_field_state(fields, name)
    if fields is not None:
        _validate_typed_fields(raw, fields)

    text_values: dict[str, str] = {}
    for name in _IDENTITY_FIELDS:
        text_values[name] = _strict_text(raw[name], name)
    if text_values["PosiDirection"] not in {"2", "3"}:
        _error("O3B_POSITION_ROW_INVALID", "net position rows cannot be planned")
    if text_values["HedgeFlag"] not in _HEDGE_VALUES:
        _error("O3B_POSITION_ROW_INVALID", "unknown hedge flag")
    if text_values["PositionDate"] not in _POSITION_DATES:
        _error("O3B_POSITION_ROW_INVALID", "unknown position date")
    if not _valid_day(text_values["TradingDay"]):
        _error("O3B_POSITION_ROW_INVALID", "invalid trading day")
    if text_values["TradingDay"] != trading_day:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "row trading day differs from current scope")
    if text_values["ExchangeID"] not in _KNOWN_EXCHANGES:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "unknown row exchange")
    if supported_exchanges is not None and text_values["ExchangeID"] not in supported_exchanges:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "row exchange is outside policy")

    quantities: dict[str, int] = {}
    for name in (*_QUANTITY_FIELDS, *_ZERO_FREEZE_FIELDS):
        value = raw[name]
        if type(value) is not int or value < 0:
            _error(
                "O3B_POSITION_ROW_INVALID",
                f"{name} requires an exact nonnegative integer",
            )
        quantities[name] = value

    if quantities["TodayPosition"] > quantities["Position"]:
        _error("O3B_POSITION_ROW_INVALID", "TodayPosition exceeds Position")
    for account_field in ("BrokerID", "InvestorID"):
        if account_field not in raw:
            _error("O3B_REQUIRED_FIELD_MISSING", account_field)
        _strict_text(raw[account_field], account_field)

    identity = tuple(text_values[name] for name in _IDENTITY_FIELDS)
    source_row_keys = tuple((name, raw[name]) for name in _IDENTITY_FIELDS)
    return _PositionRow(
        raw=raw,
        identity=identity,  # type: ignore[arg-type]
        instrument=text_values["InstrumentID"],
        exchange=text_values["ExchangeID"],
        direction=text_values["PosiDirection"],
        hedge=text_values["HedgeFlag"],
        position_date=text_values["PositionDate"],
        position=quantities["Position"],
        today_position=quantities["TodayPosition"],
        yd_position=quantities["YdPosition"],
        source_row_keys=source_row_keys,
    )


def _validate_freeze(rows: Sequence[_PositionRow]) -> None:
    for row in rows:
        if row.raw["LongFrozen"] or row.raw["ShortFrozen"]:
            _error("O3B_FROZEN_ALLOCATION_UNPROVEN", "ordinary frozen quantity is nonzero")
        if any(row.raw[name] for name in _ZERO_FREEZE_FIELDS[2:]):
            _error(
                "O3B_SPECIAL_POSITION_UNSUPPORTED",
                "special position quantity is nonzero",
            )


def _normalize_policy_deadline(policy: dict[str, Any]) -> None:
    """Normalize an optional preregistered policy deadline without renewal."""

    utc_present = "expires_at_utc" in policy
    mono_present = "expires_monotonic_ns" in policy or "expires_monotonic" in policy
    if utc_present != mono_present:
        _error(
            "O3B_POLICY_INVALID_OR_MISMATCH",
            "policy deadline needs paired UTC and monotonic values",
        )
    if not utc_present:
        return
    policy["expires_at_utc"] = _parse_utc(
        policy["expires_at_utc"], "policy expiry", "O3B_POLICY_INVALID_OR_MISMATCH"
    )
    policy["expires_monotonic_ns"] = _clock_from(
        policy,
        ("expires_monotonic_ns",),
        ("expires_monotonic",),
        "policy expiry monotonic",
    )
    policy.pop("expires_monotonic", None)


def _profile_material_matches(policy: Mapping[str, Any], name: str, expected: Any) -> bool:
    actual = policy.get(name, _MISSING)
    if actual is _MISSING:
        return False
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and dict(actual) == dict(expected)
    if isinstance(expected, (list, tuple)):
        return type(actual) in (list, tuple) and tuple(actual) == tuple(expected)
    return type(actual) is type(expected) and actual == expected


def _require_profile_material(policy: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for name, value in expected.items():
        if not _profile_material_matches(policy, name, value):
            _error(
                "O3B_POLICY_INVALID_OR_MISMATCH",
                f"profile semantic material is missing or contradictory: {name}",
            )


def _validate_source_location(policy: Mapping[str, Any]) -> None:
    value = policy.get("source_location", _MISSING)
    if type(value) not in (list, tuple) or not value:
        _error(
            "O3B_POLICY_INVALID_OR_MISMATCH",
            "profile source location is missing or invalid",
        )
    for item in value:
        _strict_metadata_text(item, "source_location", "O3B_POLICY_INVALID_OR_MISMATCH")


def _normalize_profile_aliases(policy: dict[str, Any]) -> None:
    for canonical, alias in (
        ("document_family", "source_document_family"),
        ("document_version", "source_document_version"),
    ):
        canonical_value = policy.get(canonical, _MISSING)
        alias_value = policy.get(alias, _MISSING)
        if (
            canonical_value is not _MISSING
            and alias_value is not _MISSING
            and canonical_value != alias_value
        ):
            _error(
                "O3B_POLICY_INVALID_OR_MISMATCH",
                f"conflicting profile aliases: {canonical}",
            )
        if canonical_value is _MISSING and alias_value is not _MISSING:
            policy[canonical] = alias_value
        policy.pop(alias, None)


def _validate_policy(policy: Any) -> tuple[dict[str, Any], str, frozenset[str]]:
    if not isinstance(policy, Mapping):
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "policy must be a mapping")
    policy = dict(_snapshot(policy))
    _normalize_profile_aliases(policy)
    _normalize_policy_deadline(policy)
    policy_id = _read_consistent(policy, ("policy_id",), "policy_id")
    version_a = policy.get("policy_version", _MISSING)
    version_b = policy.get("version", _MISSING)
    if version_a is not _MISSING and version_b is not _MISSING and version_a != version_b:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "conflicting policy versions")
    version = version_a if version_a is not _MISSING else version_b
    if version is _MISSING:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "policy version")
    policy.pop("policy_version", None)
    status = _read_consistent(policy, ("status",), "policy status")
    verified = _read_consistent(policy, ("execution_policy_verified",), "policy verification")
    source_sha_a = policy.get("policy_source_sha256", _MISSING)
    source_sha_b = policy.get("source_document_sha256", _MISSING)
    if (
        source_sha_a is not _MISSING
        and source_sha_b is not _MISSING
        and source_sha_a != source_sha_b
    ):
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "conflicting policy source digests")
    source_sha = source_sha_a if source_sha_a is not _MISSING else source_sha_b
    if source_sha is _MISSING:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "policy source digest")
    policy.pop("policy_source_sha256", None)
    profile = _read_consistent(policy, ("native_profile",), "native_profile")
    row_model = _read_consistent(policy, ("row_model",), "row_model")
    exchanges = _read_consistent(policy, ("exchanges",), "policy exchanges")
    freeze_mode = _read_consistent(policy, ("freeze_mode",), "freeze mode")
    zero_fields = _read_consistent(
        policy, ("required_explicit_zero_fields",), "required freeze fields"
    )
    applicability = _read_consistent(
        policy, ("real_full_ctp_applicability",), "policy applicability"
    )
    if type(zero_fields) not in (list, tuple) or any(type(item) is not str for item in zero_fields):
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "required freeze fields are invalid")
    if (
        policy_id not in {_SPLIT_POLICY_ID, _GENERIC_POLICY_ID}
        or version != "1"
        or status != "SYNTHETIC_PLANNING_PROFILE"
        or verified is not False
        or source_sha != _POLICY_SOURCE_SHA256
        or profile != "SYNTHETIC_FULL_CTP_6.7.7"
        or freeze_mode != "zero_freeze_only"
        or applicability != EXECUTION_BLOCK_REASON
        or row_model not in {"split_by_position_date", "combined_today_and_history"}
        or type(exchanges) not in (list, tuple)
        or not exchanges
        or any(type(item) is not str for item in exchanges)
        or set(zero_fields) != set(_ZERO_FREEZE_FIELDS)
    ):
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "unsupported or incomplete policy profile")
    if row_model == "split_by_position_date":
        if policy_id != _SPLIT_POLICY_ID or set(exchanges) != set(_SPLIT_EXCHANGES):
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "split policy identity mismatch")
        offsets = policy.get("offsets")
        wires = policy.get("wire_offsets")
        if not isinstance(offsets, Mapping) or dict(offsets) != {
            "today": "close_today",
            "history": "close_yesterday",
        }:
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "split offsets are incomplete")
        if not isinstance(wires, Mapping) or dict(wires) != {
            "close_today": "3",
            "close_yesterday": "4",
        }:
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "split wire offsets are incomplete")
    else:
        if policy_id != _GENERIC_POLICY_ID or set(exchanges) != set(_GENERIC_EXCHANGES):
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "generic policy identity mismatch")
        offsets = policy.get("offsets")
        wires = policy.get("wire_offsets")
        if not isinstance(offsets, Mapping) or dict(offsets) != {"generic": "close"}:
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "generic offsets are incomplete")
        if not isinstance(wires, Mapping) or dict(wires) != {"close": "1"}:
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "generic wire offset is incomplete")
    common_profile = {
        "document_family": "CTPIIMini",
        "document_version": "1.4",
        "ordinary_nonzero_error": "O3B_FROZEN_ALLOCATION_UNPROVEN",
        "special_nonzero_error": "O3B_SPECIAL_POSITION_UNSUPPORTED",
        "forbidden_products": ["TAS", "combination_position"],
    }
    _require_profile_material(policy, common_profile)
    _validate_source_location(policy)
    if row_model == "split_by_position_date":
        _require_profile_material(
            policy,
            {
                "today_row": {
                    "PositionDate": "1",
                    "current_quantity": "Position",
                    "consistency": "TodayPosition == Position",
                },
                "history_row": {
                    "PositionDate": "2",
                    "current_quantity": "Position",
                    "consistency": "TodayPosition == 0",
                },
                "missing_age_row": "zero_only_under_complete_all_account_query_and_explicit_profile_rule",
                "allocation_priority": "caller_choice_today_first_or_yesterday_first_not_exchange_rule",
            },
        )
    else:
        _require_profile_material(
            policy,
            {
                "required_position_date": "1",
                "current_total": "Position",
                "current_today": "TodayPosition",
                "current_history": "Position - TodayPosition under this explicit combined-row semantic profile",
                "yd_position_role": "audit_only_static_start_of_day",
                "allocation_priority": "UNSPECIFIED",
                "possible_allocation": {
                    "today_min": "max(0, requested - history)",
                    "today_max": "min(requested, today)",
                    "history": "requested - allocated_today",
                    "integer": True,
                },
                "must_cover_all_possible_allocations_at_execution": True,
            },
        )
    return policy, row_model, frozenset(exchanges)


def _validate_context(evidence: Any, context: Any) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        _error("O3B_CURRENT_SCOPE_MISMATCH", "current context must be a mapping")
    context = dict(_snapshot(context))
    required = (
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "clock_domain_id",
        "session_binding_id",
        "native_profile",
        "evidence_source_hash",
        "expires_at_utc",
    )
    for name in required:
        if name not in context:
            _error("O3B_CURRENT_SCOPE_MISMATCH", name)
    for name in (
        "account_fingerprint",
        "clock_domain_id",
        "session_binding_id",
        "native_profile",
    ):
        _strict_metadata_text(context[name], name, "O3B_CURRENT_SCOPE_MISMATCH")
    if not _valid_day(context["trading_day"]):
        _error("O3B_CURRENT_SCOPE_MISMATCH", "invalid current trading day")
    if type(context["connection_generation"]) is not int or context["connection_generation"] <= 0:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "invalid connection generation")
    context["evidence_source_hash"] = _strict_hex(
        context["evidence_source_hash"],
        "evidence_source_hash",
        "O3B_CURRENT_SCOPE_MISMATCH",
    )
    context["expires_at_utc"] = _parse_utc(
        context["expires_at_utc"], "current expiry", "O3B_CLOCK_INVALID"
    )
    context["expires_monotonic_ns"] = _clock_from(
        context,
        ("expires_monotonic_ns",),
        ("expires_monotonic",),
        "current expiry monotonic",
    )
    evidence_hash = _read(evidence, ("source_hash",), default=_MISSING)
    if evidence_hash is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", "evidence source_hash")
    evidence_hash = _strict_hex(evidence_hash, "source_hash", "O3B_CURRENT_SCOPE_MISMATCH")
    if evidence_hash != context["evidence_source_hash"]:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "source digest differs from current context")
    evidence_clock = _read(evidence, ("clock_domain_id",), default=_MISSING)
    if evidence_clock is not _MISSING and evidence_clock != context["clock_domain_id"]:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "clock domain differs from current context")
    return context


def _collect_declared_account_identity(declared: dict[str, str], source: Any) -> None:
    if source is _MISSING or source is None:
        return
    for canonical, names in (
        ("BrokerID", ("BrokerID", "broker_id")),
        ("InvestorID", ("InvestorID", "investor_id")),
    ):
        value = _read(source, names, default=_MISSING)
        if value is _MISSING:
            continue
        _strict_text(value, canonical)
        prior = declared.get(canonical)
        if prior is not None and prior != value:
            _error(
                "O3B_POSITION_ROW_INVALID",
                f"conflicting source account identity: {canonical}",
            )
        declared[canonical] = value


def _source_status_sources(evidence: Any, envelope: Any) -> tuple[Any, ...]:
    sources: list[Any] = [evidence]
    if isinstance(envelope, Mapping):
        sources.append(envelope)
        for name in ("session_scope", "query_source"):
            nested = envelope.get(name, _MISSING)
            if isinstance(nested, Mapping):
                sources.append(nested)
    return tuple(sources)


def _validate_source_status(
    sources: Sequence[Any], *, completed: datetime, completed_mono: int
) -> None:
    """Reject explicit negative source facts before any scope proof is used."""

    for source in sources:
        for names in (("read_only_ready", "is_read_only_ready", "query_read_only_ready"),):
            ready = _read(source, names, default=_MISSING)
            if ready is _MISSING:
                continue
            if type(ready) is not bool:
                _error(
                    "O3B_POSITION_EVIDENCE_INCONSISTENT",
                    "read-only readiness requires an exact boolean",
                )
            if not ready:
                _error(
                    "O3B_POSITION_EVIDENCE_INCOMPLETE",
                    "source is explicitly not read-only ready",
                )
        for names in (
            ("complete", "evidence_complete", "query_complete"),
            ("is_last_seen", "query_is_last_seen"),
        ):
            value = _read(source, names, default=_MISSING)
            if value is _MISSING:
                continue
            if type(value) is not bool:
                _error(
                    "O3B_POSITION_EVIDENCE_INCONSISTENT",
                    "terminal status requires an exact boolean",
                )
            if not value:
                _error(
                    "O3B_POSITION_EVIDENCE_INCOMPLETE",
                    "source is explicitly incomplete or non-terminal",
                )
        for names in (
            ("timed_out", "query_timed_out"),
            ("unsupported", "query_unsupported"),
        ):
            value = _read(source, names, default=_MISSING)
            if value is _MISSING:
                continue
            if type(value) is not bool:
                _error(
                    "O3B_POSITION_EVIDENCE_INCONSISTENT",
                    "query status requires an exact boolean",
                )
            if value:
                _error(
                    "O3B_POSITION_EVIDENCE_INCOMPLETE",
                    "source reports timeout or unsupported query",
                )
        for names in (
            ("error_code", "query_error_code"),
            ("submit_code", "query_submit_code"),
        ):
            value = _read(source, names, default=_MISSING)
            if value is _MISSING:
                continue
            if value is not None and (type(value) is not int or value != 0):
                _error(
                    "O3B_POSITION_EVIDENCE_INCOMPLETE",
                    "source reports a query error",
                )
        for names, _seconds_names, label in (
            (("completed_at_utc", "completed_utc"), (), "source completion UTC"),
            (
                ("completed_monotonic_ns", "completed_mono_ns"),
                (),
                "source completion monotonic",
            ),
        ):
            if names[0].endswith("utc"):
                value = _read(source, names, default=_MISSING)
                if value is not _MISSING and _parse_utc(value, label) != completed:
                    _error(
                        "O3B_POSITION_EVIDENCE_INCONSISTENT",
                        "source completion UTC differs from query completion",
                    )
            else:
                ns_value = _read(source, names, default=_MISSING)
                seconds_value = _read(
                    source, ("completed_monotonic", "completed_mono"), default=_MISSING
                )
                if ns_value is not _MISSING or seconds_value is not _MISSING:
                    actual = _clock_from(
                        source,
                        names,
                        ("completed_monotonic", "completed_mono"),
                        label,
                    )
                    if actual != completed_mono:
                        _error(
                            "O3B_POSITION_EVIDENCE_INCONSISTENT",
                            "source completion monotonic differs from query completion",
                        )


def _extract_evidence(
    evidence: Any,
    context: Mapping[str, Any],
    supported_exchanges: frozenset[str],
    row_model: str,
) -> tuple[dict[str, Any], tuple[_PositionRow, ...]]:
    if isinstance(evidence, (list, tuple)):
        _error(
            "O3B_POSITION_EVIDENCE_INCOMPLETE",
            "position evidence requires query metadata",
        )
    records = _read(evidence, ("records", "rows"), default=_MISSING)
    if records is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", "position records")
    if type(records) not in (list, tuple):
        _error("O3B_POSITION_EVIDENCE_INCOMPLETE", "records must be a sequence")

    request_type = _read(evidence, ("request_type",), default="positions")
    if request_type != "positions":
        _error("O3B_CURRENT_SCOPE_MISMATCH", "position planner requires positions query")
    request_id = _read(evidence, ("query_request_id", "request_id"), default=_MISSING)
    if request_id is _MISSING or type(request_id) is not int or request_id <= 0:
        _error("O3B_REQUIRED_FIELD_MISSING", "query request id")
    account = _read(evidence, ("account_fingerprint",), default=_MISSING)
    generation = _read(evidence, ("connection_generation",), default=_MISSING)
    trading_day = _read(evidence, ("trading_day",), default=_MISSING)
    if account is _MISSING or generation is _MISSING or trading_day is _MISSING:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "evidence scope is incomplete")
    if (
        type(account) is not str
        or not account
        or account != account.strip()
        or type(generation) is not int
        or generation <= 0
    ):
        _error("O3B_CURRENT_SCOPE_MISMATCH", "evidence scope has invalid types")
    if account != context["account_fingerprint"] or generation != context["connection_generation"]:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "evidence scope differs from current context")
    if trading_day != context["trading_day"]:
        _error(
            "O3B_CURRENT_SCOPE_MISMATCH",
            "evidence trading day differs from current context",
        )

    complete = _read(
        evidence, ("complete", "evidence_complete", "query_complete"), default=_MISSING
    )
    terminal = _read(evidence, ("is_last_seen", "query_is_last_seen"), default=_MISSING)
    envelope = _read(evidence, ("query_envelope",), default=_MISSING)
    if terminal is _MISSING and isinstance(envelope, Mapping):
        terminal = envelope.get("is_last_seen", _MISSING)
    if complete is not True or terminal is not True:
        _error(
            "O3B_POSITION_EVIDENCE_INCOMPLETE",
            "query is not a successful terminal result",
        )
    for name in ("timed_out", "unsupported"):
        if _read(evidence, (name,), default=False) is not False:
            _error("O3B_POSITION_EVIDENCE_INCOMPLETE", f"query {name}")
    for name in ("error_code", "submit_code"):
        code = _read(evidence, (name,), default=None)
        if code not in (None, 0):
            _error("O3B_POSITION_EVIDENCE_INCOMPLETE", f"query {name}")

    completed = _read(evidence, ("completed_at_utc", "completed_utc"), default=_MISSING)
    source_expiry = _read(evidence, ("expires_at_utc", "source_expiry_utc"), default=_MISSING)
    if completed is _MISSING or source_expiry is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", "query clock timestamps")
    completed = _parse_utc(completed, "query completion")
    source_expiry = _parse_utc(source_expiry, "query expiry")
    completed_mono = _clock_from(
        evidence,
        ("completed_monotonic_ns", "completed_mono_ns"),
        ("completed_monotonic", "completed_mono"),
        "query completion monotonic",
    )
    source_expiry_mono = _clock_from(
        evidence,
        ("expires_monotonic_ns", "source_expiry_monotonic_ns"),
        ("expires_monotonic", "source_expiry_monotonic"),
        "query expiry monotonic",
    )
    clock_domain = _read(evidence, ("clock_domain_id",), default=_MISSING)
    if clock_domain is _MISSING or clock_domain != context["clock_domain_id"]:
        _error("O3B_CLOCK_INVALID", "query clock domain differs from current context")
    if source_expiry <= completed or source_expiry_mono <= completed_mono:
        _error("O3B_CLOCK_INVALID", "query expiry must follow completion")
    utc_ttl = (source_expiry - completed).total_seconds()
    mono_ttl = (source_expiry_mono - completed_mono) / 1_000_000_000
    if not isfinite(utc_ttl) or not isfinite(mono_ttl) or abs(utc_ttl - mono_ttl) > 1e-6:
        _error("O3B_CLOCK_INVALID", "query clock domains are not paired")
    if utc_ttl > MAX_SOURCE_TTL_SECONDS + 1e-6:
        _error("O3B_CLOCK_INVALID", "query source expiry exceeds issuer bound")

    status_sources = _source_status_sources(evidence, envelope)
    _validate_source_status(status_sources, completed=completed, completed_mono=completed_mono)

    if envelope is not _MISSING:
        if not isinstance(envelope, Mapping):
            _error("O3B_POSITION_EVIDENCE_INCOMPLETE", "query envelope is not a mapping")
        for names, expected in (
            (("request_id", "query_request_id"), request_id),
            (("account_fingerprint",), account),
            (("connection_generation",), generation),
        ):
            actual = _read(envelope, names, default=_MISSING)
            if actual is not _MISSING and actual != expected:
                _error("O3B_CURRENT_SCOPE_MISMATCH", "query envelope differs from evidence")
        nested_scope = _read(envelope, ("session_scope",), default=_MISSING)
        if nested_scope is not _MISSING and isinstance(nested_scope, Mapping):
            for name in ("account_fingerprint", "connection_generation", "trading_day"):
                actual = nested_scope.get(name, _MISSING)
                if actual is not _MISSING and actual != context[name]:
                    _error(
                        "O3B_CURRENT_SCOPE_MISMATCH",
                        "session envelope differs from current",
                    )
        nested_source = _read(envelope, ("query_source",), default=_MISSING)
        if nested_source is not _MISSING and isinstance(nested_source, Mapping):
            for name, expected in (
                ("request_type", request_type),
                ("request_id", request_id),
                ("account_fingerprint", account),
                ("connection_generation", generation),
                ("trading_day", trading_day),
                ("clock_domain_id", context["clock_domain_id"]),
            ):
                actual = nested_source.get(name, _MISSING)
                if actual is not _MISSING and actual != expected:
                    _error(
                        "O3B_CURRENT_SCOPE_MISMATCH",
                        "query source envelope differs from evidence",
                    )
    query_scope = _read(evidence, ("query_scope",), default=_MISSING)
    if query_scope is _MISSING and isinstance(envelope, Mapping):
        query_scope = envelope.get("query_scope", _MISSING)
    if query_scope is not _MISSING and query_scope != "all_account_positions":
        _error(
            "O3B_POSITION_EVIDENCE_INCOMPLETE",
            "query scope is not all-account positions",
        )
    empty_scope_proved = query_scope == "all_account_positions"
    if not empty_scope_proved and isinstance(envelope, Mapping):
        session_scope = envelope.get("session_scope", _MISSING)
        if isinstance(session_scope, Mapping):
            empty_scope_proved = all(
                session_scope.get(name, _MISSING) == context[name]
                for name in (
                    "account_fingerprint",
                    "connection_generation",
                    "trading_day",
                )
            )
    all_account_scope_proved = empty_scope_proved
    if not all_account_scope_proved and isinstance(envelope, Mapping):
        session_scope = envelope.get("session_scope", _MISSING)
        if isinstance(session_scope, Mapping):
            scope_matches = all(
                session_scope.get(name, _MISSING) == context[name]
                for name in (
                    "account_fingerprint",
                    "connection_generation",
                    "trading_day",
                )
            )
            ready = session_scope.get("read_only_ready", _MISSING)
            all_account_scope_proved = scope_matches and (ready is _MISSING or ready is True)
    if not all_account_scope_proved:
        _error(
            "O3B_POSITION_EVIDENCE_INCOMPLETE",
            "all-account query scope is not structurally proved",
        )

    declared_account: dict[str, str] = {}
    _collect_declared_account_identity(declared_account, evidence)
    _collect_declared_account_identity(declared_account, envelope)
    if isinstance(envelope, Mapping):
        _collect_declared_account_identity(
            declared_account, envelope.get("session_scope", _MISSING)
        )
        _collect_declared_account_identity(declared_account, envelope.get("query_source", _MISSING))

    parsed_rows: list[_PositionRow] = []
    for row in records:
        parsed = _parse_row(row, context["trading_day"], None)
        for account_field in ("BrokerID", "InvestorID"):
            row_account = parsed.raw[account_field]
            prior = declared_account.get(account_field)
            if prior is None:
                declared_account[account_field] = row_account
            elif row_account != prior:
                _error(
                    "O3B_POSITION_ROW_INVALID",
                    f"row account identity differs from source: {account_field}",
                )
        parsed_rows.append(parsed)
    identities = set()
    for row in parsed_rows:
        if row.identity in identities:
            _error("O3B_POSITION_ROW_DUPLICATE", "duplicate native position identity")
        identities.add(row.identity)
    for rows_for_account in (parsed_rows,):
        if rows_for_account:
            brokers = {row.raw["BrokerID"] for row in rows_for_account if "BrokerID" in row.raw}
            investors = {
                row.raw["InvestorID"] for row in rows_for_account if "InvestorID" in row.raw
            }
            if len(brokers) > 1 or len(investors) > 1:
                _error(
                    "O3B_POSITION_ROW_INVALID",
                    "rows contain multiple account identities",
                )
    return {
        "request_id": request_id,
        "account_fingerprint": account,
        "connection_generation": generation,
        "trading_day": trading_day,
        "completed_at_utc": completed,
        "expires_at_utc": source_expiry,
        "completed_monotonic_ns": completed_mono,
        "expires_monotonic_ns": source_expiry_mono,
        "clock_domain_id": clock_domain,
        "source_hash": _read(evidence, ("source_hash",)),
        "query_scope": query_scope,
    }, tuple(parsed_rows)


def _normalize_request(request: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        _error("O3B_REQUEST_INVALID", "request must be a mapping")
    request = dict(_snapshot(request))
    legs = request.get("legs", _MISSING)
    if legs is _MISSING:
        _error("O3B_REQUIRED_FIELD_MISSING", "request legs")
    if type(legs) not in (list, tuple):
        _error("O3B_REQUEST_INVALID", "request legs must be a sequence")
    if not legs:
        _error("O3B_REQUEST_EMPTY")
    if len(legs) > MAX_LEGS:
        _error("O3B_REQUEST_TOO_MANY_LEGS")

    candidate_id = _strict_metadata_text(
        request.get("candidate_id", _MISSING), "candidate_id", "O3B_REQUEST_INVALID"
    )
    candidate_sha = _strict_hex(request.get("candidate_sha256", _MISSING), "candidate_sha256")
    cycle_id = _strict_metadata_text(request.get("cycle_id", _MISSING), "cycle_id")
    role = _strict_metadata_text(request.get("execution_role", _MISSING), "execution_role")
    if role not in {"exit", "recovery_exit"}:
        _error("O3B_EXECUTION_ROLE_INVALID")
    request_expiry = _parse_utc(
        request.get("expires_at_utc", _MISSING), "request expiry", "O3B_CLOCK_INVALID"
    )
    request_expiry_mono = _clock_from(
        request,
        ("expires_monotonic_ns",),
        ("expires_monotonic",),
        "request expiry monotonic",
    )
    priority_a = request.get("priority", _MISSING)
    priority_b = request.get("allocation_priority", _MISSING)
    if priority_a is not _MISSING and priority_b is not _MISSING and priority_a != priority_b:
        _error("O3B_REQUEST_INVALID", "conflicting split allocation priorities")
    priority = (
        priority_a
        if priority_a is not _MISSING
        else (priority_b if priority_b is not _MISSING else "yesterday_first")
    )
    if priority not in {"today_first", "yesterday_first"}:
        _error("O3B_REQUEST_INVALID", "unknown split allocation priority")
    generic_age = request.get("generic_age_request")
    if generic_age is not None and generic_age not in {"only_today", "only_yesterday"}:
        _error("O3B_GENERIC_AGE_NOT_GUARANTEED")

    normalized_legs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw_leg in legs:
        if not isinstance(raw_leg, Mapping):
            _error("O3B_REQUEST_INVALID", "leg must be a mapping")
        leg = dict(_snapshot(raw_leg))
        for name in (
            "leg_id",
            "InstrumentID",
            "ExchangeID",
            "HedgeFlag",
            "position_side",
            "quantity",
        ):
            if name not in leg:
                _error("O3B_REQUIRED_FIELD_MISSING", f"leg {name}")
        leg_id = _strict_metadata_text(leg["leg_id"], "leg_id")
        instrument = _strict_metadata_text(leg["InstrumentID"], "InstrumentID")
        exchange = _strict_metadata_text(leg["ExchangeID"], "ExchangeID")
        hedge = _strict_metadata_text(leg["HedgeFlag"], "HedgeFlag")
        side = _strict_metadata_text(leg["position_side"], "position_side")
        if side not in _DIRECTION_TO_SIDE:
            _error("O3B_POSITION_SIDE_INVALID")
        quantity = leg["quantity"]
        if type(quantity) is not int or quantity <= 0:
            _error("O3B_QUANTITY_INVALID")
        key = (instrument, exchange, hedge, side)
        if key in seen:
            _error("O3B_REQUEST_LEG_DUPLICATE")
        seen.add(key)
        normalized_legs.append(
            {
                "leg_id": leg_id,
                "InstrumentID": instrument,
                "ExchangeID": exchange,
                "HedgeFlag": hedge,
                "position_side": side,
                "quantity": quantity,
            }
        )
    claim_a = request.get("request_sha256", _MISSING)
    claim_b = request.get("request_digest", _MISSING)
    if claim_a is not _MISSING and claim_b is not _MISSING and claim_a != claim_b:
        _error("O3B_REQUEST_INVALID", "conflicting request digests")
    claim = claim_a if claim_a is not _MISSING else claim_b
    if claim is not _MISSING:
        _strict_hex(claim, "request_sha256")
    material = dict(request)
    material.pop("allocation_priority", None)
    material.update(
        {
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_sha,
            "cycle_id": cycle_id,
            "execution_role": role,
            "expires_at_utc": request_expiry,
            "expires_monotonic_ns": request_expiry_mono,
            "priority": priority,
            "generic_age_request": generic_age,
            "legs": tuple(normalized_legs),
        }
    )
    material.pop("request_sha256", None)
    material.pop("request_digest", None)
    material["_claimed_request_sha256"] = claim
    return {
        "candidate_id": candidate_id,
        "candidate_sha256": candidate_sha,
        "cycle_id": cycle_id,
        "execution_role": role,
        "expires_at_utc": request_expiry,
        "expires_monotonic_ns": request_expiry_mono,
        "priority": priority,
        "generic_age_request": generic_age,
        "legs": tuple(normalized_legs),
        "request_material": material,
    }


def _request_digest(request: Mapping[str, Any]) -> str:
    supplied = request["request_material"].get("_claimed_request_sha256", _MISSING)
    payload = {
        key: value
        for key, value in request["request_material"].items()
        if key != "_claimed_request_sha256"
    }
    try:
        digest = sha256(canonical_ctp_close_plan_json(payload)).hexdigest()
    except CtpClosePlanError as exc:
        _error("O3B_REQUEST_INVALID", str(exc))
    if supplied is not _MISSING and supplied != digest:
        _error("O3B_REQUEST_INVALID", "claimed request digest differs from material")
    return digest


def _policy_digest(policy: Mapping[str, Any]) -> str:
    supplied_a = policy.get("policy_sha256", _MISSING)
    supplied_b = policy.get("policy_digest", _MISSING)
    if supplied_a is not _MISSING and supplied_b is not _MISSING and supplied_a != supplied_b:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", "conflicting policy digests")
    supplied = supplied_a if supplied_a is not _MISSING else supplied_b
    if supplied is not _MISSING:
        _strict_hex(supplied, "policy_sha256", "O3B_POLICY_INVALID_OR_MISMATCH")
    payload = dict(policy)
    payload.pop("policy_sha256", None)
    payload.pop("policy_digest", None)
    try:
        digest = sha256(canonical_ctp_close_plan_json(payload)).hexdigest()
    except CtpClosePlanError as exc:
        _error("O3B_POLICY_INVALID_OR_MISMATCH", str(exc))
    if supplied is not _MISSING and supplied != digest:
        _error(
            "O3B_POLICY_INVALID_OR_MISMATCH",
            "claimed policy digest differs from material",
        )
    return digest


def _row_key(row: _PositionRow) -> tuple[str, ...]:
    return row.source_row_keys  # type: ignore[return-value]


def _action_without_id(
    *,
    leg: Mapping[str, Any],
    row_keys: tuple[tuple[str, Any], ...],
    source_hash: str,
    policy_id: str,
    policy_version: str,
    policy_sha: str,
    request: Mapping[str, Any],
    offset: str,
    quantity: int,
    age_allocation: Mapping[str, Any],
    expiry_utc: str,
    expiry_mono: int,
) -> dict[str, Any]:
    return {
        "leg_id": leg["leg_id"],
        "InstrumentID": leg["InstrumentID"],
        "ExchangeID": leg["ExchangeID"],
        "HedgeFlag": leg["HedgeFlag"],
        "side": _DIRECTION_TO_SIDE[leg["position_side"]][1],
        "position_side": leg["position_side"],
        "offset": offset,
        "quantity": quantity,
        "quantity_unit": "contracts",
        "source_row_keys": row_keys,
        "source_hash": source_hash,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_sha256": policy_sha,
        "candidate_id": request["candidate_id"],
        "candidate_sha256": request["candidate_sha256"],
        "cycle_id": request["cycle_id"],
        "execution_role": request["execution_role"],
        "age_allocation": MappingProxyType(dict(age_allocation)),
        "expires_at_utc": expiry_utc,
        "expires_monotonic_ns": expiry_mono,
    }


@dataclass(frozen=True, slots=True)
class CtpCloseAction(Mapping[str, Any]):
    """One immutable close action in a structural plan."""

    action_id: str
    leg_id: str
    InstrumentID: str
    ExchangeID: str
    HedgeFlag: str
    side: str
    position_side: str
    offset: str
    quantity: int
    quantity_unit: str
    source_row_keys: tuple[tuple[str, Any], ...]
    source_hash: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    candidate_id: str
    candidate_sha256: str
    cycle_id: str
    execution_role: str
    age_allocation: Mapping[str, Any]
    expires_at_utc: str
    expires_monotonic_ns: int

    def __getitem__(self, key: str) -> Any:
        if key not in _ACTION_KEYS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(_ACTION_KEYS)

    def __len__(self) -> int:
        return len(_ACTION_KEYS)

    def as_dict(self) -> dict[str, Any]:
        return {key: _plain(getattr(self, key)) for key in _ACTION_KEYS}


@dataclass(frozen=True, slots=True)
class CtpClosePlan(Mapping[str, Any]):
    """Immutable structural close plan; never an execution authorization."""

    schema_version: str
    status: str
    planner_capability: str
    execution_eligible: bool
    execution_block_reason: str
    source_binding: Mapping[str, Any]
    policy_binding: Mapping[str, Any]
    request_binding: Mapping[str, Any]
    effective_expiry: Mapping[str, Any]
    limits: Mapping[str, int]
    actions: tuple[CtpCloseAction, ...]
    fee_roles_required: tuple[str, ...]
    plan_sha256: str

    @property
    def digest(self) -> str:
        return self.plan_sha256

    @property
    def capability(self) -> str:
        return self.planner_capability

    @property
    def execution_fee_roles_required(self) -> tuple[str, ...]:
        """Compatibility spelling used by the generic-age contract."""

        return self.fee_roles_required

    def __getitem__(self, key: str) -> Any:
        if key == "capability":
            return self.planner_capability
        if key in {"execution_fee_roles_required", "fee_roles"}:
            return self.fee_roles_required
        if key == "digest":
            return self.plan_sha256
        if key not in _PLAN_KEYS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(_PLAN_KEYS)

    def __len__(self) -> int:
        return len(_PLAN_KEYS)

    def as_dict(self) -> dict[str, Any]:
        return {key: _plain(getattr(self, key)) for key in _PLAN_KEYS}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain(item) for item in value]
    return value


def _make_action(data: Mapping[str, Any], action_id: str) -> CtpCloseAction:
    return CtpCloseAction(
        action_id=action_id,
        leg_id=data["leg_id"],
        InstrumentID=data["InstrumentID"],
        ExchangeID=data["ExchangeID"],
        HedgeFlag=data["HedgeFlag"],
        side=data["side"],
        position_side=data["position_side"],
        offset=data["offset"],
        quantity=data["quantity"],
        quantity_unit=data["quantity_unit"],
        source_row_keys=tuple(data["source_row_keys"]),
        source_hash=data["source_hash"],
        policy_id=data["policy_id"],
        policy_version=data["policy_version"],
        policy_sha256=data["policy_sha256"],
        candidate_id=data["candidate_id"],
        candidate_sha256=data["candidate_sha256"],
        cycle_id=data["cycle_id"],
        execution_role=data["execution_role"],
        age_allocation=MappingProxyType(dict(data["age_allocation"])),
        expires_at_utc=data["expires_at_utc"],
        expires_monotonic_ns=data["expires_monotonic_ns"],
    )


def _validate_deadlines(
    evidence_meta: Mapping[str, Any],
    request: Mapping[str, Any],
    context: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    now_utc: Any,
    monotonic_now: Any,
) -> tuple[datetime, int, datetime, int]:
    now = _parse_utc(now_utc, "validation now")
    now_mono = _clock_ns(monotonic_now, "validation monotonic", seconds=True)
    completed = evidence_meta["completed_at_utc"]
    completed_mono = evidence_meta["completed_monotonic_ns"]
    if now < completed or now_mono < completed_mono:
        _error("O3B_CLOCK_INVALID", "validation time precedes query completion")
    source_expiry = evidence_meta["expires_at_utc"]
    source_expiry_mono = evidence_meta["expires_monotonic_ns"]
    request_expiry = request["expires_at_utc"]
    request_expiry_mono = request["expires_monotonic_ns"]
    context_expiry = context["expires_at_utc"]
    context_expiry_mono = context["expires_monotonic_ns"]
    policy_has_expiry = "expires_at_utc" in policy
    if policy_has_expiry != ("expires_monotonic_ns" in policy):
        _error(
            "O3B_POLICY_INVALID_OR_MISMATCH",
            "policy deadline is not normalized as a pair",
        )
    for label, deadline_utc, deadline_mono in (
        ("request", request_expiry, request_expiry_mono),
        ("current context", context_expiry, context_expiry_mono),
    ):
        if deadline_utc <= completed or deadline_mono <= completed_mono:
            _error("O3B_CLOCK_INVALID", f"{label} deadline precedes completion")
        utc_delta = (deadline_utc - completed).total_seconds()
        mono_delta = (deadline_mono - completed_mono) / 1_000_000_000
        if abs(utc_delta - mono_delta) > 1e-6:
            _error("O3B_CLOCK_INVALID", f"{label} deadline clocks are not paired")
    deadlines_utc = [source_expiry, request_expiry, context_expiry]
    deadlines_mono = [source_expiry_mono, request_expiry_mono, context_expiry_mono]
    if policy_has_expiry:
        policy_expiry = policy["expires_at_utc"]
        policy_expiry_mono = policy["expires_monotonic_ns"]
        if policy_expiry <= completed or policy_expiry_mono <= completed_mono:
            _error("O3B_SOURCE_EXPIRED", "policy deadline has elapsed")
        utc_delta = (policy_expiry - completed).total_seconds()
        mono_delta = (policy_expiry_mono - completed_mono) / 1_000_000_000
        if abs(utc_delta - mono_delta) > 1e-6:
            _error("O3B_CLOCK_INVALID", "policy deadline clocks are not paired")
        deadlines_utc.append(policy_expiry)
        deadlines_mono.append(policy_expiry_mono)
    effective_utc = min(deadlines_utc)
    effective_mono = min(deadlines_mono)
    if effective_utc <= completed or effective_mono <= completed_mono:
        _error("O3B_SOURCE_EXPIRED", "effective deadline has elapsed")
    effective_ttl = (effective_utc - completed).total_seconds()
    effective_mono_ttl = (effective_mono - completed_mono) / 1_000_000_000
    if abs(effective_ttl - effective_mono_ttl) > 1e-6:
        _error("O3B_CLOCK_INVALID", "effective deadline clocks are not paired")
    if now >= effective_utc or now_mono >= effective_mono:
        _error("O3B_SOURCE_EXPIRED", "source or request deadline has elapsed")
    return effective_utc, effective_mono, now, now_mono


def _split_actions(
    leg: Mapping[str, Any],
    rows: Sequence[_PositionRow],
    request: Mapping[str, Any],
    *,
    source_hash: str,
    policy_id: str,
    policy_version: str,
    policy_sha: str,
    expiry_utc: str,
    expiry_mono: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    target = (
        leg["InstrumentID"],
        leg["ExchangeID"],
        _DIRECTION_TO_SIDE[leg["position_side"]][0],
        leg["HedgeFlag"],
    )
    matches = [
        row for row in rows if (row.instrument, row.exchange, row.direction, row.hedge) == target
    ]
    if not matches:
        if not rows:
            _error(
                "O3B_CLOSE_EXCEEDS_POSITION",
                "empty complete query has no closable position",
            )
        _error("O3B_TARGET_NOT_IN_SOURCE", "requested raw identity is absent")
    for row in matches:
        if row.position_date == "1" and row.today_position != row.position:
            _error("O3B_POSITION_ROW_INVALID", "today split row is inconsistent")
        if row.position_date == "2" and row.today_position != 0:
            _error("O3B_POSITION_ROW_INVALID", "history split row is inconsistent")
    _validate_freeze(matches)
    by_date = {row.position_date: row for row in matches}
    quantity = leg["quantity"]
    available = sum(row.position for row in matches)
    if quantity > available:
        _error("O3B_CLOSE_EXCEEDS_POSITION", "requested quantity exceeds current position")
    order = ("2", "1") if request["priority"] == "yesterday_first" else ("1", "2")
    actions: list[dict[str, Any]] = []
    remaining = quantity
    for position_date in order:
        row = by_date.get(position_date)
        if row is None:
            continue
        take = min(remaining, row.position)
        if not take:
            continue
        offset = "close_yesterday" if position_date == "2" else "close_today"
        actions.append(
            _action_without_id(
                leg=leg,
                row_keys=row.source_row_keys,
                source_hash=source_hash,
                policy_id=policy_id,
                policy_version=policy_version,
                policy_sha=policy_sha,
                request=request,
                offset=offset,
                quantity=take,
                age_allocation={
                    "today": take if position_date == "1" else 0,
                    "history": take if position_date == "2" else 0,
                },
                expiry_utc=expiry_utc,
                expiry_mono=expiry_mono,
            )
        )
        remaining -= take
    if remaining:
        _error("O3B_CLOSE_EXCEEDS_POSITION", "position split could not satisfy request")
    return actions, tuple(action["offset"] for action in actions)


def _generic_actions(
    leg: Mapping[str, Any],
    rows: Sequence[_PositionRow],
    request: Mapping[str, Any],
    *,
    source_hash: str,
    policy_id: str,
    policy_version: str,
    policy_sha: str,
    expiry_utc: str,
    expiry_mono: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    target = (
        leg["InstrumentID"],
        leg["ExchangeID"],
        _DIRECTION_TO_SIDE[leg["position_side"]][0],
        leg["HedgeFlag"],
    )
    matches = [
        row for row in rows if (row.instrument, row.exchange, row.direction, row.hedge) == target
    ]
    if not matches:
        if not rows:
            _error(
                "O3B_CLOSE_EXCEEDS_POSITION",
                "empty complete query has no closable position",
            )
        _error("O3B_TARGET_NOT_IN_SOURCE", "requested raw identity is absent")
    if len(matches) != 1 or matches[0].position_date != "1":
        _error("O3B_POSITION_ROW_INVALID", "generic policy requires one combined row")
    row = matches[0]
    _validate_freeze((row,))
    total = row.position
    today = row.today_position
    history = total - today
    quantity = leg["quantity"]
    if quantity > total:
        _error("O3B_CLOSE_EXCEEDS_POSITION", "requested quantity exceeds current position")
    today_min = max(0, quantity - history)
    today_max = min(quantity, today)
    age_request = request["generic_age_request"]
    if age_request == "only_today" and not (today_min == today_max == quantity):
        _error("O3B_GENERIC_AGE_NOT_GUARANTEED")
    if age_request == "only_yesterday" and not (today_min == today_max == 0):
        _error("O3B_GENERIC_AGE_NOT_GUARANTEED")
    allocation = {
        "today_min": today_min,
        "today_max": today_max,
        "history_equals_quantity_minus_today": True,
    }
    actions = [
        _action_without_id(
            leg=leg,
            row_keys=row.source_row_keys,
            source_hash=source_hash,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_sha=policy_sha,
            request=request,
            offset="close",
            quantity=quantity,
            age_allocation=allocation,
            expiry_utc=expiry_utc,
            expiry_mono=expiry_mono,
        )
    ]
    # A generic close is always needed; a possible today allocation requires
    # its distinct fee role.  The actual venue allocation is intentionally
    # left for the later session/transport owner.
    fees = ("close", "close_today") if today_max else ("close",)
    return actions, fees


def build_ctp_close_plan(
    evidence: Any,
    request: Any,
    policy: Any,
    *,
    current_context: Mapping[str, Any],
    now_utc: Any,
    monotonic_now: Any,
) -> CtpClosePlan:
    """Build a bounded, immutable synthetic CTP close plan.

    The function intentionally accepts structural evidence mappings as well as
    O3a objects exposing the same public fields.  It never upgrades either one
    to trusted provenance, and therefore every successful result remains
    ``execution_eligible=False`` with ``STRUCTURAL_ONLY`` capability.
    """

    validated_context = _validate_context(evidence, current_context)
    validated_policy, row_model, supported_exchanges = _validate_policy(policy)
    if validated_context["native_profile"] != validated_policy["native_profile"]:
        _error("O3B_CURRENT_SCOPE_MISMATCH", "context native profile differs from policy")
    evidence_meta, rows = _extract_evidence(
        evidence, validated_context, supported_exchanges, row_model
    )
    normalized_request = _normalize_request(request, validated_context)
    for leg in normalized_request["legs"]:
        if leg["ExchangeID"] not in supported_exchanges:
            _error("O3B_POLICY_INVALID_OR_MISMATCH", "leg exchange is outside policy")
    effective_utc, effective_mono, _, _ = _validate_deadlines(
        evidence_meta,
        normalized_request,
        validated_context,
        validated_policy,
        now_utc=now_utc,
        monotonic_now=monotonic_now,
    )

    policy_sha = _policy_digest(validated_policy)
    request_sha = _request_digest(normalized_request)
    source_hash = evidence_meta["source_hash"]
    policy_version = validated_policy["version"]
    expiry_utc = _format_utc(effective_utc)
    raw_actions: list[dict[str, Any]] = []
    fee_roles: list[str] = []
    for leg in normalized_request["legs"]:
        if row_model == "split_by_position_date":
            actions, fees = _split_actions(
                leg,
                rows,
                normalized_request,
                source_hash=source_hash,
                policy_id=validated_policy["policy_id"],
                policy_version=policy_version,
                policy_sha=policy_sha,
                expiry_utc=expiry_utc,
                expiry_mono=effective_mono,
            )
        else:
            actions, fees = _generic_actions(
                leg,
                rows,
                normalized_request,
                source_hash=source_hash,
                policy_id=validated_policy["policy_id"],
                policy_version=policy_version,
                policy_sha=policy_sha,
                expiry_utc=expiry_utc,
                expiry_mono=effective_mono,
            )
        raw_actions.extend(actions)
        for fee in fees:
            if fee not in fee_roles:
                fee_roles.append(fee)
    if not raw_actions or len(raw_actions) > MAX_ACTIONS:
        _error("O3B_ACTION_LIMIT_EXCEEDED", "bounded action set violated")

    source_binding = MappingProxyType(
        {
            "source_hash": source_hash,
            "request_id": evidence_meta["request_id"],
            "account_fingerprint": validated_context["account_fingerprint"],
            "trading_day": validated_context["trading_day"],
            "connection_generation": validated_context["connection_generation"],
            "clock_domain_id": validated_context["clock_domain_id"],
            "session_binding_id": validated_context["session_binding_id"],
        }
    )
    policy_binding = MappingProxyType(
        {
            "policy_id": validated_policy["policy_id"],
            "policy_version": policy_version,
            "policy_sha256": policy_sha,
            "execution_policy_verified": False,
        }
    )
    request_binding = MappingProxyType(
        {
            "candidate_id": normalized_request["candidate_id"],
            "candidate_sha256": normalized_request["candidate_sha256"],
            "cycle_id": normalized_request["cycle_id"],
            "execution_role": normalized_request["execution_role"],
            "request_sha256": request_sha,
        }
    )
    effective_expiry = MappingProxyType(
        {
            "expires_at_utc": expiry_utc,
            "expires_monotonic_ns": effective_mono,
        }
    )
    limits = MappingProxyType({"legs_max": MAX_LEGS, "actions_max": MAX_ACTIONS})
    digest_actions = [dict(action) for action in raw_actions]
    digest_payload = {
        "schema_version": SCHEMA_VERSION,
        "execution_eligible": False,
        "source_binding": source_binding,
        "policy_binding": policy_binding,
        "request_binding": request_binding,
        "effective_expiry": effective_expiry,
        "limits": limits,
        "actions_without_action_id": digest_actions,
    }
    try:
        plan_sha = sha256(canonical_ctp_close_plan_json(digest_payload)).hexdigest()
    except CtpClosePlanError as exc:
        _error("O3B_CANONICAL_VALUE_INVALID", str(exc))
    close_actions = tuple(
        _make_action(
            raw,
            sha256(f"{plan_sha}:{index}".encode()).hexdigest(),
        )
        for index, raw in enumerate(raw_actions)
    )
    return CtpClosePlan(
        schema_version=SCHEMA_VERSION,
        status="PLANNED",
        planner_capability=PLANNER_CAPABILITY,
        execution_eligible=False,
        execution_block_reason=EXECUTION_BLOCK_REASON,
        source_binding=source_binding,
        policy_binding=policy_binding,
        request_binding=request_binding,
        effective_expiry=effective_expiry,
        limits=limits,
        actions=close_actions,
        fee_roles_required=tuple(fee_roles),
        plan_sha256=plan_sha,
    )


__all__ = [
    "CtpCloseAction",
    "CtpClosePlan",
    "CtpClosePlanError",
    "EXECUTION_BLOCK_REASON",
    "PLANNER_CAPABILITY",
    "SCHEMA_VERSION",
    "build_ctp_close_plan",
    "canonical_ctp_close_plan_json",
]
