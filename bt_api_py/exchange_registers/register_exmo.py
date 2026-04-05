"""
EXMO Exchange Registry Module
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _exmo_balance_handler
from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeDataSpot
from bt_api_py.feeds.live_exmo.spot import ExmoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_exmo():
    """Register EXMO SPOT to global ExchangeRegistry"""
    # Register Feed class
    ExchangeRegistry.register_feed("EXMO___SPOT", ExmoRequestDataSpot)

    # Register config class
    ExchangeRegistry.register_exchange_data("EXMO___SPOT", ExmoExchangeDataSpot)

    # Register balance handler
    ExchangeRegistry.register_balance_handler("EXMO___SPOT", _exmo_balance_handler)


# Auto-register on module import
register_exmo()
