"""Validate portable position intent before a venue-specific order is built."""

from bt_api_base.exceptions import InvalidOrderError

from bt_api_py._contracts.models import OrderRequest


def position_intent(request: OrderRequest) -> tuple[str, bool]:
    offset = request.offset or ("close" if request.reduce_only else "open")
    closing = offset in {"close", "close_today", "close_yesterday"}
    if request.reduce_only and not closing:
        raise InvalidOrderError("", request.symbol, "reduce_only contradicts an opening offset")
    side = request.position_side
    if request.position_mode == "dual_side" and side not in {"long", "short"}:
        raise InvalidOrderError(
            "", request.symbol, "dual_side orders require an explicit long or short position_side"
        )
    if side in {"long", "short"}:
        expected_buy = (side == "long") != closing
        if (request.side.value == "buy") != expected_buy:
            raise InvalidOrderError(
                "", request.symbol, "order side contradicts position_side and offset"
            )
    return offset, closing
