"""
Bybit Exchange Registration
"""

from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot
from bt_api_py.feeds.live_bybit.swap import BybitRequestDataSwap
from bt_api_py.containers.exchanges.bybit_exchange_data import (
    BybitExchangeDataSpot,
    BybitExchangeDataSwap,
)


def register_bybit():
    """Register Bybit Spot and Swap feeds"""
    # Spot
    ExchangeRegistry.register_feed("BYBIT___SPOT", BybitRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BYBIT___SPOT", BybitExchangeDataSpot)

    # Swap
    ExchangeRegistry.register_feed("BYBIT___SWAP", BybitRequestDataSwap)
    ExchangeRegistry.register_exchange_data("BYBIT___SWAP", BybitExchangeDataSwap)


# Import this module to register the feeds
register_bybit()