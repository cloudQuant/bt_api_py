"""Venue order mappers registry (Task 2.2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._venue_mappers.binance import map_order_request as _binance_map
from bt_api_py._venue_mappers.ctp import map_order_request as _ctp_map
from bt_api_py._venue_mappers.okx import map_order_request as _okx_map

OrderMapper = Callable[[OrderRequest], dict[str, Any]]

MAPPERS: dict[str, OrderMapper] = {
    "BINANCE___SPOT": _binance_map,
    "OKX___SPOT": _okx_map,
    "CTP___FUTURE": _ctp_map,
}


def get_venue_mapper(exchange_name: str) -> OrderMapper | None:
    """Return the order mapper for ``exchange_name``, or ``None`` if unmapped."""
    return MAPPERS.get(exchange_name)


__all__ = ["MAPPERS", "get_venue_mapper"]
