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

from bt_api_base.exceptions import InvalidOrderError

from ._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from ._contracts.models import FeeSchedule, Freshness, FundingSnapshot, InstrumentSpec

_CLOCK_DOMAIN_SEED = f"{socket.gethostname()}:{os.getpid()}:{time.monotonic_ns()}"
CLOCK_DOMAIN_ID = hashlib.sha256(_CLOCK_DOMAIN_SEED.encode()).hexdigest()[:24]
_EXPLICIT_IDENTITY_FIELDS = "_explicit_identity_fields"

# Only response codes whose venue documentation proves that the request was
# rejected before acceptance belong here. Unknown numeric codes are deliberately
# uncertain: treating a newly introduced venue code as a rejection could permit
# a duplicate order submission.
_DEFINITE_REJECT_CODES_BY_VENUE = {
    "OKX": frozenset(
        {
            "50101",  # API key does not match the selected environment
            "50119",  # API key does not exist
            "51000",  # invalid request parameter
            "51008",  # insufficient balance or margin
            "51010",  # request unsupported by the account mode
            "51121",  # quantity is not a multiple of the lot size
        }
    ),
    "BINANCE": frozenset(
        {
            "-1002",  # unauthorized request
            "-1013",  # invalid request message
            "-1014",  # unsupported order composition
            "-1015",  # too many new orders
            "-1021",  # timestamp outside recvWindow
            "-1022",  # invalid signature
            "-1099",  # unauthenticated or unauthorized
            "-1100",  # illegal parameter characters
            "-1101",  # too many parameters
            "-1102",  # mandatory parameter missing or malformed
            "-1103",  # unknown parameter
            "-1104",  # unread parameters
            "-1105",  # empty parameter
            "-1106",  # parameter not required
            "-1108",  # invalid asset
            "-1109",  # invalid account
            "-1110",  # invalid instrument type
            "-1111",  # invalid precision
            "-1114",  # time-in-force not required
            "-1115",  # invalid time-in-force
            "-1116",  # invalid order type
            "-1117",  # invalid side
            "-1118",  # empty new client order id
            "-1119",  # empty original client order id
            "-1121",  # invalid symbol
            "-1122",  # invalid symbol status
            "-1128",  # invalid optional parameter combination
            "-1130",  # invalid parameter
            "-1136",  # invalid new-order response type
            "-2010",  # new order rejected
            "-2011",  # cancellation rejected
            "-2013",  # order does not exist
            "-2014",  # invalid API-key format
            "-2015",  # invalid API key, IP, or permission
            "-2018",  # insufficient balance
            "-2019",  # insufficient margin
            "-2021",  # order would immediately trigger
            "-2022",  # reduce-only order rejected
            "-2024",  # insufficient position
            "-2025",  # maximum open orders exceeded
            "-2026",  # unsupported reduce-only order type
            "-2027",  # maximum leverage exceeded
            "-4001",  # price below zero
            "-4002",  # price above maximum
            "-4003",  # quantity below zero
            "-4004",  # quantity below minimum
            "-4005",  # quantity above maximum
            "-4013",  # price below minimum
            "-4014",  # invalid tick-size increment
            "-4015",  # invalid client order id
            "-4016",  # price above multiplier cap
            "-4023",  # invalid step-size increment
            "-4024",  # price below multiplier floor
            "-4060",  # invalid position side
            "-4061",  # position side does not match account mode
            "-4062",  # reduce-only conflict
            "-4118",  # reduce-only margin check failed
            "-4131",  # market order rejected by price filter
            "-4135",  # invalid activation price
            "-4137",  # quantity invalid with closePosition
            "-4138",  # reduceOnly required with closePosition
            "-4139",  # unsupported market order
            "-4140",  # invalid opening-position status
            "-4141",  # symbol closed
            "-4142",  # strategy trigger price rejected
            "-4164",  # minimum notional not met
            "-4189",  # account restricted to reduce-only orders
            "-4192",  # trading forbidden during cooling-off period
        }
    ),
}
_AUTH_ENVIRONMENT_MISMATCH_CODES = {
    ("OKX", "50101"),
    ("OKX", "50119"),
    ("BINANCE", "-2015"),
}
_PARAMETER_ERROR_CODES = {
    ("OKX", "50014"),
    ("OKX", "50016"),
    ("OKX", "51000"),
}
_STATUS = {
    "new": "accepted",
    "open": "accepted",
    "live": "accepted",
    "working": "accepted",
    "submitted": "submitted",
    "accepted": "accepted",
    "pending": "submitted",
    "partial": "partial",
    "partially_filled": "partial",
    "partial_filled": "partial",
    "filled": "completed",
    "completed": "completed",
    "all_traded": "completed",
    "cancelled": "canceled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
    "expired": "expired",
    "expired_in_match": "expired",
    "rejected": "rejected",
    "unknown": "unknown",
    "local_pending": "submitted",
}
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
        pick(
            row, "received_wall_time", "local_time", "local_update_time", "receive_time"
        )
    ) or datetime.now(UTC)
    return Freshness(
        source=source, observed_at=observed, stale=stale, stale_reason=reason
    )


def _fingerprint(values):
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def instrument_spec(result, exchange_name, symbol):
    """Build the strict Decimal public instrument contract from venue rules."""
    source = rows(result, "get_instrument_spec", exchange_name=exchange_name)
    candidates = [
        row
        for row in source
        if str(pick(row, "symbol", "symbol_name", "instId", default="")) == symbol
    ]
    if not candidates:
        raise NormalizedApiError("get_instrument_spec", "instrument_not_found")
    row = candidates[0]
    venue = exchange_name.partition("___")[0]
    filters = {
        item.get("filterType"): item
        for item in row.get("filters", [])
        if isinstance(item, dict)
    }
    lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
    price_filter = filters.get("PRICE_FILTER") or {}
    notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    if venue == "OKX":
        required = {
            "contract_value": pick(row, "ctVal", "contract_value"),
            "contract_multiplier": pick(
                row, "ctMult", "contract_multiplier", default="1"
            ),
            "price_tick": pick(row, "tickSz", "price_tick"),
            "quantity_step": pick(row, "lotSz", "quantity_step"),
            "min_quantity": pick(row, "minSz", "min_quantity"),
        }
        max_quantity_raw = pick(row, "max_quantity", "maxLmtSz", "maxMktSz")
        quantity_unit = "contracts"
    elif venue == "BINANCE":
        required = {
            "contract_value": pick(row, "contract_value", default="1"),
            "contract_multiplier": pick(row, "contract_multiplier", default="1"),
            "price_tick": pick(row, "tick_size", default=price_filter.get("tickSize")),
            "quantity_step": pick(row, "quantity_step", default=lot.get("stepSize")),
            "min_quantity": pick(row, "min_quantity", default=lot.get("minQty")),
        }
        max_quantity_raw = pick(row, "max_quantity", default=lot.get("maxQty"))
        quantity_unit = "base"
    elif venue == "CTP":
        if (
            row.get("metadata_complete") is not True
            or row.get("evidence_complete") is not True
        ):
            raise NormalizedApiError("get_instrument_spec", "ctp_metadata_incomplete")
        required = {
            "contract_value": pick(row, "contract_value", "volume_multiple"),
            "contract_multiplier": pick(row, "contract_multiplier", default="1"),
            "price_tick": pick(row, "price_tick", "tick_size"),
            "quantity_step": pick(row, "quantity_step", default="1"),
            "min_quantity": pick(row, "min_quantity", default="1"),
        }
        max_quantity_raw = pick(row, "max_quantity")
        quantity_unit = "lots"
    else:
        raise CapabilityNotSupportedError(
            "get_instrument_spec",
            detail=f"{exchange_name} has no strict instrument mapper",
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
            "base_currency",
            "baseAsset",
            "baseCcy",
            "ctValCcy",
            default=parts[0] if parts else "",
        )
    )
    quote = str(
        pick(
            row,
            "quote_currency",
            "quoteAsset",
            "quoteCcy",
            "settleCcy",
            "marginAsset",
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
        "min_notional": str(
            pick(
                row,
                "min_notional",
                default=pick(notional_filter, "notional", "minNotional"),
            )
            or ""
        ),
        "status": status,
    }
    min_notional_raw = pick(
        row, "min_notional", default=pick(notional_filter, "notional", "minNotional")
    )
    if min_notional_raw not in (None, ""):
        min_notional = decimal_number(min_notional_raw)
    elif venue == "OKX":
        # OKX contract instrument rules do not define a separate minimum
        # notional floor.  Expose that proven absence as the numeric identity
        # value while keeping the raw empty field in rule_values/fingerprint.
        min_notional = Decimal("0")
    else:
        min_notional = None
    max_quantity = (
        decimal_number(max_quantity_raw) if max_quantity_raw not in (None, "") else None
    )
    if max_quantity is not None and max_quantity <= 0:
        raise NormalizedApiError("get_instrument_spec", "invalid_max_quantity_rule")
    return InstrumentSpec(
        exchange_name=exchange_name,
        symbol=symbol,
        asset_type=str(
            pick(
                row, "asset_type", "instType", default=exchange_name.partition("___")[2]
            )
        ).lower(),
        base_currency=base,
        quote_currency=quote,
        contract_type=str(
            pick(row, "contract_type", "contractType", "instType", default="unknown")
        ).lower(),
        linear=bool(
            pick(
                row,
                "linear",
                default=(row.get("ctType") == "linear" if "ctType" in row else True),
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


_OKX_SPOT_FEE_TYPES = frozenset({"SPOT", "MARGIN"})
_OKX_DERIVATIVE_FEE_TYPES = frozenset({"SWAP", "FUTURES", "OPTION"})


def _okx_fee_asset_type(value):
    asset_type = str(value or "").strip().upper()
    return "FUTURES" if asset_type == "FUTURE" else asset_type


def _okx_fee_instrument_id(symbol, asset_type):
    inst_id = str(symbol or "").strip().replace("/", "-").upper()
    if asset_type == "SWAP" and inst_id and not inst_id.endswith("-SWAP"):
        inst_id += "-SWAP"
    return inst_id


def _compact_instrument_id(value):
    return "".join(
        character for character in str(value or "").upper() if character.isalnum()
    )


def okx_fee_scope(result, exchange_name, symbol):
    """Resolve the exact OKX fee-query identity from public instrument metadata."""
    if str(exchange_name).partition("___")[0].upper() != "OKX":
        raise CapabilityNotSupportedError(
            "get_fee_schedule", detail="OKX fee scope requested for another venue"
        )
    asset_type = _okx_fee_asset_type(str(exchange_name).partition("___")[2])
    if asset_type not in _OKX_SPOT_FEE_TYPES | _OKX_DERIVATIVE_FEE_TYPES:
        raise NormalizedApiError("get_fee_schedule", "unsupported_fee_instrument_type")
    expected_inst_id = _okx_fee_instrument_id(symbol, asset_type)
    expected_compact = _compact_instrument_id(expected_inst_id)
    source = rows(result, "get_fee_instrument_metadata", exchange_name=exchange_name)
    matches = []
    for row in source:
        row_inst_id = str(
            pick(row, "instId", "symbol", "symbol_name", default="")
        ).strip()
        row_asset_type = _okx_fee_asset_type(
            pick(row, "instType", "asset_type", default=asset_type)
        )
        if (
            row_asset_type == asset_type
            and _compact_instrument_id(row_inst_id) == expected_compact
        ):
            matches.append((row, row_inst_id))
    if not matches:
        raise NormalizedApiError(
            "get_fee_schedule", "fee_instrument_metadata_not_found"
        )
    if len(matches) != 1:
        raise NormalizedApiError(
            "get_fee_schedule", "fee_instrument_metadata_ambiguous"
        )
    row, inst_id = matches[0]
    group_id_value = pick(row, "groupId", "group_id")
    group_id = str(group_id_value).strip() if group_id_value not in (None, "") else None
    scope = {
        "asset_type": asset_type,
        "inst_id": inst_id,
        "inst_family": None,
        "group_id": group_id,
        "currency": pick(row, "settleCcy", "quoteCcy", "fee_currency"),
    }
    if asset_type in _OKX_DERIVATIVE_FEE_TYPES:
        inst_family = str(
            pick(row, "instFamily", "underlying_symbol_name", default="")
        ).strip()
        if not inst_family:
            raise NormalizedApiError("get_fee_schedule", "fee_inst_family_missing")
        scope["inst_family"] = inst_family
    return scope


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


def _okx_legacy_fee_fields(symbol, asset_type):
    upper_symbol = str(symbol or "").upper()
    if "USDC" in upper_symbol:
        return ("makerUSDC", "maker"), ("takerUSDC", "taker")
    if asset_type in _OKX_DERIVATIVE_FEE_TYPES and "USDT" in upper_symbol:
        return ("makerU", "maker"), ("takerU", "taker")
    return ("maker",), ("taker",)


def _okx_cost_and_rebate(value):
    native_rate = decimal_number(value)
    if native_rate is None:
        return None, None
    if native_rate < 0:
        return -native_rate, Decimal("0")
    if native_rate > 0:
        return Decimal("0"), native_rate
    return Decimal("0"), Decimal("0")


def fee_schedule(
    result,
    exchange_name,
    symbol,
    account_id,
    *,
    expected_group_id=None,
    metadata_currency=None,
):
    source = rows(result, "get_fee_schedule", exchange_name=exchange_name)
    venue = exchange_name.partition("___")[0].upper()
    asset_type = _okx_fee_asset_type(exchange_name.partition("___")[2])
    if venue == "OKX":
        typed_rows = [
            row
            for row in source
            if _okx_fee_asset_type(pick(row, "instType", default=asset_type))
            == asset_type
        ]
        if not typed_rows:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, {}, "fee_asset_type_missing"
            )
        if len(typed_rows) != 1:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, {}, "fee_asset_type_ambiguous"
            )
        row = typed_rows[0]
    else:
        row = source[0] if source else {}

    selected_group_id = None
    native_maker = None
    native_taker = None
    if venue == "OKX" and row.get("feeGroup") not in (None, []):
        fee_groups = row["feeGroup"]
        if not isinstance(fee_groups, list) or not all(
            isinstance(group, dict) for group in fee_groups
        ):
            return _unavailable_fee_schedule(
                exchange_name,
                symbol,
                account_id,
                row,
                "fee_group_payload_invalid",
            )
        if expected_group_id not in (None, ""):
            group_matches = [
                group
                for group in fee_groups
                if str(group.get("groupId", "")).strip()
                == str(expected_group_id).strip()
            ]
        else:
            group_matches = fee_groups
        if not group_matches:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, row, "fee_group_not_found"
            )
        if len(group_matches) != 1:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, row, "fee_group_ambiguous"
            )
        selected_group = group_matches[0]
        selected_group_id = str(selected_group.get("groupId", "")).strip() or None
        native_maker = pick(selected_group, "maker")
        native_taker = pick(selected_group, "taker")
    elif venue == "OKX":
        maker_fields, taker_fields = _okx_legacy_fee_fields(symbol, asset_type)
        native_maker = pick(row, *maker_fields)
        native_taker = pick(row, *taker_fields)
    else:
        native_maker = pick(
            row,
            "maker_rate",
            "makerCommissionRate",
            "makerCommission",
            "maker",
        )
        native_taker = pick(
            row,
            "taker_rate",
            "takerCommissionRate",
            "takerCommission",
            "taker",
        )

    if native_maker in (None, "") or native_taker in (None, ""):
        return _unavailable_fee_schedule(
            exchange_name, symbol, account_id, row, "fee_rate_missing"
        )

    raw = {
        "selected_group_id": selected_group_id,
        "maker_native_rate": native_maker,
        "taker_native_rate": native_taker,
    }
    if venue == "OKX":
        try:
            maker_rate, maker_rebate = _okx_cost_and_rebate(native_maker)
            taker_rate, taker_rebate = _okx_cost_and_rebate(native_taker)
        except ValueError:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, row, "fee_rate_invalid"
            )
        raw.update(
            {
                "maker_rebate_rate": maker_rebate,
                "taker_rebate_rate": taker_rebate,
                "sign_semantics": "okx_negative_commission_positive_rebate",
            }
        )
    else:
        try:
            maker_rate = abs(decimal_number(native_maker))
            taker_rate = abs(decimal_number(native_taker))
        except ValueError:
            return _unavailable_fee_schedule(
                exchange_name, symbol, account_id, row, "fee_rate_invalid"
            )

    if maker_rate is None or taker_rate is None:
        return _unavailable_fee_schedule(
            exchange_name, symbol, account_id, row, "fee_rate_missing"
        )
    return FeeSchedule(
        exchange_name=exchange_name,
        symbol=symbol,
        account_id=account_id,
        maker_rate=maker_rate,
        taker_rate=taker_rate,
        currency=pick(
            row,
            "currency",
            "feeCcy",
            "commissionAsset",
            "settleCcy",
            default=metadata_currency,
        ),
        source=f"{venue.lower()}_get_fee",
        freshness=_freshness(row),
        raw=raw,
    )


_FUNDING_RAW_FIELDS = (
    "funding_rate",
    "fundingRate",
    "lastFundingRate",
    "rate",
    "next_funding_time",
    "fundingTime",
    "nextFundingTime",
    "settlement_interval_seconds",
    "funding_interval_seconds",
    "fundingIntervalHours",
)


def _funding_raw(row):
    return {key: row[key] for key in _FUNDING_RAW_FIELDS if key in row}


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
        raw=_funding_raw(row),
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

    rate_raw = pick(row, "funding_rate", "fundingRate", "lastFundingRate", "rate")
    next_time_raw = pick(
        row,
        "next_funding_time",
        *(
            ("fundingTime", "nextFundingTime")
            if exchange_name.startswith("OKX___")
            else ("nextFundingTime", "fundingTime")
        ),
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
    interval_hours = row.get("fundingIntervalHours")
    if interval_raw in (None, "") and interval_hours in (None, ""):
        funding_time = row.get("fundingTime")
        following_funding_time = row.get("nextFundingTime")
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
        raw=_funding_raw(row),
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


def _error_classification(exchange_name, code):
    venue = str(exchange_name or "").partition("___")[0].upper()
    if (venue, code) in _AUTH_ENVIRONMENT_MISMATCH_CODES:
        return "auth", True
    if (venue, code) in _PARAMETER_ERROR_CODES:
        return "parameter", False
    return None, False


def _is_definite_reject(exchange_name, code):
    venue = str(exchange_name or "").partition("___")[0].upper()
    return code in _DEFINITE_REJECT_CODES_BY_VENUE.get(venue, ())


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
    for row in raw.get("data", []) if isinstance(raw.get("data"), list) else []:
        if isinstance(row, dict) and str(row.get("sCode", "0")) != "0":
            code = row["sCode"]
            break
    if str(code) in {"0", "200"}:
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
        return _detach_error(
            NormalizedApiError(operation, "invalid_order", definite_reject=write)
        )
    exchange_name = (
        exchange_name
        or getattr(exc, "venue", None)
        or getattr(exc, "exchange_name", None)
    )
    context = getattr(exc, "context", {})
    raw = context.get("raw_response") if isinstance(context, dict) else None
    translated = _response_error(
        raw, operation, exchange_name=exchange_name, write=write
    )
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
        NormalizedApiError(
            operation, _safe_code(type(exc).__name__), execution_unknown=write
        )
    )


def check_response(raw, operation, *, exchange_name=None, write=False):
    error = _response_error(raw, operation, exchange_name=exchange_name, write=write)
    if error is not None:
        raise _detach_error(error) from None


def _native(result):
    raw = (
        result.get_input_data()
        if callable(getattr(result, "get_input_data", None))
        else result
    )
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    return raw


def _dict(item):
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        result = asdict(item)
        raw = result.pop("raw", {})
        if (
            "fill_id" in result
            and raw
            and not any(key in raw for key in ("fee", "commission"))
        ):
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


def _raw_rows(raw):
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, dict):
        if isinstance(raw.get("data"), list):
            return raw["data"]
        if isinstance(raw.get("symbols"), list):
            return raw["symbols"]
    return [raw]


def rows(result, operation, *, exchange_name=None, write=False):
    raw = _native(result)
    if isinstance(raw, BaseException):
        raise normalize_error(
            raw, operation, exchange_name=exchange_name, write=write
        ) from None
    check_response(raw, operation, exchange_name=exchange_name, write=write)
    native_rows = _raw_rows(raw)
    getter = getattr(result, "get_data", None)
    if callable(getter):
        try:
            normalized = getter()
            normalized = (
                normalized if isinstance(normalized, (tuple, list)) else [normalized]
            )
            mapped = [
                _dict(row) for item in normalized for row in _raw_rows(_dict(item))
            ]
            if mapped:
                # Keep missing native fields locally for vendor-only values.
                if len(mapped) == len(native_rows):
                    return [
                        {
                            **(dict(native) if isinstance(native, dict) else {}),
                            **{
                                key: value
                                for key, value in item.items()
                                if value is not None
                            },
                        }
                        for native, item in zip(native_rows, mapped, strict=True)
                    ]
                return mapped
            if not native_rows:
                return []
        except (ValueError, TypeError, KeyError, AttributeError, NotImplementedError):
            # Some legacy containers do not normalize newer endpoints yet.
            pass
    return [_dict(item) for item in native_rows]


def _base(exchange_name, row, symbol=None):
    exchange, _, asset = exchange_name.partition("___")
    return {
        "exchange_name": exchange_name,
        "exchange": exchange,
        "asset_type": str(
            pick(row, "asset_type", "market_type", default=asset)
        ).lower(),
        "symbol": str(
            pick(
                row,
                "symbol",
                "symbol_name",
                "instrument",
                "instrument_id",
                "data_name",
                "instId",
                "InstrumentID",
                "trade_symbol",
                default=symbol or "",
            )
        ),
    }


def _unit(exchange_name, row):
    return pick(
        row,
        "quantity_unit",
        default=(
            "lots"
            if exchange_name.startswith("MT5___")
            else (
                "contracts"
                if exchange_name.startswith("CTP___") or exchange_name == "OKX___SWAP"
                else "base"
            )
        ),
    )


def _timestamps(row):
    freshness = row.get("freshness") or {}
    exchange_time = seconds(
        pick(
            row,
            "exchange_time",
            "timestamp",
            "server_time",
            "ts",
            "E",
            "T",
            "uTime",
            "event_time",
            "event_time_utc",
            "timestamp_ms",
            default=freshness.get("observed_at"),
        )
    )
    received_wall = (
        seconds(
            pick(
                row,
                "received_wall_time",
                "local_time",
                "local_update_time",
                "receive_time",
                "recv_time_utc",
            )
        )
        or time.time()
    )
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


def _event_id(event, row):
    native = pick(
        row,
        "event_id",
        "trade_id",
        "tradeId",
        "fill_id",
        "order_id",
        "orderId",
        "ordId",
    )
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


def _finish_event(event, row):
    audit_names = (
        "sequence",
        "sequence_id",
        "previous_sequence",
        "prevSeqId",
        "pu",
        "U",
        "u",
        "checksum",
        "action",
        "ts",
        "E",
        "T",
    )
    event.setdefault(
        "raw_audit_fields", {name: row[name] for name in audit_names if name in row}
    )
    event.setdefault("event_id", _event_id(event, row))
    event.setdefault("coalesced_count", int(pick(row, "coalesced_count", default=0)))
    return event


def metadata(row, exchange_name, symbol=None):
    result = _base(exchange_name, row, symbol)
    filters = {item["filterType"]: item for item in row.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    floor = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
    multiplier = number(
        pick(row, "multiplier", "contract_size", "VolumeMultiple", "ctVal")
    )
    if multiplier is not None and row.get("ctVal") not in (None, ""):
        multiplier *= number(row.get("ctMult"), 1)
    if multiplier is None and exchange_name.startswith("BINANCE___"):
        multiplier = 1.0
    step = number(
        pick(
            row,
            "lot_size",
            "order_size_step",
            "volume_step",
            "lotSz",
            "stepSize",
            default=lot.get("stepSize"),
        )
    )
    result.update(
        quantity_unit=_unit(exchange_name, row),
        multiplier=multiplier,
        lot_size=step,
        order_size_step=step,
        min_size=number(
            pick(
                row,
                "min_size",
                "volume_min",
                "minSz",
                "MinLimitOrderVolume",
                default=lot.get("minQty"),
            )
        ),
        max_size=number(
            pick(
                row,
                "max_size",
                "volume_max",
                "maxLmtSz",
                "MaxLimitOrderVolume",
                default=lot.get("maxQty"),
            )
        ),
        min_notional=number(
            pick(row, "min_notional", default=pick(floor, "notional", "minNotional")), 0
        ),
        tick_size=number(
            pick(
                row,
                "tick_size",
                "price_tick",
                "tickSz",
                "PriceTick",
                default=filters.get("PRICE_FILTER", {}).get("tickSize"),
            )
        ),
        settlement_currency=pick(
            row, "settlement_currency", "settleCcy", "marginAsset", "currency"
        ),
        base_currency=pick(row, "base_currency", "baseAsset", "ctValCcy", "baseCcy"),
        linear=pick(
            row,
            "linear",
            default=(
                row.get("ctType") == "linear"
                if "ctType" in row
                else True if row.get("contractType") == "PERPETUAL" else None
            ),
        ),
        margin_rate=number(
            pick(row, "margin_rate", "LongMarginRatio", "long_margin_rate")
        ),
        commission_rate=number(pick(row, "commission_rate", "taker_commission_rate")),
        exchange_id=pick(row, "exchange_id", "ExchangeID"),
    )
    return result


def balance_rows(source, exchange_name):
    expanded = []
    for row in source:
        details = row.get("details") or row.get("assets") or row.get("balances")
        expanded.extend(details if isinstance(details, list) else [row])
    result = []
    for item in expanded:
        row = _dict(item)
        currency = str(
            pick(
                row,
                "CurrencyID",
                "currency",
                "ccy",
                "asset",
                "account_type",
                "symbol_name",
                "symbol",
                default="",
            )
        )
        cash = decimal_number(
            pick(
                row,
                "available_cash",
                "availEq",
                "availBal",
                "availableBalance",
                "available",
                "cash",
                "available_margin",
                "Available",
                "margin_free",
                "free_margin",
                "free",
            )
        )
        value = decimal_number(
            pick(row, "Balance", "equity", "eq", "marginBalance", "value")
        )
        if value is None:
            wallet = decimal_number(
                pick(
                    row,
                    "balance",
                    "walletBalance",
                    "cashBal",
                    "margin",
                    "total_wallet_balance",
                )
            )
            if wallet is not None:
                value = wallet + decimal_number(
                    pick(row, "crossUnPnl", "unrealizedProfit", "unrealized_profit"), 0
                )
        result.append(
            {
                "exchange_name": exchange_name,
                "currency": currency,
                "cash": cash,
                "value": value,
                "available_cash": cash,
                "equity": value,
                "margin_used": decimal_number(
                    pick(
                        row, "margin_used", "used_margin", "initialMargin", "CurrMargin"
                    )
                ),
                "account_id": pick(row, "account_id", "AccountID"),
            }
        )
    return result


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
        elif not funded and exchange_name == "BINANCE___SWAP":
            # All-zero USD-M accounts still identify their USDT balance row.
            selected = [row for row in selected if row["currency"] == "USDT"]
        else:
            raise ValueError("multiple_account_currencies_require_selection")
    if (
        len(selected) != 1
        or selected[0]["cash"] is None
        or selected[0]["value"] is None
    ):
        raise ValueError("incomplete_account_snapshot")
    return {**selected[0], **_combined_trading_permissions(source)}


def position(row, exchange_name, symbol=None):
    result = _base(exchange_name, row, symbol)
    raw_amount = pick(
        row,
        "quantity",
        "position_volume",
        "volume",
        "size",
        "pos",
        "positionAmt",
        "Position",
        "trade_volume",
    )
    if raw_amount in (None, ""):
        raise ValueError("position_quantity_missing")
    exact_amount = decimal_number(raw_amount)
    amount = number(raw_amount)
    # Preserve a non-zero financial quantity that underflows binary float.  The
    # ordinary float representation remains unchanged for compatible values.
    if amount == 0 and exact_amount != 0:
        amount = exact_amount
    side = str(
        pick(
            row,
            "position_side",
            "direction",
            "position_direction",
            "posSide",
            "positionSide",
            "side",
            default="net",
        )
    ).lower()
    side = {"buy": "long", "sell": "short", "both": "net"}.get(side, side)
    if side not in {"long", "short", "net"}:
        raise ValueError("unknown_position_side")
    quantity = abs(amount) if side in {"long", "short"} else amount
    result.update(
        quantity=quantity,
        quantity_known=True,
        quantity_exact_zero=exact_amount == 0,
        size=quantity,
        volume=abs(amount),
        position_side=side,
        direction=("short" if amount < 0 else "long") if side == "net" else side,
        position_id=pick(row, "position_id", "ticket"),
        quantity_unit=_unit(exchange_name, row),
        price=number(
            pick(
                row,
                "average_price",
                "avg_price",
                "price",
                "avgPx",
                "entryPrice",
                "price_open",
            )
        ),
        today=number(
            pick(row, "today", "today_position", "today_volume", "TodayPosition")
        ),
        yesterday=number(
            pick(row, "yesterday", "yesterday_position", "yd_position", "YdPosition")
        ),
        multiplier=number(
            pick(
                row, "multiplier", "contract_size", "volume_multiple", "VolumeMultiple"
            )
        ),
        offset=pick(row, "offset"),
        exchange_id=pick(row, "exchange_id", "ExchangeID"),
    )
    return result


def _side_value(row, exchange_name, *, position=False, default=None):
    """Separate transaction side from position side using validated values.

    Binance's legacy swap trade getter returns ps (position side) as trade
    side. The original order update S/ps fields are independent authorities.
    """
    candidates = []
    native = row.get("o")
    if exchange_name.startswith("BINANCE___"):
        key = "ps" if position else "S"
        if isinstance(native, dict):
            candidates.append(native.get(key))
        candidates.append(row.get(key))
    names = (
        ("position_side", "posSide", "positionSide")
        if position
        else ("side", "order_side", "trade_side")
    )
    candidates.extend(row.get(name) for name in names)
    candidates.append(default)
    accepted = {"long", "short", "net"} if position else {"buy", "sell"}
    for value in candidates:
        if isinstance(value, Enum):
            value = value.value
        if not isinstance(value, str):
            continue
        value = value.lower()
        if position:
            value = {"both": "net", "buy": "long", "sell": "short"}.get(value, value)
        if value in accepted:
            return value
    return None


def _position_mode_value(row, default=None):
    """Return a canonical position mode only when the payload or request supplies one."""
    if "dualSidePosition" in row:
        value = row["dualSidePosition"]
    else:
        value = pick(row, "position_mode", "positionMode", "posMode", default=default)
    value = getattr(value, "value", value)
    if isinstance(value, bool):
        return "dual_side" if value else "net"
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "net_mode": "net",
        "oneway": "net",
        "one_way": "net",
        "long_short_mode": "dual_side",
        "hedge": "dual_side",
        "hedge_mode": "dual_side",
    }.get(value, value)


def _explicit_execution_identity_fields(row):
    """Identify semantic identity actually carried by the venue payload.

    Order normalization also fills stable venue defaults and request fallbacks.
    Those values are useful to API consumers but are not independent remote
    evidence and therefore must not contradict a previously persisted intent.
    """

    nested = row.get("o") if isinstance(row.get("o"), dict) else {}

    def supplied(source, *names):
        return any(source.get(name) not in (None, "") for name in names)

    fields = set()
    if supplied(row, "side", "order_side", "trade_side", "S") or supplied(nested, "S"):
        fields.add("side")
    if supplied(row, "position_side", "posSide", "positionSide", "ps") or supplied(
        nested, "ps"
    ):
        fields.add("position_side")
    if supplied(row, "offset", "order_offset", "trade_offset"):
        fields.add("offset")
    if supplied(row, "quantity_unit"):
        fields.add("quantity_unit")
    if supplied(row, "position_mode", "positionMode", "posMode", "dualSidePosition"):
        fields.add("position_mode")
    return sorted(fields)


def _trading_permissions(row):
    """Normalize only explicit account/API-key permissions; absence is unknown."""
    raw = pick(row, "trading_permissions", "perm")
    permissions = None
    permission_allows = None
    if isinstance(raw, str):
        raw = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)) and raw:
        values = {str(value).strip().lower() for value in raw}
        known = {"read_only", "trade", "withdraw"}
        if values <= known:
            permissions = sorted(values)
            permission_allows = "trade" in values
    explicit = pick(row, "can_trade", "canTrade")
    explicit = (
        True
        if explicit is True or explicit == "true"
        else False if explicit is False or explicit == "false" else None
    )
    # Account trading disabled or an API key with read-only permissions both
    # prohibit execution, even if the other permission source allows it.
    can_trade = (
        False
        if explicit is False or permission_allows is False
        else True if explicit is True or permission_allows is True else None
    )
    return {"can_trade": can_trade, "trading_permissions": permissions}


def _combined_trading_permissions(source):
    """Combine explicit permission evidence without turning absence into access."""
    snapshots = [_trading_permissions(row) for row in source]
    values = [row["can_trade"] for row in snapshots]
    can_trade = False if False in values else True if True in values else None
    permissions = sorted(
        {
            permission
            for row in snapshots
            for permission in (row["trading_permissions"] or ())
        }
    )
    return {
        "can_trade": can_trade,
        "trading_permissions": permissions or None,
    }


def account_permissions(result, exchange_name):
    """Normalize an account-permission response without requiring balance rows."""
    source = rows(result, "get_account_config", exchange_name=exchange_name)
    if not source:
        raise ValueError("account_permission_snapshot_missing")
    return {
        "exchange_name": exchange_name,
        **_combined_trading_permissions(source),
    }


def order(row, exchange_name, symbol=None, request=None, operation="query_order"):
    result = _base(exchange_name, row, symbol)
    req = asdict(request) if is_dataclass(request) else (request or {})
    ack_payload = row.get("payload")
    if isinstance(ack_payload, dict):
        row = {**row, **ack_payload}
    raw_status = (
        str(pick(row, "status", "order_status", "state", default=""))
        .lower()
        .replace("-", "_")
    )
    status = _STATUS.get(raw_status, "unknown")
    order_id = pick(
        row,
        "order_id",
        "external_order_id",
        "ordId",
        "orderId",
        "order_sys_id",
        "OrderSysID",
    )
    client_id = pick(
        row,
        "client_order_id",
        "clOrdId",
        "clientOrderId",
        "order_ref",
        "OrderRef",
        default=req.get("client_order_id"),
    )
    if not raw_status and order_id and operation == "make_order":
        status = "accepted"
    if not raw_status and row.get("accepted") is False:
        status = "rejected"
    filled = number(
        pick(
            row,
            "filled",
            "filled_quantity",
            "executed_qty",
            "accFillSz",
            "executedQty",
            "VolumeTraded",
        ),
        0,
    )
    average = number(
        pick(row, "avg_price", "average_price", "order_avg_price", "avgPx", "avgPrice")
    )
    if exchange_name.startswith("CTP___"):
        # CTP order reports contain limit price, not an execution VWAP. Deal
        # events are authoritative; never promote the legacy getter's limit.
        average = number(pick(row, "execution_avg_price"))
    if average is None and filled:
        quote = number(pick(row, "cum_quote", "cumQuote", "cummulativeQuoteQty"))
        average = quote / filled if quote is not None else None
    unknown = bool(row.get("execution_unknown")) or status == "unknown"
    trade_authority = exchange_name.startswith("CTP___")
    if filled and (average is None or average <= 0) and not trade_authority:
        unknown = True
    if status == "completed" and filled <= 0:
        # MT5 immediate results name the executed amount volume.
        if exchange_name.startswith("MT5___"):
            filled = number(row.get("volume"), 0)
            average = number(row.get("price"))
            unknown = not (filled and average and average > 0)
        else:
            unknown = True
    result.update(
        kind="order",
        order_id=str(order_id or ""),
        client_order_id=str(client_id or ""),
        status=status if not unknown else "submitted",
        filled=filled,
        avg_price=average,
        size=number(
            pick(
                row,
                "quantity",
                "size",
                "order_size",
                "sz",
                "origQty",
                "VolumeTotalOriginal",
                default=req.get("quantity"),
            )
        ),
        side=_side_value(row, exchange_name, default=req.get("side")),
        position_side=_side_value(
            row, exchange_name, position=True, default=req.get("position_side")
        ),
        position_id=pick(row, "position_id", default=req.get("position_id")),
        offset=pick(row, "offset", "order_offset", default=req.get("offset")),
        quantity_unit=_unit(exchange_name, row),
        position_mode=_position_mode_value(row, default=req.get("position_mode")),
        execution_unknown=unknown,
        terminal_confirmed=not unknown and status in _TERMINAL,
        definite_reject=not unknown and status == "rejected",
        order_ref=pick(
            row, "order_ref", "OrderRef", default=req.get("order_ref") or client_id
        ),
        exchange_id=pick(
            row, "exchange_id", "ExchangeID", default=req.get("exchange_id")
        ),
        front_id=pick(row, "front_id", "FrontID", default=req.get("front_id")),
        session_id=pick(row, "session_id", "SessionID", default=req.get("session_id")),
        account_id=pick(
            row,
            "account_id",
            "AccountID",
            "InvestorID",
            default=req.get("account_id"),
        ),
        trading_day=pick(
            row, "trading_day", "TradingDay", default=req.get("trading_day")
        ),
        fee_currency=pick(row, "fee_currency", "feeCcy", "commissionAsset"),
        commission_currency=pick(
            row, "commission_currency", "fee_currency", "feeCcy", "commissionAsset"
        ),
        commission_source="unavailable",
        execution_source="trades" if trade_authority else "cumulative",
        **_timestamps(row),
    )
    result[_EXPLICIT_IDENTITY_FIELDS] = _explicit_execution_identity_fields(row)
    if row.get("feeCcy") and row.get("fee") not in (None, ""):
        result["cumulative_commission"] = -number(row["fee"])
    elif row.get("cumulative_commission") not in (None, ""):
        result["cumulative_commission"] = number(row["cumulative_commission"])
    if result.get("cumulative_commission") is not None:
        result["commission_source"] = "exchange"
    return result


def trade(row, exchange_name, symbol=None):
    result = _base(exchange_name, row, symbol)
    result.update(
        kind="trade",
        trade_id=str(
            pick(
                row,
                "trade_id",
                "fill_id",
                "deal_id",
                "tradeId",
                "id",
                "TradeID",
                default="",
            )
        ),
        order_id=str(
            pick(
                row,
                "order_id",
                "external_order_id",
                "orderId",
                "ordId",
                "OrderSysID",
                default="",
            )
        ),
        client_order_id=pick(row, "client_order_id", "clOrdId", "OrderRef"),
        size=number(
            pick(
                row,
                "quantity",
                "size",
                "trade_volume",
                "qty",
                "fillSz",
                "volume",
                "Volume",
            )
        ),
        price=number(pick(row, "trade_price", "price", "fillPx", "Price")),
        side=_side_value(row, exchange_name),
        offset=pick(row, "offset", "trade_offset"),
        position_id=pick(row, "position_id"),
        position_side=_side_value(row, exchange_name, position=True),
        quantity_unit=_unit(exchange_name, row),
        position_mode=_position_mode_value(row),
        exchange_id=pick(row, "exchange_id", "ExchangeID"),
        account_id=pick(row, "account_id", "AccountID", "InvestorID"),
        trading_day=pick(row, "trading_day", "TradingDay"),
        fee=number(pick(row, "fee", "trade_fee", "commission")),
        fee_currency=pick(
            row, "fee_currency", "trade_fee_symbol", "commissionAsset", "feeCcy"
        ),
        **_timestamps(row),
    )
    result[_EXPLICIT_IDENTITY_FIELDS] = _explicit_execution_identity_fields(row)
    if row.get("feeCcy") and row.get("fee") not in (None, ""):
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
        return {
            **_base(exchange_name, row, symbol),
            "rate": number(
                pick(row, "funding_rate", "fundingRate", "lastFundingRate", "rate")
            ),
            "next_funding_time": seconds(
                pick(
                    row,
                    "next_funding_time",
                    *(
                        ("fundingTime", "nextFundingTime")
                        if exchange_name.startswith("OKX___")
                        else ("nextFundingTime", "fundingTime")
                    ),
                )
            ),
        }
    if operation == "set_position_mode":
        raw = _native(result)
        code = raw.get("code") if isinstance(raw, dict) else None
        expected_code = "0" if exchange_name == "OKX___SWAP" else "200"
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
        mode = _position_mode_value(row)
        if mode not in {"net", "dual_side"}:
            raise CapabilityNotSupportedError(
                operation, detail="position mode is unavailable"
            )
        return {
            "exchange_name": exchange_name,
            "position_mode": mode,
            **_trading_permissions(row),
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
        return order(source[0], exchange_name, symbol, request, operation)
    if operation == "get_open_orders":
        return [order(row, exchange_name, symbol) for row in source]
    if operation == "get_deals":
        return [trade(row, exchange_name, symbol) for row in source]
    if operation == "get_kline":
        return [
            normalize_event(row, exchange_name, kind="bar", symbol=symbol)
            for row in source
        ]
    if operation in {"get_tick", "get_depth"}:
        return normalize_event(
            source[0],
            exchange_name,
            kind={"get_tick": "tick", "get_depth": "orderbook", "get_kline": "bar"}[
                operation
            ],
            symbol=symbol,
        )
    raise CapabilityNotSupportedError(operation, detail="no normalized result mapper")


def normalize_event(item, exchange_name, *, kind=None, symbol=None):
    row = _dict(item)
    if isinstance(row.get("payload"), dict):
        row = {**row, **row["payload"]}
    kind = (
        kind
        or str(pick(row, "kind", "event_type", "event", "type", default="")).lower()
    )
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
        return _finish_event(order(row, exchange_name, symbol), row)
    if kind == "trade":
        return _finish_event(trade(row, exchange_name, symbol), row)
    if kind == "position":
        return _finish_event(
            {
                "kind": kind,
                **position(row, exchange_name, symbol),
                **_timestamps(row),
            },
            row,
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
            **_timestamps(row),
        }
        if len(balances) == 1:
            event.update(balances[0])
        return _finish_event(event, row)
    result = {**_base(exchange_name, row, symbol), "kind": kind, **_timestamps(row)}
    if kind == "orderbook":

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

        raw_sequence = pick(
            row, "sequence", "sequence_id", "update_id", "lastUpdateId", "u"
        )
        raw_previous = pick(
            row, "previous_sequence", "prev_sequence", "prevSeqId", "pu"
        )
        sequence = int(raw_sequence) if raw_sequence not in (None, "") else None
        previous_sequence = (
            int(raw_previous) if raw_previous not in (None, "") else None
        )
        action = str(pick(row, "snapshot_or_delta", "action", default="")).lower()
        snapshot_or_delta = (
            "delta"
            if action in {"update", "delta"} or row.get("pu") not in (None, "")
            else "snapshot"
        )
        continuity_status = str(pick(row, "continuity_status", default="")).lower()
        if not continuity_status:
            continuity_status = (
                "snapshot"
                if snapshot_or_delta == "snapshot" and sequence is not None
                else "unverified"
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
        result.update(
            bids=levels("bids"),
            asks=levels("asks"),
            quantity_unit=_unit(exchange_name, row),
            sequence=sequence,
            previous_sequence=previous_sequence,
            snapshot_or_delta=snapshot_or_delta,
            continuity_status=continuity_status,
        )
    elif kind == "tick":
        bid = number(pick(row, "bid_price", "bid", "BidPrice1"))
        ask = number(pick(row, "ask_price", "ask", "AskPrice1"))
        price = number(pick(row, "price", "last_price", "last", "LastPrice"))
        if not price and bid and ask:
            price = (bid + ask) / 2
        volume_semantics = str(pick(row, "volume_semantics", default="") or "")
        delta_volume = number(
            pick(
                row,
                "delta_volume",
                default=pick(row, "volume", "last_volume", "Volume"),
            )
        )
        cumulative_volume = number(
            pick(row, "cum_volume", "cumulative_volume", "Volume")
        )
        result.update(
            price=price,
            bid_price=bid,
            ask_price=ask,
            volume=(
                delta_volume
                if volume_semantics == "delta"
                else number(pick(row, "volume", "last_volume", "Volume"))
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
                    pick(row, "trading_day", "TradingDay", default="") or ""
                ),
                action_day=str(pick(row, "action_day", "ActionDay", default="") or ""),
                event_time_utc=seconds(pick(row, "event_time_utc")),
                recv_time_utc=seconds(pick(row, "recv_time_utc")),
                recv_monotonic_ns=int(pick(row, "recv_monotonic_ns", default=0) or 0),
                connection_generation=int(
                    pick(row, "connection_generation", default=0) or 0
                ),
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
            connection_generation=int(
                pick(row, "connection_generation", default=0) or 0
            ),
            scopes=row.get("scopes"),
            failed_scopes=tuple(row.get("failed_scopes") or ()),
        )
    else:
        return None
    return _finish_event(result, row)
