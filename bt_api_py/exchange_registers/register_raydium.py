"""
Raydium DEX Registry Module
Register Raydium DEX feed, exchange data to global ExchangeRegistry.
Import this module to complete registration.
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _raydium_balance_handler
from bt_api_py.containers.exchanges.raydium_exchange_data import RaydiumExchangeDataSpot
from bt_api_py.feeds.live_raydium.spot import RaydiumRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_raydium() -> None:
    """Register Raydium DEX to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    """
    ExchangeRegistry.register_feed("RAYDIUM___DEX", RaydiumRequestDataSpot)
    ExchangeRegistry.register_exchange_data("RAYDIUM___DEX", RaydiumExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("RAYDIUM___DEX", _raydium_balance_handler)


register_raydium()
