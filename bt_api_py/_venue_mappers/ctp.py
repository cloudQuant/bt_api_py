"""CTP venue order mapping (Task 2.2).

CTP is a Chinese futures market that accepts limit orders only, so market
orders are rejected before any exchange call.
"""

from __future__ import annotations

from typing import Any

from bt_api_base.exceptions import InvalidOrderError

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType
from bt_api_py._venue_mappers._position import position_intent


def map_order_request(request: OrderRequest) -> dict[str, Any]:
    """Map a v1 ``OrderRequest`` to CTP ``make_order`` arguments."""
    if request.order_type is OrderType.MARKET:
        raise CapabilityNotSupportedError(
            "make_order", detail="CTP supports limit orders only", definite_reject=True
        )
    offset, _ = position_intent(request)
    if request.quantity != request.quantity.to_integral_value():
        raise InvalidOrderError(
            "CTP___FUTURE", request.symbol, "CTP quantities must be integer contract lots"
        )
    if request.position_id is not None:
        raise CapabilityNotSupportedError(
            "make_order",
            detail="CTP closes use offset, not position tickets",
            definite_reject=True,
        )
    result = {
        "symbol": request.symbol,
        "vol": float(request.quantity),
        "price": float(request.price) if request.price is not None else None,
        "order_type": f"{request.side.value}-{request.order_type.value}",
        "offset": offset,
        "post_only": False,
        "client_order_id": request.client_order_id,
        "time_in_force": request.time_in_force,
    }
    if request.exchange_id is not None:
        result["exchange_id"] = request.exchange_id
    return result
