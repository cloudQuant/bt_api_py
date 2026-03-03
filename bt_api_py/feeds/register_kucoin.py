"""
KuCoin exchange registration module.
Registers KuCoin Spot and Futures feeds, exchange data, and handlers to global ExchangeRegistry.
Import this module to complete registration.
"""

from bt_api_py.balance_utils import simple_balance_handler as _kucoin_balance_handler
from bt_api_py.containers.exchanges.kucoin_exchange_data import (
    KuCoinExchangeDataSpot,
    KuCoinExchangeDataFutures,
)
from bt_api_py.feeds.live_kucoin.spot import KuCoinRequestDataSpot
from bt_api_py.feeds.live_kucoin.futures import KuCoinRequestDataFutures
from bt_api_py.registry import ExchangeRegistry


def register_kucoin():
    """Register KuCoin Spot and Futures to global ExchangeRegistry."""
    # Spot
    ExchangeRegistry.register_feed("KUCOIN___SPOT", KuCoinRequestDataSpot)
    ExchangeRegistry.register_exchange_data("KUCOIN___SPOT", KuCoinExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("KUCOIN___SPOT", _kucoin_balance_handler)
    # Futures
    ExchangeRegistry.register_feed("KUCOIN___FUTURES", KuCoinRequestDataFutures)
    ExchangeRegistry.register_exchange_data("KUCOIN___FUTURES", KuCoinExchangeDataFutures)
    ExchangeRegistry.register_balance_handler("KUCOIN___FUTURES", _kucoin_balance_handler)


# Module import triggers auto-registration
register_kucoin()
