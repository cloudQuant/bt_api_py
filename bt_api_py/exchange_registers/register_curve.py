"""
Curve DEX Registry Module
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _curve_balance_handler
from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeDataSpot
from bt_api_py.feeds.live_curve.spot import CurveRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_curve() -> None:
    """Register Curve DEX to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    """
    ExchangeRegistry.register_feed("CURVE___DEX", CurveRequestDataSpot)
    ExchangeRegistry.register_exchange_data("CURVE___DEX", CurveExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("CURVE___DEX", _curve_balance_handler)


register_curve()
