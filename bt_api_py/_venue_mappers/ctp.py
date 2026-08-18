"""CTP venue order mapping (Task 2.2).

CTP is a Chinese futures market that accepts limit orders only, so market
orders are rejected before any exchange call.
"""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType


def map_order_request(request: OrderRequest) -> dict[str, Any]:
    """Map a v1 ``OrderRequest`` to CTP ``make_order`` arguments."""
    if request.order_type is OrderType.MARKET:
        raise CapabilityNotSupportedError("make_order", detail="CTP supports limit orders only")
    return {
        "symbol": request.symbol,
        "vol": float(request.quantity),
        "price": float(request.price) if request.price is not None else None,
        "order_type": f"{request.side.value}-{request.order_type.value}",
        "offset": "close" if request.reduce_only else "open",
        "post_only": False,
        "client_order_id": request.client_order_id,
        "reduce_only": request.reduce_only,
    }
