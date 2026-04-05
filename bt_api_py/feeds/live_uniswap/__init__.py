"""
Uniswap Feed - DEX integration for Uniswap protocol.

Uniswap is a decentralized exchange (DEX) on Ethereum and other EVM chains.
This module provides REST API integration using the Uniswap Trading API.

Supported chains:
- ETHEREUM (1)
- ARBITRUM (42161)
- OPTIMISM (10)
- POLYGON (137)
- BSC (56)
- AVALANCHE (43114)
- BASE (8453)
"""

from __future__ import annotations

from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot

__all__ = [
    "UniswapRequestDataSpot",
]
