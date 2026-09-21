"""Binance venue order mapping (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest
from bt_api_py._normalization import _trading_permissions, instrument_spec, pick, rows
from bt_api_py._venue_mappers._position import position_intent


def _wire_decimal(value: Decimal | None) -> str | None:
    """Render an exact, non-scientific decimal for signing and wire transport."""
    return None if value is None else format(value, "f")


def map_order_request(request: OrderRequest) -> dict[str, Any]:
    """Map a v1 ``OrderRequest`` to Binance ``make_order`` arguments."""
    offset, closing = position_intent(request)
    if offset in {"close_today", "close_yesterday"} or request.position_id is not None:
        raise CapabilityNotSupportedError(
            "make_order",
            detail="Binance does not support dated or ticket-specific closes",
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
    }
    if request.position_mode == "dual_side":
        result["position_side"] = request.position_side.upper()
        result.pop("reduce_only")
    elif request.position_mode == "net":
        result["position_side"] = "BOTH"
    elif request.position_side is not None:
        result["position_side"] = request.position_side.upper()
    return result


def _native_decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _position_mode(value: Any) -> str | None:
    if value in (True, "true", "TRUE"):
        return "dual_side"
    if value in (False, "false", "FALSE"):
        return "net"
    text = str(value).lower() if value not in (None, "") else None
    if text is None:
        return None
    return {
        "both": "net",
        "one_way": "net",
        "hedge": "dual_side",
    }.get(text, text)


def _margin_mode(value: Any) -> str | None:
    text = str(value).lower() if value not in (None, "") else None
    if text is None:
        return None
    return {"crossed": "cross", "cross_margin": "cross"}.get(text, text)


def _matching_row(result: Any, symbol: str) -> dict[str, Any]:
    if result is None:
        return {}
    source = rows(result, "get_order_readiness")
    matching = [
        row
        for row in source
        if str(pick(row, "symbol", "symbol_name", "instId", default="")) == symbol
    ]
    return matching[0] if matching else (source[0] if len(source) == 1 else {})


def _build_binance_readiness_assessment(
    *,
    quantity_ready: bool,
    min_ready: bool | None,
    lot_ready: bool | None,
    max_ready: bool | None,
    permission: dict[str, Any],
    position_mode_ready: bool | None,
    instrument_live: bool | None,
    margin_mode_ready: bool | None,
    leverage_ready: bool | None,
    spec: Any,
    spec_error: str | None,
    expected_mode_valid: bool,
) -> tuple[dict[str, bool | None], list[str], bool, bool]:
    checks = {
        "quantity_native": quantity_ready,
        "quantity_min_size": min_ready,
        "quantity_lot_size": lot_ready,
        "quantity_max_size": max_ready,
        "trading_permission": permission["can_trade"],
        "position_mode": position_mode_ready,
        "instrument_live": instrument_live,
        "margin_mode": margin_mode_ready,
        "leverage": leverage_ready,
    }

    reasons: list[str] = []
    if not quantity_ready:
        reasons.append("invalid_quantity_native")
    if spec is None:
        reasons.append(f"instrument_rules_unavailable:{spec_error}")
    elif quantity_ready:
        if min_ready is False:
            reasons.append("quantity_below_min_size")
        if lot_ready is False:
            reasons.append("quantity_not_multiple_of_lot_size")
        elif lot_ready is None:
            reasons.append("lot_size_unproven")
        if max_ready is False:
            reasons.append("quantity_above_max_size")
        elif max_ready is None:
            reasons.append("max_size_unproven")
    if permission["can_trade"] is False:
        reasons.append("trading_permission_denied")
    elif permission["can_trade"] is None:
        reasons.append("trading_permission_unproven")
    if not expected_mode_valid:
        reasons.append("invalid_expected_position_mode")
    elif position_mode_ready is False:
        reasons.append("position_mode_mismatch")
    elif position_mode_ready is None:
        reasons.append("position_mode_unproven")
    if instrument_live is False:
        reasons.append("instrument_not_live")
    elif instrument_live is None:
        reasons.append("instrument_state_unproven")
    if margin_mode_ready is False:
        reasons.append("margin_mode_mismatch")
    elif margin_mode_ready is None:
        reasons.append("margin_mode_unproven")
    if leverage_ready is not True:
        reasons.append("leverage_unproven")

    definite_failure = any(check is False for check in checks.values())
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
    exchange_info: Any,
    position_mode_info: Any = None,
    symbol_config: Any = None,
    leverage_info: Any = None,
    max_size: Any = None,
) -> dict[str, Any]:
    """Build a fail-closed Binance USD-M readiness snapshot from read-only data."""
    requested = _native_decimal(quantity_native)
    config_rows = rows(account_config, "get_order_readiness")
    config = config_rows[0] if config_rows else {}
    permission = _trading_permissions(config)

    mode_row = _matching_row(position_mode_info, symbol) if position_mode_info is not None else {}
    mode_value = pick(mode_row, "position_mode", "dualSidePosition")
    if mode_value in (None, ""):
        mode_value = pick(config, "position_mode", "dualSidePosition")
    actual_position_mode = _position_mode(mode_value)

    spec = None
    spec_error = None
    try:
        spec = instrument_spec(exchange_info, exchange_name, symbol)
    except Exception as exc:
        spec_error = str(getattr(exc, "code", type(exc).__name__))

    user_symbol = _matching_row(symbol_config, symbol)
    configured_margin_mode = _margin_mode(pick(user_symbol, "margin_mode", "marginType"))
    leverage = _native_decimal(pick(user_symbol, "leverage"))
    maximum = spec.max_quantity if spec is not None else None
    max_notional = _native_decimal(pick(user_symbol, "max_notional", "maxNotionalValue"))

    leverage_by_side: dict[str, float] = {}
    leverage_rows = rows(leverage_info, "get_order_readiness") if leverage_info is not None else []
    for row in leverage_rows:
        row_symbol = pick(row, "symbol", "instId")
        if row_symbol not in (None, "", symbol):
            continue
        row_margin = _margin_mode(pick(row, "margin_mode", "mgnMode", "marginType"))
        if row_margin not in (None, margin_mode):
            continue
        side = str(pick(row, "position_side", "posSide", default="net")).lower()
        side = {"both": "net"}.get(side, side)
        row_leverage = _native_decimal(pick(row, "leverage", "lever"))
        if side in {"long", "short", "net"} and row_leverage is not None:
            leverage_by_side[side] = float(row_leverage)
    if leverage is None and leverage_by_side:
        leverage = min(Decimal(str(value)) for value in leverage_by_side.values())
    if configured_margin_mode is None and any(
        _margin_mode(pick(row, "margin_mode", "mgnMode", "marginType")) == margin_mode
        for row in leverage_rows
    ):
        configured_margin_mode = margin_mode

    max_buy = None
    max_sell = None
    if max_size is not None:
        maximum_row = _matching_row(max_size, symbol)
        max_buy = _native_decimal(pick(maximum_row, "max_buy", "maxBuy"))
        max_sell = _native_decimal(pick(maximum_row, "max_sell", "maxSell"))
        known_limits = [value for value in (max_buy, max_sell) if value is not None]
        if known_limits:
            maximum = min([maximum, *known_limits] if maximum is not None else known_limits)

    quantity_ready = requested is not None and requested > 0
    min_ready: bool | None = None
    lot_ready: bool | None = None
    max_ready: bool | None = None
    if quantity_ready and spec is not None:
        assert spec.min_quantity is not None and spec.quantity_step is not None
        min_ready = requested >= spec.min_quantity
        try:
            lot_ready = requested % spec.quantity_step == 0
        except InvalidOperation:
            lot_ready = None
        max_ready = requested <= maximum if maximum is not None else None

    expected_mode_valid = expected_position_mode in {None, "net", "dual_side"}
    position_mode_ready = (
        None
        if actual_position_mode not in {"net", "dual_side"}
        else expected_mode_valid
        and (expected_position_mode is None or actual_position_mode == expected_position_mode)
    )
    instrument_status = spec.status.lower() if spec is not None else None
    instrument_live = (
        instrument_status in {"live", "trading", "enabled"}
        if instrument_status is not None
        else None
    )
    leverage_ready = None if leverage is None else leverage > 0
    margin_mode_ready = (
        configured_margin_mode == margin_mode
        if configured_margin_mode in {"cross", "isolated"}
        else None
    )
    checks, reasons, definite_failure, ready = _build_binance_readiness_assessment(
        quantity_ready=quantity_ready,
        min_ready=min_ready,
        lot_ready=lot_ready,
        max_ready=max_ready,
        permission=permission,
        position_mode_ready=position_mode_ready,
        instrument_live=instrument_live,
        margin_mode_ready=margin_mode_ready,
        leverage_ready=leverage_ready,
        spec=spec,
        spec_error=spec_error,
        expected_mode_valid=expected_mode_valid,
    )
    return {
        "ready": ready,
        "definite_failure": definite_failure,
        "reasons": reasons,
        "execution_unproven": True,
        "exchange_name": exchange_name,
        "symbol": symbol,
        "requested_quantity_native": float(requested) if requested is not None else None,
        "quantity_unit": spec.quantity_unit if spec is not None else "base",
        "margin_mode": margin_mode,
        "configured_margin_mode": configured_margin_mode,
        "position_mode": actual_position_mode,
        "expected_position_mode": expected_position_mode,
        "can_trade": permission["can_trade"],
        "trading_permissions": permission["trading_permissions"],
        "instrument_state": instrument_status,
        "min_size": float(spec.min_quantity) if spec is not None else None,
        "lot_size": float(spec.quantity_step) if spec is not None else None,
        "max_size": float(maximum) if maximum is not None else None,
        "max_buy": float(max_buy) if max_buy is not None else None,
        "max_sell": float(max_sell) if max_sell is not None else None,
        "max_notional": float(max_notional) if max_notional is not None else None,
        "leverage": float(leverage) if leverage is not None else None,
        "leverage_by_position_side": leverage_by_side,
        "checks": checks,
    }
