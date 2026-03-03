"""
Foxbit Exchange Registry Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _foxbit_balance_handler
from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeDataSpot
from bt_api_py.feeds.live_foxbit.spot import FoxbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_foxbit():
    """Register Foxbit SPOT to global ExchangeRegistry"""
    # Register Feed class
    ExchangeRegistry.register_feed("FOXBIT___SPOT", FoxbitRequestDataSpot)

    # Register config class
    ExchangeRegistry.register_exchange_data("FOXBIT___SPOT", FoxbitExchangeDataSpot)

    # Register balance handler
    ExchangeRegistry.register_balance_handler("FOXBIT___SPOT", _foxbit_balance_handler)


# Auto-register on module import
register_foxbit()
