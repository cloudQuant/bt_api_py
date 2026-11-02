"""Transport-independent result mapping for the existing public BtApi methods.

Native RequestData containers and their public getters are the first source.
Vendor dictionaries are handled here only when a field has no common getter.
No execution loop, account policy or consumer-framework object belongs here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, cast

from bt_api_base.exceptions import InvalidOrderError
from bt_api_base.order_provenance_fields import (
    PROVIDER_SCOPED_ORDER_PROVENANCE_SCHEMAS,
)
from bt_api_base.order_observation_contracts import (
    NormalizationProvenance as NormalizationProvenance,
    NormalizedRecord,
    NormalizedRecords,
)

from ._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from ._contracts.models import FeeSchedule, Freshness, FundingSnapshot, InstrumentSpec
from ._ctp_market_attestation import (
    _ctp_quote_v2_parent_attestation_matches,
    _ctp_quote_v2_trusted_text,
)


from .venue_normalization_policy import normalization_policy

_CLOCK_DOMAIN_SEED = f"{socket.gethostname()}:{os.getpid()}:{time.monotonic_ns()}"
CLOCK_DOMAIN_ID = hashlib.sha256(_CLOCK_DOMAIN_SEED.encode()).hexdigest()[:24]
_EXPLICIT_IDENTITY_FIELDS = "_explicit_identity_fields"
_ORDER_V2_SOURCE_FIELDS = frozenset(
    {"order_revision", "fill_revision", "fee_amount", "fee_currency"}
)
_ORDER_V2_ENVELOPE_FIELDS = frozenset({"fill_watermark"})


class _NormalizedSourceRows(list):
    """Internal rows() result carrying source alignment facts for order APIs."""

    def __init__(self, values=(), *, collection_provenance=None, row_provenance=(), aligned=True):
        super().__init__(values)
        self.collection_provenance = collection_provenance
        self.row_provenance = tuple(row_provenance)
        self.source_alignment_valid = aligned

def _order_source_provenance(raw, operation, row=None, *, policy):
    """Project source facts using the selected route's field contract."""
    from bt_api_base.order_provenance_projection import project_order_source_provenance

    return project_order_source_provenance(
        raw, operation, row,
        source_value_fields=policy.order_provenance_source_fields,
        envelope_value_fields=policy.order_provenance_envelope_fields,
        v2_source_fields=_ORDER_V2_SOURCE_FIELDS,
        v2_envelope_fields=_ORDER_V2_ENVELOPE_FIELDS,
        schema_v1=policy.order_provenance_schemas[0],
        schema_v2=policy.order_provenance_schemas[1],
    )


_TERMINAL = {"completed", "canceled", "expired", "rejected"}
_GETTERS = {
    "symbol": ("get_symbol_name", "get_position_symbol_name", "get_order_symbol_name"),
    "exchange": ("get_exchange_name",),
    "asset_type": ("get_asset_type",),
    "event": ("get_event",),
    "timestamp": ("get_server_time",),
    "local_time": ("get_local_update_time",),
    "account_id": ("get_account_id",),
    "order_id": ("get_order_id",),
    "client_order_id": ("get_client_order_id",),
    "size": ("get_order_size", "get_position_volume", "get_trade_volume"),
    "filled": ("get_executed_qty",),
    "avg_price": ("get_order_avg_price", "get_avg_price"),
    "side": ("get_order_side", "get_trade_side"),
    "status": ("get_order_status",),
    "order_type": ("get_order_type",),
    "position_side": ("get_position_side",),
    "position_id": ("get_position_id",),
    "direction": ("get_position_direction",),
    "today": ("get_today_position",),
    "yesterday": ("get_yesterday_position",),
    "offset": ("get_order_offset", "get_trade_offset"),
    "exchange_id": ("get_order_exchange_id",),
    "front_id": ("get_front_id",),
    "session_id": ("get_session_id",),
    "order_ref": ("get_order_ref",),
    "trade_id": ("get_trade_id",),
    "trade_price": ("get_trade_price",),
    "fee": ("get_trade_fee",),
    "fee_currency": ("get_trade_fee_symbol",),
    "price": ("get_last_price", "get_last", "get_order_price"),
    "volume": ("get_last_volume", "get_volume"),
    "open": ("get_open_price",),
    "high": ("get_high_price",),
    "low": ("get_low_price",),
    "close": ("get_close_price",),
    "bid_price": ("get_bid_price",),
    "ask_price": ("get_ask_price",),
    "bid_price_list": ("get_bid_price_list",),
    "ask_price_list": ("get_ask_price_list",),
    "bid_volume_list": ("get_bid_volume_list",),
    "ask_volume_list": ("get_ask_volume_list",),
    "cash": ("get_total_available_margin", "get_available_margin"),
    "value": ("get_total_margin", "get_margin"),
    "currency": ("get_currency",),
}


def pick(row, *names, default=None):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value.value if isinstance(value, Enum) else value
    return default


def number(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_number")
    return result


def decimal_number(value, default=None):
    """Convert a native numeric string without passing through binary float."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid_decimal_number") from exc
    if not result.is_finite():
        raise ValueError("non_finite_number")
    return result


def _datetime_from_seconds(value):
    value = seconds(value)
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _freshness(row, *, source="exchange", stale=False, reason=None):
    observed = _datetime_from_seconds(
        pick(row, "received_wall_time", "local_time", "local_update_time", "receive_time")
    ) or datetime.now(UTC)
    return Freshness(source=source, observed_at=observed, stale=stale, stale_reason=reason)


def _fingerprint(values):
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def instrument_spec(result, exchange_name, symbol):
    """Build the strict Decimal public instrument contract from venue rules."""
    source = rows(result, "get_instrument_spec", exchange_name=exchange_name)
    policy = normalization_policy(exchange_name)
    fields = policy.instrument_spec_fields
    candidates = [
        row
        for row in source
        if str(pick(row, *fields["symbol"], default="")) == symbol
    ]
    if not candidates:
        raise NormalizedApiError("get_instrument_spec", "instrument_not_found")
    row = candidates[0]
    from .provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

    try:
        projector = load_route_entrypoint("bt_api.instrument_rules_projections", exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "get_instrument_spec", detail=f"{exchange_name} has no strict instrument mapper"
        ) from None
    required, max_quantity_raw, quantity_unit, min_notional_raw, no_notional_floor = projector(
        row, pick=pick, normalized_error=NormalizedApiError
    )
    missing = sorted(name for name, value in required.items() if value in (None, ""))
    if missing:
        raise NormalizedApiError(
            "get_instrument_spec", "missing_instrument_rules_" + "_".join(missing)
        )
    decimals = {name: decimal_number(value) for name, value in required.items()}
    if any(value is None or value <= 0 for value in decimals.values()):
        raise NormalizedApiError("get_instrument_spec", "invalid_instrument_rules")
    parts = symbol.split("-") if "-" in symbol else []
    base = str(
        pick(
            row,
            *fields["base_currency"],
            default=parts[0] if parts else "",
        )
    )
    quote = str(
        pick(
            row,
            *fields["quote_currency"],
            default=parts[1] if len(parts) > 1 else "",
        )
    )
    if not base or not quote:
        raise NormalizedApiError("get_instrument_spec", "missing_currency_rules")
    status = str(pick(row, "status", "state", default="unknown"))
    rule_values = {
        "exchange_name": exchange_name,
        "symbol": symbol,
        **{name: str(value) for name, value in decimals.items()},
        "max_quantity": str(max_quantity_raw or ""),
        "min_notional": str(min_notional_raw or ""),
        "status": status,
    }
    if min_notional_raw not in (None, ""):
        min_notional = decimal_number(min_notional_raw)
    elif no_notional_floor:
        # The provider declares no separate minimum notional floor.
        min_notional = Decimal("0")
    else:
        min_notional = None
    max_quantity = decimal_number(max_quantity_raw) if max_quantity_raw not in (None, "") else None
    if max_quantity is not None and max_quantity <= 0:
        raise NormalizedApiError("get_instrument_spec", "invalid_max_quantity_rule")
    return InstrumentSpec(
        exchange_name=exchange_name,
        symbol=symbol,
        asset_type=str(
            pick(row, *fields["asset_type"], default=exchange_name.partition("___")[2])
        ).lower(),
        base_currency=base,
        quote_currency=quote,
        contract_type=str(
            pick(row, *fields["contract_type"], default="unknown")
        ).lower(),
        linear=bool(
            pick(
                row,
                "linear",
                default=(
                    row.get(policy.instrument_spec_linear_type_field) == "linear"
                    if policy.instrument_spec_linear_type_field is not None
                    and policy.instrument_spec_linear_type_field in row
                    else True
                ),
            )
        ),
        contract_value=decimals["contract_value"],
        contract_multiplier=decimals["contract_multiplier"],
        price_tick=decimals["price_tick"],
        quantity_step=decimals["quantity_step"],
        min_quantity=decimals["min_quantity"],
        max_quantity=max_quantity,
        min_notional=min_notional,
        quantity_unit=quantity_unit,
        status=status,
        freshness=_freshness(row),
        raw_rule_fingerprint=_fingerprint(rule_values),
        raw=rule_values,
    )


def _fee_projection(exchange_name):
    """Bind the selected provider's fee projection to neutral SDK helpers."""
    from bt_api_base.fee_projection import legacy_fee_projection
    from .provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

    try:
        factory = load_route_entrypoint("bt_api.fee_projections", exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "get_fee_schedule", detail="provider fee projection unavailable"
        ) from None
    if factory is legacy_fee_projection:
        return None
    return factory(
        {
            "rows": rows,
            "pick": pick,
            "decimal_number": decimal_number,
            "unavailable_fee_schedule": _unavailable_fee_schedule,
            "freshness": _freshness,
            "fee_schedule_type": FeeSchedule,
            "normalized_error": NormalizedApiError,
            "unsupported_error": CapabilityNotSupportedError,
        }
    )


def _unavailable_fee_schedule(exchange_name, symbol, account_id, row, reason):
    return FeeSchedule(
        exchange_name=exchange_name,
        symbol=symbol,
        account_id=account_id,
        maker_rate=None,
        taker_rate=None,
        currency=None,
        source="unavailable",
        freshness=_freshness(row, source="unavailable", stale=True, reason=reason),
        available=False,
        unavailable_reason=reason,
        raw={},
    )


def fee_schedule(
    result,
    exchange_name,
    symbol,
    account_id,
    *,
    expected_group_id=None,
    metadata_currency=None,
):
    projection = _fee_projection(exchange_name)
    if projection is not None:
        return projection.schedule(
            result, exchange_name, symbol, account_id,
            expected_group_id=expected_group_id, metadata_currency=metadata_currency,
        )
    source = rows(result, "get_fee_schedule", exchange_name=exchange_name)
    venue = exchange_name.partition("___")[0].upper()
    row = source[0] if source else {}
    policy = normalization_policy(exchange_name)
    selected_group_id = None
    native_maker = None
    native_taker = None
    native_maker = pick(row, *policy.fee_maker_fields)
    native_taker = pick(row, *policy.fee_taker_fields)
    if native_maker in (None, "") or native_taker in (None, ""):
        return _unavailable_fee_schedule(exchange_name, symbol, account_id, row, "fee_rate_missing")
    raw = {
        "selected_group_id": selected_group_id,
        "maker_native_rate": native_maker,
        "taker_native_rate": native_taker,
    }
    try:
        maker_rate = abs(decimal_number(native_maker))
        taker_rate = abs(decimal_number(native_taker))
    except ValueError:
        return _unavailable_fee_schedule(exchange_name, symbol, account_id, row, "fee_rate_invalid")
    if maker_rate is None or taker_rate is None:
        return _unavailable_fee_schedule(exchange_name, symbol, account_id, row, "fee_rate_missing")
    return FeeSchedule(
        exchange_name=exchange_name,
        symbol=symbol,
        account_id=account_id,
        maker_rate=maker_rate,
        taker_rate=taker_rate,
        currency=pick(row, *policy.fee_currency_fields, default=metadata_currency),
        source=f"{venue.lower()}_get_fee",
        freshness=_freshness(row),
        raw=raw,
    )


def _funding_raw(row, exchange_name):
    fields = normalization_policy(exchange_name).funding_raw_fields
    return {key: row[key] for key in fields if key in row}


def _funding_datetime(value):
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("funding_datetime_must_be_timezone_aware")
        return value.astimezone(UTC)
    return _datetime_from_seconds(value)


def _unavailable_funding_snapshot(
    exchange_name,
    symbol,
    row,
    reason,
    *,
    freshness=None,
    rate=None,
    next_funding_time=None,
    settlement_interval_seconds=None,
):
    observed_at = freshness.observed_at if freshness is not None else datetime.now(UTC)
    return FundingSnapshot(
        exchange_name=exchange_name,
        symbol=symbol,
        rate=rate,
        next_funding_time=next_funding_time,
        settlement_interval_seconds=settlement_interval_seconds,
        source="unavailable",
        freshness=Freshness(
            source="unavailable",
            observed_at=observed_at,
            stale=True,
            stale_reason=reason,
        ),
        available=False,
        unavailable_reason=reason,
        raw=_funding_raw(row, exchange_name),
    )


def funding_snapshot(result, exchange_name, symbol):
    source = rows(result, "get_funding_snapshot", exchange_name=exchange_name)
    row = source[0] if source else {}
    observed_raw = pick(
        row,
        "received_wall_time",
        "local_time",
        "local_update_time",
        "receive_time",
    )
    if isinstance(observed_raw, datetime) and observed_raw.utcoffset() is None:
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_observed_at_invalid",
        )
    try:
        freshness = _freshness(row)
    except (OverflowError, OSError, TypeError, ValueError):
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_observed_at_invalid",
        )

    policy = normalization_policy(exchange_name)
    rate_raw = pick(row, *policy.funding_rate_fields)
    next_time_raw = pick(
        row,
        "next_funding_time",
        *policy.funding_time_fields,
    )
    if rate_raw in (None, "") or next_time_raw in (None, ""):
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_fields_missing",
            freshness=freshness,
        )
    try:
        rate = decimal_number(rate_raw)
        next_funding_time = _funding_datetime(next_time_raw)
    except (OverflowError, OSError, TypeError, ValueError):
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_fields_invalid",
            freshness=freshness,
        )

    interval_raw = pick(row, "settlement_interval_seconds", "funding_interval_seconds")
    interval_hours = pick(row, *policy.funding_interval_hours_fields)
    if interval_raw in (None, "") and interval_hours in (None, ""):
        funding_time = pick(row, *policy.funding_interval_start_fields)
        following_funding_time = pick(row, *policy.funding_interval_end_fields)
        if funding_time in (None, "") or following_funding_time in (None, ""):
            return _unavailable_funding_snapshot(
                exchange_name,
                symbol,
                row,
                "funding_interval_missing",
                freshness=freshness,
                rate=rate,
                next_funding_time=next_funding_time,
            )
        try:
            first = _funding_datetime(funding_time)
            second = _funding_datetime(following_funding_time)
            interval_value = Decimal(str((second - first).total_seconds()))
        except (OverflowError, OSError, TypeError, ValueError):
            interval_value = None
    else:
        try:
            interval_value = (
                decimal_number(interval_raw)
                if interval_raw not in (None, "")
                else decimal_number(interval_hours) * Decimal(3600)
            )
        except (TypeError, ValueError):
            interval_value = None
    if (
        interval_value is None
        or interval_value <= 0
        or interval_value != interval_value.to_integral_value()
    ):
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_interval_invalid",
            freshness=freshness,
            rate=rate,
            next_funding_time=next_funding_time,
        )
    interval_seconds = int(interval_value)
    if next_funding_time <= freshness.observed_at:
        return _unavailable_funding_snapshot(
            exchange_name,
            symbol,
            row,
            "funding_schedule_stale",
            freshness=freshness,
            rate=rate,
            next_funding_time=next_funding_time,
            settlement_interval_seconds=interval_seconds,
        )
    return FundingSnapshot(
        exchange_name=exchange_name,
        symbol=symbol,
        rate=rate,
        next_funding_time=next_funding_time,
        settlement_interval_seconds=interval_seconds,
        source=f"{exchange_name.partition('___')[0].lower()}_funding_rate",
        freshness=freshness,
        raw=_funding_raw(row, exchange_name),
    )


def seconds(value):
    if isinstance(value, datetime):
        return value.timestamp()
    result = number(value)
    if result is None:
        return None
    if result > 1e17:
        return result / 1e9
    if result > 1e14:
        return result / 1e6
    return result / 1000 if result > 1e11 else result


def _safe_code(value):
    if isinstance(value, Enum):
        value = value.name
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text) else "invalid_error_code"


def _provider_error_classification(exchange_name, code):
    """Select venue-owned codes; an absent classifier keeps outcomes uncertain."""
    from .provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

    venue = str(exchange_name or "").partition("___")[0].upper()
    if not venue:
        return None, False, False
    try:
        classifier = load_route_entrypoint("bt_api.error_classifiers", venue, optional=True)
    except ProviderEntryPointError:
        return None, False, False
    if classifier is None:
        return None, False, False
    return classifier(code)


def _error_classification(exchange_name, code):
    category, mismatch, _ = _provider_error_classification(exchange_name, code)
    return category, mismatch


def _is_definite_reject(exchange_name, code):
    _, _, definite_reject = _provider_error_classification(exchange_name, code)
    return definite_reject


def _detach_error(error):
    """Remove exception chaining that could retain transport secrets."""
    error.__cause__ = None
    error.__context__ = None
    error.__traceback__ = None
    error.__suppress_context__ = True
    return error


def _response_error(raw, operation, *, exchange_name=None, write=False):
    if not isinstance(raw, dict):
        return None
    code = pick(raw, "code", default="0")
    try:
        policy = normalization_policy(exchange_name)
        item_fields = policy.response_error_item_fields
        if type(item_fields) is not tuple or any(
            type(field) is not str or not field for field in item_fields
        ):
            raise ValueError("invalid provider response error fields")
        policy_unavailable = False
    except Exception:
        item_fields = ()
        policy_unavailable = True
    no_item_error = object()
    for row in raw.get("data", []) if isinstance(raw.get("data"), list) else []:
        if not isinstance(row, dict):
            continue
        item_error = next(
            (row[field] for field in item_fields if field in row and str(row[field]) != "0"),
            no_item_error,
        )
        if item_error is not no_item_error:
            code = item_error
            break
    if str(code) in {"0", "200"}:
        if policy_unavailable:
            return NormalizedApiError(
                operation, "provider_response_policy_unavailable", execution_unknown=write
            )
        return None
    code = _safe_code(code)
    definite_reject = _is_definite_reject(exchange_name, code)
    category, mismatch = _error_classification(exchange_name, code)
    return NormalizedApiError(
        operation,
        code,
        execution_unknown=write and not definite_reject,
        definite_reject=write and definite_reject,
        category=category,
        environment_mismatch_possible=mismatch,
    )


def normalize_error(exc, operation, *, exchange_name=None, write=False):
    if isinstance(exc, NormalizedApiError):
        code = _safe_code(exc.code)
        if write and code.lstrip("-").isdigit():
            definite_reject = _is_definite_reject(exchange_name, code)
            exc.execution_unknown = not definite_reject
            exc.definite_reject = definite_reject
        category, mismatch = _error_classification(exchange_name, exc.code)
        if category is not None:
            exc.category = category
            exc.environment_mismatch_possible = mismatch
        return _detach_error(exc)
    if isinstance(exc, CapabilityNotSupportedError):
        return _detach_error(exc)
    if isinstance(exc, InvalidOrderError):
        return _detach_error(NormalizedApiError(operation, "invalid_order", definite_reject=write))
    exchange_name = (
        exchange_name or getattr(exc, "venue", None) or getattr(exc, "exchange_name", None)
    )
    context = getattr(exc, "context", {})
    raw = context.get("raw_response") if isinstance(context, dict) else None
    translated = _response_error(raw, operation, exchange_name=exchange_name, write=write)
    if translated is not None:
        return _detach_error(translated)
    raw_code = getattr(exc, "code", None)
    if raw_code is not None and not isinstance(raw_code, Enum):
        translated = _response_error(
            {"code": raw_code},
            operation,
            exchange_name=exchange_name,
            write=write,
        )
        if translated is not None:
            return _detach_error(translated)
    # A Python exception after entering a write may follow actual acceptance.
    # Known parameter validation is handled before that boundary, not inferred.
    return _detach_error(
        NormalizedApiError(operation, _safe_code(type(exc).__name__), execution_unknown=write)
    )


def check_response(raw, operation, *, exchange_name=None, write=False):
    error = _response_error(raw, operation, exchange_name=exchange_name, write=write)
    if error is not None:
        raise _detach_error(error) from None


def _native(result):
    raw = result.get_input_data() if callable(getattr(result, "get_input_data", None)) else result
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    return raw


def _dict(item):
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        result = asdict(cast("Any", item))
        raw = result.pop("raw", {})
        if "fill_id" in result and raw and not any(key in raw for key in ("fee", "commission")):
            result.pop("fee", None)
        return {**(raw if isinstance(raw, dict) else {}), **result}
    initializer = getattr(item, "init_data", None)
    if callable(initializer):
        initializer()
    getter = getattr(item, "get_all_data", None)
    result = getter() if callable(getter) else {}
    result = dict(result) if isinstance(result, dict) else {}
    # Public container inputs retain native fields lacking common getters.
    for attr in (
        "order_info",
        "trade_info",
        "account_info",
        "balance_info",
        "position_info",
        "ticker_info",
        "order_book_info",
    ):
        raw = getattr(item, attr, None)
        if isinstance(raw, (str, bytes)):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, dict):
            result = {**raw, **result}
    for name, methods in _GETTERS.items():
        for method in methods:
            function = getattr(item, method, None)
            if not callable(function):
                continue
            try:
                value = function()
            except (NotImplementedError, AttributeError, TypeError):
                # Optional aggregate getters can require fields omitted from
                # a partial account push. Retain native fields and let the
                # operation-specific mapper validate what it actually needs.
                continue
            if value is not None and value != "":
                result[name] = value.value if isinstance(value, Enum) else value
                break
    if not result:
        raise ValueError("unsupported_result_container")
    return result


def _raw_rows(raw, collection_fields):
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, dict):
        for field in collection_fields:
            if isinstance(raw.get(field), list):
                return raw[field]
    return [raw]


def rows(result, operation, *, exchange_name=None, write=False):
    raw = _native(result)
    if isinstance(raw, BaseException):
        raise normalize_error(raw, operation, exchange_name=exchange_name, write=write) from None
    check_response(raw, operation, exchange_name=exchange_name, write=write)
    policy = normalization_policy(exchange_name)
    collection_fields = getattr(policy, "result_collection_fields", None)
    if (
        type(collection_fields) is not tuple
        or any(type(field) is not str or not field for field in collection_fields)
        or len(set(collection_fields)) != len(collection_fields)
    ):
        raise NormalizedApiError(
            operation, "provider_result_collection_policy_unavailable", execution_unknown=write
        )
    native_rows = _raw_rows(raw, collection_fields)

    def source_rows(values, *, aligned):
        if operation not in {"make_order", "query_order", "get_deals"}:
            return values
        schemas = getattr(policy, "order_provenance_schemas", None)
        if type(schemas) is not tuple or schemas != PROVIDER_SCOPED_ORDER_PROVENANCE_SCHEMAS:
            raise NormalizedApiError(
                operation, "provider_order_provenance_schema_unavailable", execution_unknown=write
            )
        for field_name in ("order_provenance_source_fields", "order_provenance_envelope_fields"):
            fields = getattr(policy, field_name, None)
            if type(fields) is not frozenset or any(type(field) is not str or not field for field in fields):
                raise NormalizedApiError(
                    operation, "provider_order_provenance_policy_unavailable", execution_unknown=write
                )
        collection = _order_source_provenance(raw, operation, policy=policy)
        row_provenance = (
            tuple(_order_source_provenance(raw, operation, item, policy=policy) for item in native_rows)
            if aligned and len(values) == len(native_rows)
            else tuple(None for _ in values)
        )
        return _NormalizedSourceRows(
            values,
            collection_provenance=collection,
            row_provenance=row_provenance,
            aligned=aligned and len(values) == len(native_rows),
        )

    getter = getattr(result, "get_data", None)
    if callable(getter):
        try:
            normalized = getter()
            normalized = normalized if isinstance(normalized, (tuple, list)) else [normalized]
            mapped = [
                _dict(row)
                for item in normalized
                for row in _raw_rows(_dict(item), collection_fields)
            ]
            if mapped:
                # Keep missing native fields locally for vendor-only values.
                if len(mapped) == len(native_rows):
                    merged = [
                        {
                            **(dict(native) if isinstance(native, dict) else {}),
                            **{key: value for key, value in item.items() if value is not None},
                        }
                        for native, item in zip(native_rows, mapped, strict=True)
                    ]
                    return source_rows(merged, aligned=True)
                return source_rows(mapped, aligned=False)
            if not native_rows:
                return source_rows([], aligned=True)
        except (ValueError, TypeError, KeyError, AttributeError, NotImplementedError):
            # Some legacy containers do not normalize newer endpoints yet.
            pass
    return source_rows([_dict(item) for item in native_rows], aligned=True)


def _base(exchange_name, row, symbol=None):
    exchange, _, asset = exchange_name.partition("___")
    policy = normalization_policy(exchange_name)
    return {
        "exchange_name": exchange_name,
        "exchange": exchange,
        "asset_type": str(pick(row, *policy.base_asset_type_fields, default=asset)).lower(),
        "symbol": str(
            pick(
                row,
                *policy.base_symbol_fields,
                default=symbol or "",
            )
        ),
    }


def _is_provider_quote_v2(row, exchange_name) -> bool:
    """Recognize the selected provider's declared quote schema, or fail closed.

    The ``*.quote.v2`` namespace is reserved for provider-owned projections.
    A declared schema on a different route must not fall back to generic ticks.
    Declaring a schema does not establish source or execution authority.
    """
    schema = str(pick(row, "schema_version", default="") or "")
    if not schema:
        return False
    policy = normalization_policy(exchange_name)
    declared = getattr(policy, "quote_v2_schema", None)
    if declared is not None and (type(declared) is not str or not declared):
        raise CapabilityNotSupportedError(
            "normalize_event", detail="provider quote V2 schema declaration invalid"
        )
    if schema == declared:
        return True
    if schema.endswith(".quote.v2"):
        raise CapabilityNotSupportedError(
            "normalize_event", detail="provider quote V2 schema unavailable for route"
        )
    return False


def _unit(exchange_name, row):
    return pick(
        row,
        "quantity_unit",
        default=normalization_policy(exchange_name).quantity_unit,
    )


def _timestamps(row, exchange_name):
    if _is_provider_quote_v2(row, exchange_name):
        policy = normalization_policy(exchange_name)
        project = getattr(policy, "quote_v2_timestamps", None)
        if not callable(project):
            raise CapabilityNotSupportedError(
                "normalize_event", detail="provider quote V2 timestamp projection unavailable"
            )
        projected = project(row, pick=pick, seconds=seconds)
        if type(projected) is not dict:
            raise CapabilityNotSupportedError(
                "normalize_event", detail="provider quote V2 timestamps invalid"
            )
        return projected
    freshness = row.get("freshness") or {}
    if not isinstance(freshness, dict):
        freshness = {}
    exchange_time = seconds(
        pick(
            row,
            *normalization_policy(exchange_name).exchange_time_fields,
            default=freshness.get("observed_at"),
        )
    )
    received_wall = seconds(
        pick(
            row,
            "received_wall_time",
            "local_time",
            "local_update_time",
            "receive_time",
            "recv_time_utc",
        )
    )
    if received_wall is None:
        received_wall = time.time()
    received_monotonic = pick(
        row,
        "received_monotonic_ns",
        "local_monotonic_ns",
        "receive_monotonic_ns",
        "recv_monotonic_ns",
    )
    if received_monotonic in (None, ""):
        received_monotonic = time.monotonic_ns()
    else:
        received_monotonic = int(received_monotonic)
    stale = bool(pick(row, "stale", default=freshness.get("stale", False)))
    stale_reason = pick(row, "stale_reason", default=freshness.get("stale_reason"))
    return {
        "timestamp": exchange_time,
        "local_time": received_wall,
        "exchange_time": exchange_time,
        "received_wall_time": received_wall,
        "received_monotonic_ns": received_monotonic,
        "clock_domain_id": str(pick(row, "clock_domain_id", default=CLOCK_DOMAIN_ID)),
        "source": str(pick(row, "source", default="exchange")),
        "stale": stale,
        "stale_reason": stale_reason,
    }










def _ctp_quote_v2_fields(
    row, timestamps, *, exchange_name="", symbol="", parent_attestation=None,
):
    """Bind the selected CTP provider projection to SDK-owned attestation checks."""
    from .provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

    try:
        factory = load_route_entrypoint("bt_api.legacy_quote_v2_projectors", exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "normalize_event", detail="provider quote V2 projection unavailable"
        ) from None
    projector = factory(
        pick=pick,
        number=number,
        trusted_text=_ctp_quote_v2_trusted_text,
        parent_attestation_matches=_ctp_quote_v2_parent_attestation_matches,
    )
    result = projector(
        row, timestamps, exchange_name=exchange_name, symbol=symbol,
        parent_attestation=parent_attestation,
    )
    if type(result) is not dict:
        raise CapabilityNotSupportedError("normalize_event", detail="provider quote V2 result invalid")
    parent_verified = _ctp_quote_v2_parent_attestation_matches(
        parent_attestation,
        exchange_name=exchange_name,
        symbol=symbol,
        source=result.get("source"),
        rules_hash=result.get("rules_hash"),
        clock_domain_id=result.get("clock_domain_id"),
        connection_generation=result.get("connection_generation"),
        subscription_epoch=result.get("subscription_epoch"),
    )
    eligible = result.get("execution_eligible") is True and parent_verified
    result["execution_eligible"] = eligible
    cohort_fields = (
        "cohort_now_monotonic_ns", "cohort_now_epoch", "cohort_now_clock_domain_id",
        "cohort_now_receive_clock_error_ms", "cohort_now_receive_clock_quality",
        "cohort_now_freshness_verified",
    )
    for field in cohort_fields:
        result.pop(field, None)
    if eligible:
        result.update(
            cohort_now_monotonic_ns=parent_attestation.cohort_now_monotonic_ns,
            cohort_now_epoch=parent_attestation.cohort_now_epoch,
            cohort_now_clock_domain_id=parent_attestation.clock_domain_id,
            cohort_now_receive_clock_error_ms=parent_attestation.cohort_now_receive_clock_error_ms,
            cohort_now_receive_clock_quality=parent_attestation.cohort_now_receive_clock_quality,
            cohort_now_freshness_verified=parent_attestation.cohort_now_freshness_verified,
        )
    return result


def _event_id(event, row, exchange_name):
    native = pick(row, *normalization_policy(exchange_name).event_id_fields)
    if native not in (None, ""):
        identity = f"{event.get('exchange_name')}:{event.get('kind')}:{native}"
    else:
        identity = ":".join(
            str(value or "")
            for value in (
                event.get("exchange_name"),
                event.get("kind"),
                event.get("symbol"),
                event.get("sequence"),
                event.get("exchange_time"),
                event.get("received_monotonic_ns"),
            )
        )
    return hashlib.sha256(identity.encode()).hexdigest()


def _finish_event(event, row, exchange_name):
    audit_names = normalization_policy(exchange_name).event_audit_fields
    event.setdefault("raw_audit_fields", {name: row[name] for name in audit_names if name in row})
    event.setdefault("event_id", _event_id(event, row, exchange_name))
    event.setdefault("coalesced_count", int(pick(row, "coalesced_count", default=0)))
    return event


def metadata(row, exchange_name, symbol=None):
    """Project SDK instrument metadata with the selected provider's fields."""
    from bt_api_base.instrument_metadata_projection import project_instrument_metadata

    result = _base(exchange_name, row, symbol)
    policy = normalization_policy(exchange_name)
    result.update(project_instrument_metadata(row, policy, pick=pick, number=number))
    return result


def balance_rows(source, exchange_name):
    """Project provider balance fields through the shared SDK boundary."""
    from bt_api_base.balance_projection import project_balance_rows

    return project_balance_rows(
        source, exchange_name, normalization_policy(exchange_name),
        to_dict=_dict, pick=pick, decimal_number=decimal_number,
    )


def account(source, exchange_name, currency=None):
    selected = balance_rows(source, exchange_name)
    requested = currency if currency not in (None, "ALL", "all", "") else None
    if requested:
        selected = [row for row in selected if row["currency"] == requested]
    if not selected:
        raise ValueError("account_currency_not_found")
    if len(selected) > 1:
        funded = [row for row in selected if row["cash"] or row["value"]]
        if len(funded) == 1:
            selected = funded
        elif not funded:
            default_currency = normalization_policy(exchange_name).zero_balance_currency
            if default_currency is None:
                raise ValueError("multiple_account_currencies_require_selection")
            selected = [row for row in selected if row["currency"] == default_currency]
        else:
            raise ValueError("multiple_account_currencies_require_selection")
    if len(selected) != 1 or selected[0]["cash"] is None or selected[0]["value"] is None:
        raise ValueError("incomplete_account_snapshot")
    return {**selected[0], **_combined_trading_permissions(source, exchange_name)}


def position(row, exchange_name, symbol=None):
    """Project provider position fields through the shared SDK boundary."""
    from bt_api_base.position_projection import project_position_fields

    result = _base(exchange_name, row, symbol)
    policy = normalization_policy(exchange_name)
    result.update(
        project_position_fields(row, policy, pick=pick, number=number, decimal_number=decimal_number)
    )
    return result


def _side_value(row, exchange_name, *, position=False, default=None):
    """Normalize side candidates supplied by the selected provider route."""
    from bt_api_base.execution_identity_projection import normalize_side_candidates

    from .venue_order_side_projection import order_side_candidates

    candidates = order_side_candidates(exchange_name, row, position=position)
    candidates.append(default)
    return normalize_side_candidates(candidates, position=position)


def _position_mode_value(row, exchange_name, default=None):
    """Return a canonical position mode only when the payload or request supplies one."""
    from bt_api_base.execution_identity_projection import normalize_position_mode

    policy = normalization_policy(exchange_name)
    return normalize_position_mode(row, policy, default=default, pick=pick)


def _explicit_execution_identity_fields(row, policy):
    """Identify semantic identity actually carried by the venue payload.

    Order normalization also fills stable venue defaults and request fallbacks.
    Those values are useful to API consumers but are not independent remote
    evidence and therefore must not contradict a previously persisted intent.
    """

    from bt_api_base.execution_identity_projection import (
        explicit_execution_identity_fields,
        unsupported_order_identity_aliases,
    )
    from bt_api_base.order_identity_fields import NEUTRAL_ORDER_IDENTITY_FIELDS

    identity_fields = getattr(policy, "identity_fields", None)
    if (
        not hasattr(identity_fields, "items")
        or set(identity_fields) != set(NEUTRAL_ORDER_IDENTITY_FIELDS)
        or any(type(names) is not tuple or any(type(name) is not str or not name for name in names)
               for names in identity_fields.values())
    ):
        raise NormalizedApiError(
            "normalize_order_identity", "provider_identity_policy_unavailable", execution_unknown=True
        )
    if unsupported_order_identity_aliases(row, policy):
        raise NormalizedApiError(
            "normalize_order_identity", "provider_identity_alias_unsupported", execution_unknown=True
        )
    return explicit_execution_identity_fields(row, policy)


def _trading_permissions(row, exchange_name):
    """Project the selected provider's explicit fields without granting authority."""
    from bt_api_base.account_permission_projection import project_account_permissions

    policy = normalization_policy(exchange_name)
    return project_account_permissions(
        row,
        pick=pick,
        permission_fields=policy.permission_fields,
        can_trade_fields=policy.can_trade_fields,
    )


def _combined_trading_permissions(source, exchange_name):
    """Combine explicit permission evidence without turning absence into access."""
    from bt_api_base.account_permission_projection import combine_account_permission_projections

    return combine_account_permission_projections(
        source, project=lambda row: _trading_permissions(row, exchange_name)
    )


def account_permissions(result, exchange_name):
    """Normalize an account-permission response without requiring balance rows."""
    source = rows(result, "get_account_config", exchange_name=exchange_name)
    if not source:
        raise ValueError("account_permission_snapshot_missing")
    return {
        "exchange_name": exchange_name,
        **_combined_trading_permissions(source, exchange_name),
    }


def _provider_field_candidates(policy, attribute):
    """Require an explicit selected-provider alias tuple without granting authority."""
    fields = getattr(policy, attribute, None)
    if type(fields) is not tuple or any(
        type(name) is not str or not name for name in fields
    ):
        raise NormalizedApiError(
            "provider_normalization", "provider_field_policy_unavailable", execution_unknown=True
        )
    return fields


def order(row, exchange_name, symbol=None, request=None, operation="query_order"):
    result = _base(exchange_name, row, symbol)
    req = asdict(cast("Any", request)) if is_dataclass(request) else (request or {})
    ack_payload = row.get("payload")
    if isinstance(ack_payload, dict):
        row = {**row, **ack_payload}
    policy = normalization_policy(exchange_name)
    raw_status = (
        str(pick(row, *_provider_field_candidates(policy, "order_status_fields"), default="")).lower().replace("-", "_")
    )
    status = policy.order_statuses.get(raw_status, "unknown")
    order_id = pick(row, *policy.order_fields["order_id"])
    client_id = pick(row, *policy.order_fields["client_id"], default=req.get("client_order_id"))
    if not raw_status and order_id and operation == "make_order":
        status = "accepted"
    if not raw_status and row.get("accepted") is False:
        status = "rejected"
    filled = number(
        pick(row, *policy.order_fields["filled"]),
        0,
    )
    average = number(
        pick(row, *policy.order_fields["average"])
    )
    average = policy.order_average(row, average, pick=pick, number=number)
    if average is None and filled:
        quote = number(pick(row, *policy.order_fields["cum_quote"]))
        average = quote / filled if quote is not None else None
    unknown = bool(row.get("execution_unknown")) or status == "unknown"
    trade_authority = policy.trade_authority
    if filled and (average is None or average <= 0) and not trade_authority:
        unknown = True
    if status == "completed" and filled <= 0:
        unknown = True
    result.update(
        kind="order",
        order_id=str(order_id or ""),
        client_order_id=str(client_id or ""),
        status=status if not unknown else "submitted",
        filled=filled,
        avg_price=average,
        size=number(
            pick(row, *policy.order_fields["size"], default=req.get("quantity"))
        ),
        side=_side_value(row, exchange_name, default=req.get("side")),
        position_side=_side_value(
            row, exchange_name, position=True, default=req.get("position_side")
        ),
        position_id=pick(row, "position_id", default=req.get("position_id")),
        offset=pick(row, "offset", "order_offset", default=req.get("offset")),
        position_bucket=pick(row, "position_bucket", default=req.get("position_bucket")),
        quantity_unit=_unit(exchange_name, row),
        position_mode=_position_mode_value(row, exchange_name, default=req.get("position_mode")),
        execution_unknown=unknown,
        terminal_confirmed=not unknown and status in _TERMINAL,
        definite_reject=not unknown and status == "rejected",
        order_ref=pick(
            row, *policy.order_fields["order_ref"], default=req.get("order_ref") or client_id
        ),
        exchange_id=pick(row, *policy.order_fields["exchange_id"], default=req.get("exchange_id")),
        front_id=pick(row, *policy.order_fields["front_id"], default=req.get("front_id")),
        session_id=pick(row, *policy.order_fields["session_id"], default=req.get("session_id")),
        account_id=pick(row, *policy.order_fields["account_id"], default=req.get("account_id")),
        trading_day=pick(row, *policy.order_fields["trading_day"], default=req.get("trading_day")),
        fee_currency=pick(row, *policy.order_fields["fee_currency"]),
        commission_currency=pick(row, *policy.order_fields["commission_currency"]),
        commission_source="unavailable",
        execution_source="trades" if trade_authority else "cumulative",
        **_timestamps(row, exchange_name),
    )
    result[_EXPLICIT_IDENTITY_FIELDS] = _explicit_execution_identity_fields(row, policy)
    if (
        policy.signed_fee_currency_field is not None
        and row.get(policy.signed_fee_currency_field)
        and row.get("fee") not in (None, "")
    ):
        result["cumulative_commission"] = -number(row["fee"])
    elif row.get("cumulative_commission") not in (None, ""):
        result["cumulative_commission"] = number(row["cumulative_commission"])
    if result.get("cumulative_commission") is not None:
        result["commission_source"] = "exchange"
    return result


def trade(row, exchange_name, symbol=None):
    result = _base(exchange_name, row, symbol)
    policy = normalization_policy(exchange_name)
    result.update(
        kind="trade",
        trade_id=str(
            pick(row, *_provider_field_candidates(policy, "trade_id_fields"), default="")
        ),
        order_id=str(
            pick(row, *policy.trade_fields["order_id"], default="")
        ),
        client_order_id=pick(row, *policy.trade_fields["client_id"]),
        size=number(
            pick(row, *policy.trade_fields["size"])
        ),
        price=number(pick(row, *policy.trade_fields["price"])),
        side=_side_value(row, exchange_name),
        offset=pick(row, "offset", "trade_offset"),
        position_bucket=pick(row, "position_bucket"),
        position_id=pick(row, "position_id"),
        position_side=_side_value(row, exchange_name, position=True),
        quantity_unit=_unit(exchange_name, row),
        position_mode=_position_mode_value(row, exchange_name),
        exchange_id=pick(row, *policy.trade_fields["exchange_id"]),
        account_id=pick(row, *policy.trade_fields["account_id"]),
        trading_day=pick(row, *policy.trade_fields["trading_day"]),
        # Risk's versioned source contract names the unmodified provider fee
        # amount explicitly. Prefer that field when present while preserving
        # the legacy aliases for every existing provider payload.
        fee=number(pick(row, *_provider_field_candidates(policy, "trade_fee_fields"))),
        fee_currency=pick(row, *policy.trade_fields["fee_currency"]),
        **_timestamps(row, exchange_name),
    )
    result[_EXPLICIT_IDENTITY_FIELDS] = _explicit_execution_identity_fields(row, policy)
    if (
        "fee_amount" not in row
        and policy.signed_fee_currency_field is not None
        and row.get(policy.signed_fee_currency_field)
        and row.get("fee") not in (None, "")
    ):
        result["fee"] = -number(row["fee"])
    if row.get("fee_unresolved") is True or row.get("trade_fee_verified") is False:
        result["fee"] = None
        result["fee_unresolved"] = True
    return result




def normalize_result(operation, result, exchange_name, symbol=None, request=None):
    source = rows(
        result,
        operation,
        exchange_name=exchange_name,
        write=operation in {"make_order", "cancel_order", "set_position_mode"},
    )
    if operation == "get_exchange_info":
        mapped = [metadata(row, exchange_name, symbol) for row in source]
        if symbol is None:
            return mapped
        matching = [row for row in mapped if row["symbol"] == symbol]
        if not matching:
            raise ValueError("instrument_not_found")
        return matching[0]
    if operation == "get_funding_rate":
        if not source:
            raise ValueError("funding_snapshot_missing")
        row = source[0]
        policy = normalization_policy(exchange_name)
        return {
            **_base(exchange_name, row, symbol),
            "rate": number(pick(row, *policy.funding_rate_fields)),
            "next_funding_time": seconds(
                pick(
                    row,
                    "next_funding_time",
                    *policy.funding_time_fields,
                )
            ),
        }
    if operation == "set_position_mode":
        raw = _native(result)
        code = raw.get("code") if isinstance(raw, dict) else None
        expected_code = normalization_policy(exchange_name).position_mode_ack_code
        if str(code) != expected_code:
            raise NormalizedApiError(
                operation,
                "position_mode_ack_missing",
                execution_unknown=True,
            )
        return {
            "exchange_name": exchange_name,
            "acknowledged": True,
        }
    if operation in {"get_account_config", "get_position_mode"}:
        row = source[0]
        mode = _position_mode_value(row, exchange_name)
        if mode not in {"net", "dual_side"}:
            raise CapabilityNotSupportedError(operation, detail="position mode is unavailable")
        return {
            "exchange_name": exchange_name,
            "position_mode": mode,
            **_trading_permissions(row, exchange_name),
        }
    if operation == "get_account":
        return account(source, exchange_name, symbol)
    if operation == "get_balance":
        mapped = balance_rows(source, exchange_name)
        if symbol in (None, "ALL"):
            return mapped
        matching = [row for row in mapped if row["currency"] == symbol]
        if not matching:
            raise ValueError("balance_currency_not_found")
        return matching[0]
    if operation == "get_position":
        snapshots = [position(row, exchange_name, symbol) for row in source]
        return [
            snapshot
            for snapshot in snapshots
            if not (
                snapshot.get("quantity_known") is True
                and snapshot.get("quantity_exact_zero") is True
            )
        ]
    if operation in {"make_order", "query_order", "cancel_order"}:
        if not source:
            raise ValueError("order_response_missing")
        normalized = order(source[0], exchange_name, symbol, request, operation)
        if operation == "cancel_order":
            normalization_policy(exchange_name).apply_cancel_result(normalized, result, request)
        provenance = None
        source_row_count = None
        source_alignment_valid = False
        collection_provenance = None
        if isinstance(source, _NormalizedSourceRows) and source.source_alignment_valid:
            provenance = source.row_provenance[0] if source.row_provenance else None
            source_row_count = len(source)
            source_alignment_valid = True
            collection_provenance = source.collection_provenance
        return NormalizedRecord(
            normalized,
            provenance=provenance,
            source_row_count=source_row_count,
            source_alignment_valid=source_alignment_valid,
            collection_provenance=collection_provenance,
        )
    if operation == "get_open_orders":
        return [order(row, exchange_name, symbol) for row in source]
    if operation == "get_deals":
        mapped = [trade(row, exchange_name, symbol) for row in source]
        if isinstance(source, _NormalizedSourceRows):
            row_provenance = source.row_provenance
            if source.source_alignment_valid and len(row_provenance) == len(mapped):
                mapped = [
                    NormalizedRecord(item, provenance=row_provenance[index])
                    for index, item in enumerate(mapped)
                ]
                return NormalizedRecords(
                    mapped,
                    provenance=source.collection_provenance,
                    row_provenance=row_provenance,
                    source_alignment_valid=source.source_alignment_valid,
                )
            return NormalizedRecords(
                mapped,
                provenance=source.collection_provenance,
                row_provenance=tuple(None for _ in mapped),
                source_alignment_valid=False,
            )
        return mapped
    if operation == "get_kline":
        return [normalize_event(row, exchange_name, kind="bar", symbol=symbol) for row in source]
    if operation in {"get_tick", "get_depth"}:
        return normalize_event(
            source[0],
            exchange_name,
            kind={"get_tick": "tick", "get_depth": "orderbook", "get_kline": "bar"}[operation],
            symbol=symbol,
        )
    raise CapabilityNotSupportedError(operation, detail="no normalized result mapper")


def _orderbook_event_fields(row, exchange_name):
    def levels(side):
        values = row.get(side)
        if values is None:
            prefix = "bid" if side == "bids" else "ask"
            values = zip(
                row.get(prefix + "_price_list") or [],
                row.get(prefix + "_volume_list") or [],
                strict=True,
            )
        return [(number(level[0]), number(level[1])) for level in values]

    policy = normalization_policy(exchange_name)
    raw_sequence = pick(row, *policy.orderbook_sequence_fields)
    raw_previous = pick(row, *policy.orderbook_previous_fields)
    sequence = int(raw_sequence) if raw_sequence not in (None, "") else None
    previous_sequence = int(raw_previous) if raw_previous not in (None, "") else None
    declared_snapshot_or_delta = row.get("snapshot_or_delta")
    if declared_snapshot_or_delta is None:
        action = str(pick(row, "action", default="")).lower()
        snapshot_or_delta = (
            "delta"
            if action in {"update", "delta"} or any(
                row.get(field) not in (None, "")
                for field in policy.orderbook_delta_predecessor_fields
            )
            else "snapshot"
        )
    else:
        declared_snapshot_or_delta = str(declared_snapshot_or_delta).strip().lower()
        snapshot_or_delta = (
            declared_snapshot_or_delta
            if declared_snapshot_or_delta in {"snapshot", "delta"}
            else "unverified"
        )
    continuity_status = str(pick(row, "continuity_status", default="")).lower()
    if not continuity_status:
        continuity_status = (
            "snapshot" if snapshot_or_delta == "snapshot" and sequence is not None else "unverified"
        )
    if continuity_status not in {
        "continuous",
        "snapshot",
        "duplicate",
        "out_of_order",
        "gap",
        "checksum_failed",
        "unverified",
    }:
        continuity_status = "unverified"
    return {
        "bids": levels("bids"),
        "asks": levels("asks"),
        "quantity_unit": _unit(exchange_name, row),
        "sequence": sequence,
        "previous_sequence": previous_sequence,
        "snapshot_or_delta": snapshot_or_delta,
        "continuity_status": continuity_status,
    }


def normalize_event(
    item,
    exchange_name,
    *,
    kind=None,
    symbol=None,
    _ctp_quote_v2_parent_attestation=None,
):
    row = _dict(item)
    if isinstance(row.get("payload"), dict):
        row = {**row, **row["payload"]}
    kind = kind or str(pick(row, "kind", "event_type", "event", "type", default="")).lower()
    kind = {
        "orderbookevent": "orderbook",
        "order_book": "orderbook",
        "depth": "orderbook",
        "tickerevent": "tick",
        "tickevent": "tick",
        "ticker": "tick",
        "barevent": "bar",
        "kline": "bar",
        "orderevent": "order",
        "tradeevent": "trade",
        "positionevent": "position",
        "accountevent": "account",
    }.get(kind, kind)
    if not kind:
        if "bids" in row or "bid_price_list" in row:
            kind = "orderbook"
        elif "bid_price" in row or "ask_price" in row:
            kind = "tick"
    if kind == "order":
        return _finish_event(order(row, exchange_name, symbol), row, exchange_name)
    if kind == "trade":
        return _finish_event(trade(row, exchange_name, symbol), row, exchange_name)
    if kind == "position":
        return _finish_event(
            {
                "kind": kind,
                **position(row, exchange_name, symbol),
                **_timestamps(row, exchange_name),
            },
            row,
            exchange_name,
        )
    if kind == "account":
        # WebSocket accounts are updates, not necessarily complete portfolio
        # snapshots. Preserve each denomination without inventing an FX sum or
        # requiring optional aggregate margin fields from every push.
        balances = balance_rows([row], exchange_name)
        event = {
            "kind": kind,
            "exchange_name": exchange_name,
            "balances": balances,
            "partial": True,
            **_timestamps(row, exchange_name),
        }
        if len(balances) == 1:
            event.update(balances[0])
        return _finish_event(event, row, exchange_name)
    result = {**_base(exchange_name, row, symbol), "kind": kind, **_timestamps(row, exchange_name)}
    if kind == "orderbook":
        result.update(_orderbook_event_fields(row, exchange_name))
    elif kind == "tick":
        if _is_provider_quote_v2(row, exchange_name):
            result.update(
                _ctp_quote_v2_fields(
                    row,
                    result,
                    exchange_name=exchange_name,
                    symbol=result["symbol"],
                    parent_attestation=_ctp_quote_v2_parent_attestation,
                )
            )
        else:
            policy = normalization_policy(exchange_name)
            bid = number(pick(row, *policy.tick_bid_fields))
            ask = number(pick(row, *policy.tick_ask_fields))
            price = number(pick(row, *policy.tick_price_fields))
            if not price and bid and ask:
                price = (bid + ask) / 2
            volume_semantics = str(pick(row, "volume_semantics", default="") or "")
            delta_volume = number(
                pick(
                    row,
                    "delta_volume",
                    default=pick(row, *policy.tick_volume_fields),
                )
            )
            cumulative_volume = number(pick(row, *policy.tick_cumulative_fields))
            result.update(
                price=price,
                bid_price=bid,
                ask_price=ask,
                volume=(
                    delta_volume
                    if volume_semantics == "delta"
                    else number(pick(row, *policy.tick_volume_fields))
                ),
            )
            if pick(row, "schema_version") not in (None, ""):
                result.update(
                    schema_version=str(pick(row, "schema_version")),
                    volume_semantics=volume_semantics,
                    cum_volume=cumulative_volume,
                    cumulative_volume=cumulative_volume,
                    delta_volume=delta_volume,
                    volume_complete=bool(pick(row, "volume_complete", default=False)),
                    volume_quality=str(pick(row, "volume_quality", default="") or ""),
                    trading_day=str(
                        pick(row, *_provider_field_candidates(policy, "tick_trading_day_fields"), default="") or ""
                    ),
                    action_day=str(
                        pick(row, *_provider_field_candidates(policy, "tick_action_day_fields"), default="") or ""
                    ),
                    event_time_utc=seconds(pick(row, "event_time_utc")),
                    recv_time_utc=seconds(pick(row, "recv_time_utc")),
                    recv_monotonic_ns=int(pick(row, "recv_monotonic_ns", default=0) or 0),
                    connection_generation=int(pick(row, "connection_generation", default=0) or 0),
                    ingest_seq=int(pick(row, "ingest_seq", default=0) or 0),
                    quality_flags=tuple(pick(row, "quality_flags", default=()) or ()),
                    event_time_source=str(pick(row, "event_time_source", default="") or ""),
                )
    elif kind == "bar":
        result.update(
            {
                key: number(pick(row, key, key + "_price"))
                for key in ("open", "high", "low", "close", "volume")
            }
        )
    elif kind == "reconcile":
        result.update(
            status=pick(row, "status", default="required"),
            connection_generation=int(pick(row, "connection_generation", default=0) or 0),
            scopes=row.get("scopes"),
            failed_scopes=tuple(row.get("failed_scopes") or ()),
        )
    else:
        return None
    return _finish_event(result, row, exchange_name)
