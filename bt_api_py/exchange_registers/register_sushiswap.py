"""
SushiSwap Exchange Registration Module
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _sushiswap_balance_handler
from bt_api_py.containers.exchanges.sushiswap_exchange_data import (
    SushiSwapChain,
    SushiSwapExchangeDataSpot,
)
from bt_api_py.feeds.live_sushiswap.spot import SushiSwapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_sushiswap():
    """Register SushiSwap DEX to global ExchangeRegistry"""
    # Register main DEX identifier
    ExchangeRegistry.register_feed("SUSHISWAP___DEX", SushiSwapRequestDataSpot)
    ExchangeRegistry.register_exchange_data("SUSHISWAP___DEX", SushiSwapExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("SUSHISWAP___DEX", _sushiswap_balance_handler)

    # Register chain-specific identifiers
    chains = [
        (SushiSwapChain.ETHEREUM, "ETHEREUM"),
        (SushiSwapChain.ARBITRUM, "ARBITRUM"),
        (SushiSwapChain.POLYGON, "POLYGON"),
        (SushiSwapChain.OPTIMISM, "OPTIMISM"),
        (SushiSwapChain.BSC, "BSC"),
        (SushiSwapChain.AVALANCHE, "AVALANCHE"),
    ]

    for _chain, chain_name in chains:
        identifier = f"SUSHISWAP___{chain_name}"
        ExchangeRegistry.register_feed(identifier, SushiSwapRequestDataSpot)
        ExchangeRegistry.register_exchange_data(identifier, SushiSwapExchangeDataSpot)
        ExchangeRegistry.register_balance_handler(identifier, _sushiswap_balance_handler)


# Auto-register on module import
register_sushiswap()
