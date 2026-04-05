"""
Balancer Feed - DEX integration for Balancer protocol.

Balancer is a decentralized exchange (DEX) on Ethereum and other EVM chains.
This module provides REST API integration using GraphQL queries.

Supported chains:
- MAINNET (Ethereum)
- POLYGON
- ARBITRUM
- OPTIMISM
- GNOSIS
- AVALANCHE
- BASE
"""

from __future__ import annotations

from bt_api_py.feeds.live_balancer.spot import BalancerRequestDataSpot

__all__ = [
    "BalancerRequestDataSpot",
]
