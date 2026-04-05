"""
KuCoin exchange registration module.
Registers KuCoin Spot and Futures feeds, exchange data, and handlers to global ExchangeRegistry.
Import this module to complete registration.
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _kucoin_balance_handler
from bt_api_py.containers.exchanges.kucoin_exchange_data import (
    KuCoinExchangeDataFutures,
    KuCoinExchangeDataSpot,
)
from bt_api_py.feeds.live_kucoin.futures import KuCoinRequestDataFutures
from bt_api_py.feeds.live_kucoin.spot import KuCoinRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_kucoin() -> None:
    """Register KuCoin Spot and Futures to global ExchangeRegistry.

    This function registers:
    - Spot feed class, exchange data, and balance handler
    - Futures feed class, exchange data, and balance handler
    """
    ExchangeRegistry.register_feed("KUCOIN___SPOT", KuCoinRequestDataSpot)
    ExchangeRegistry.register_exchange_data("KUCOIN___SPOT", KuCoinExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("KUCOIN___SPOT", _kucoin_balance_handler)

    ExchangeRegistry.register_feed("KUCOIN___FUTURES", KuCoinRequestDataFutures)
    ExchangeRegistry.register_exchange_data("KUCOIN___FUTURES", KuCoinExchangeDataFutures)
    ExchangeRegistry.register_balance_handler("KUCOIN___FUTURES", _kucoin_balance_handler)


register_kucoin()
