"""
Curve DEX Registry Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _curve_balance_handler
from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeDataSpot
from bt_api_py.feeds.live_curve.spot import CurveRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_curve():
    """Register Curve DEX to global ExchangeRegistry"""
    # Register Feed class
    ExchangeRegistry.register_feed("CURVE___DEX", CurveRequestDataSpot)

    # Register config class
    ExchangeRegistry.register_exchange_data("CURVE___DEX", CurveExchangeDataSpot)

    # Register balance handler (for on-chain balances)
    ExchangeRegistry.register_balance_handler("CURVE___DEX", _curve_balance_handler)


# Auto-register on module import
register_curve()
