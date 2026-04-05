from __future__ import annotations

from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.adapters.binance_adapter import BinanceGatewayAdapter
from bt_api_py.gateway.adapters.ctp_adapter import CtpGatewayAdapter
from bt_api_py.gateway.adapters.ib_web_adapter import IbWebGatewayAdapter
from bt_api_py.gateway.adapters.okx_adapter import OkxGatewayAdapter

try:
    from bt_api_py.gateway.adapters.mt5_adapter import Mt5GatewayAdapter
except ImportError:
    Mt5GatewayAdapter = None  # type: ignore[assignment,misc]

__all__ = [
    "BaseGatewayAdapter",
    "BinanceGatewayAdapter",
    "CtpGatewayAdapter",
    "IbWebGatewayAdapter",
    "Mt5GatewayAdapter",
    "OkxGatewayAdapter",
]
