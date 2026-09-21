"""OKX venue order mapping (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest
from bt_api_py._normalization import _trading_permissions, number, pick, rows
from bt_api_py._venue_mappers._position import position_intent


def _wire_decimal(value: Decimal | None) -> str | None:
    """Render an exact, non-scientific decimal for signing and wire transport."""
    return None if value is None else format(value, "f")


def map_order_request(request: OrderRequest) -> dict[str, Any]:
    """Map a v1 ``OrderRequest`` to OKX ``make_order`` arguments."""
    offset, closing = position_intent(request)
    if offset in {"close_today", "close_yesterday"} or request.position_id is not None:
        raise CapabilityNotSupportedError(
            "make_order",
            detail="OKX does not support dated or ticket-specific closes",
            definite_reject=True,
        )
    result = {
        "symbol": request.symbol,
        "vol": _wire_decimal(request.quantity),
        "price": _wire_decimal(request.price),
        "order_type": f"{request.side.value}-{request.order_type.value}",
        "offset": offset,
        "post_only": False,
        "client_order_id": request.client_order_id,
        "reduce_only": closing,
        "time_in_force": request.time_in_force,
        "size_in_contracts": request.quantity_unit in {"contracts", "native"},
    }
    if request.position_mode == "dual_side":
        result["position_side"] = request.position_side
    elif request.position_mode == "net":
        result["position_side"] = "net"
    elif request.position_side is not None:
        result["position_side"] = request.position_side
    return result


def _native_decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _position_mode(value: Any) -> str | None:
    return {"net_mode": "net", "long_short_mode": "dual_side"}.get(value, value)


def _build_okx_readiness_assessment(
    *,
    quantity_ready: bool,
    min_size_ready: bool | None,
    lot_size_ready: bool | None,
    permission: dict[str, Any],
    account_level_ready: bool | None,
    position_mode_matches: bool | None,
    instrument_ready: bool,
    leverage_ready: bool | None,
    max_buy_ready: bool | None,
    max_sell_ready: bool | None,
    expected_mode_valid: bool,
    matching_instruments: Any,
    instrument_state: Any,
) -> tuple[dict[str, bool | None], list[str], bool, bool]:
    checks = {
        "quantity_native": quantity_ready,
        "quantity_min_size": min_size_ready,
        "quantity_lot_size": lot_size_ready,
        "trading_permission": permission["can_trade"],
        "derivatives_account_level": account_level_ready,
        "position_mode": position_mode_matches,
        "instrument_live": instrument_ready,
        "leverage": leverage_ready,
        "max_buy": max_buy_ready,
        "max_sell": max_sell_ready,
    }

    reasons: list[str] = []
    if not quantity_ready:
        reasons.append("invalid_quantity_native")
    elif matching_instruments:
        if min_size_ready is False:
            reasons.append("quantity_below_min_size")
        elif min_size_ready is None:
            reasons.append("min_size_unproven")
        if lot_size_ready is False:
            reasons.append("quantity_not_multiple_of_lot_size")
        elif lot_size_ready is None:
            reasons.append("lot_size_unproven")
    if permission["can_trade"] is False:
        reasons.append("trading_permission_denied")
    elif permission["can_trade"] is None:
        reasons.append("trading_permission_unproven")
    if account_level_ready is False:
        reasons.append("account_level_has_no_derivatives")
    elif account_level_ready is None:
        reasons.append("account_level_unproven")
    if not expected_mode_valid:
        reasons.append("invalid_expected_position_mode")
    elif position_mode_matches is False:
        reasons.append("position_mode_mismatch")
    elif position_mode_matches is None:
        reasons.append("position_mode_unproven")
    if not matching_instruments:
        reasons.append("instrument_not_enabled")
    elif instrument_state != "live":
        reasons.append("instrument_not_live")
    if leverage_ready is False:
        reasons.append("leverage_configuration_missing")
    elif leverage_ready is None:
        reasons.append("leverage_unproven")
    if max_buy_ready is False:
        reasons.append("max_buy_insufficient")
    elif max_buy_ready is None:
        reasons.append("max_buy_unproven")
    if max_sell_ready is False:
        reasons.append("max_sell_insufficient")
    elif max_sell_ready is None:
        reasons.append("max_sell_unproven")

    definite_failure = any(
        check is False
        for name, check in checks.items()
        if name
        in {
            "quantity_native",
            "quantity_min_size",
            "quantity_lot_size",
            "trading_permission",
            "derivatives_account_level",
            "position_mode",
            "instrument_live",
            "leverage",
            "max_buy",
            "max_sell",
        }
    )
    ready = all(check is True for check in checks.values())
    return checks, reasons, definite_failure, ready


def normalize_order_readiness(
    exchange_name: str,
    symbol: str,
    quantity_native: Any,
    *,
    margin_mode: str,
    expected_position_mode: str | None,
    account_config: Any,
    account_instruments: Any,
    leverage_info: Any = None,
    max_size: Any = None,
) -> dict[str, Any]:
    """Build a conservative OKX order-readiness snapshot from read-only responses.

    A passing snapshot only proves that the inspected account constraints are
    compatible with the requested native contract quantity. It never proves
    that a later order will be accepted.
    """
    requested = _native_decimal(quantity_native)
    config_rows = rows(account_config, "get_order_readiness")
    config = config_rows[0] if config_rows else {}
    permission = _trading_permissions(config)
    account_level = pick(config, "account_level", "acctLv")
    account_level = str(account_level) if account_level not in (None, "") else None
    actual_position_mode = _position_mode(pick(config, "position_mode", "posMode"))

    instrument_rows = rows(account_instruments, "get_order_readiness")
    matching_instruments = [
        row
        for row in instrument_rows
        if str(pick(row, "symbol", "symbol_name", "instId", default="")) == symbol
    ]
    instrument = matching_instruments[0] if matching_instruments else {}
    instrument_state = pick(instrument, "instrument_state", "symbol_status", "state")
    instrument_state = str(instrument_state).lower() if instrument_state not in (None, "") else None
    lot_size = _native_decimal(pick(instrument, "lot_size", "order_size_step", "lotSz"))
    min_size = _native_decimal(pick(instrument, "min_size", "minSz"))

    expected_mode_valid = expected_position_mode in {None, "net", "dual_side"}
    position_mode_matches = (
        None
        if actual_position_mode not in {"net", "dual_side"}
        else expected_mode_valid
        and (expected_position_mode is None or expected_position_mode == actual_position_mode)
    )
    account_level_ready = (
        True if account_level in {"2", "3", "4"} else False if account_level == "1" else None
    )

    leverage_by_side: dict[str, float] = {}
    leverage_ready: bool | None = None
    if leverage_info is not None:
        for row in rows(leverage_info, "get_order_readiness"):
            row_symbol = pick(row, "symbol", "instId")
            row_margin = pick(row, "margin_mode", "mgnMode")
            if row_symbol not in (None, "", symbol) or row_margin not in (
                None,
                "",
                margin_mode,
            ):
                continue
            side = str(pick(row, "position_side", "posSide", default="net")).lower()
            side = {"both": "net"}.get(side, side)
            leverage = number(pick(row, "leverage", "lever"))
            if side in {"long", "short", "net"} and leverage is not None:
                leverage_by_side[side] = leverage
        needed_sides = {"long", "short"} if actual_position_mode == "dual_side" else {"net"}
        leverage_ready = needed_sides <= leverage_by_side.keys()

    max_buy = None
    max_sell = None
    max_buy_ready: bool | None = None
    max_sell_ready: bool | None = None
    if max_size is not None:
        max_rows = rows(max_size, "get_order_readiness")
        matching_max = [
            row for row in max_rows if pick(row, "symbol", "instId") in (None, "", symbol)
        ]
        maximum = matching_max[0] if matching_max else {}
        max_buy_value = _native_decimal(pick(maximum, "max_buy", "maxBuy"))
        max_sell_value = _native_decimal(pick(maximum, "max_sell", "maxSell"))
        max_buy = float(max_buy_value) if max_buy_value is not None else None
        max_sell = float(max_sell_value) if max_sell_value is not None else None
        if requested is not None and requested > 0:
            max_buy_ready = max_buy_value >= requested if max_buy_value is not None else None
            max_sell_ready = max_sell_value >= requested if max_sell_value is not None else None

    quantity_ready = requested is not None and requested > 0
    min_size_ready: bool | None = None
    lot_size_ready: bool | None = None
    if quantity_ready and matching_instruments:
        if min_size is not None and min_size > 0:
            min_size_ready = requested >= min_size
        if lot_size is not None and lot_size > 0:
            try:
                lot_size_ready = requested % lot_size == 0
            except InvalidOperation:
                # Decimal modulo can exceed the active context for pathological
                # exponents. Such a quantity remains unproven and cannot pass.
                lot_size_ready = None
    instrument_ready = bool(matching_instruments) and instrument_state == "live"
    checks, reasons, definite_failure, ready = _build_okx_readiness_assessment(
        quantity_ready=quantity_ready,
        min_size_ready=min_size_ready,
        lot_size_ready=lot_size_ready,
        permission=permission,
        account_level_ready=account_level_ready,
        position_mode_matches=position_mode_matches,
        instrument_ready=instrument_ready,
        leverage_ready=leverage_ready,
        max_buy_ready=max_buy_ready,
        max_sell_ready=max_sell_ready,
        expected_mode_valid=expected_mode_valid,
        matching_instruments=matching_instruments,
        instrument_state=instrument_state,
    )
    return {
        "ready": ready,
        "definite_failure": definite_failure,
        "reasons": reasons,
        "execution_unproven": True,
        "exchange_name": exchange_name,
        "symbol": symbol,
        "requested_quantity_native": (float(requested) if requested is not None else None),
        "quantity_unit": "native_contracts",
        "margin_mode": margin_mode,
        "position_mode": actual_position_mode,
        "expected_position_mode": expected_position_mode,
        "account_level": account_level,
        "can_trade": permission["can_trade"],
        "trading_permissions": permission["trading_permissions"],
        "instrument_state": instrument_state,
        "min_size": float(min_size) if min_size is not None and min_size > 0 else None,
        "lot_size": float(lot_size) if lot_size is not None and lot_size > 0 else None,
        "leverage_by_position_side": leverage_by_side,
        "max_buy": max_buy,
        "max_sell": max_sell,
        "checks": checks,
    }
