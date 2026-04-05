"""
OKX Feed - Backward compatibility re-export module.

All classes have been moved to bt_api_py.feeds.live_okx package.
This file re-exports everything for backward compatibility.

1. okx单币种模式下，现货下单的时候返回的订单种类并不是SPOT, 而是MARGIN, 订阅orders的时候需要制定为MARGIN
"""

from __future__ import annotations

from bt_api_py.feeds.live_okx.account_wss_base import (
    OkxAccountWssData,
    OkxKlineWssData,
    OkxMarketWssData,
)

# Futures
from bt_api_py.feeds.live_okx.futures import (
    OkxAccountWssDataFutures,
    OkxKlineWssDataFutures,
    OkxMarketWssDataFutures,
    OkxRequestDataFutures,
    OkxWssDataFutures,
)
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData

# Base classes
from bt_api_py.feeds.live_okx.request_base import OkxRequestData

# Spot
from bt_api_py.feeds.live_okx.spot import (
    OkxAccountWssDataSpot,
    OkxKlineWssDataSpot,
    OkxMarketWssDataSpot,
    OkxRequestDataSpot,
    OkxWssDataSpot,
)

# Swap
from bt_api_py.feeds.live_okx.swap import (
    OkxAccountWssDataSwap,
    OkxKlineWssDataSwap,
    OkxMarketWssDataSwap,
    OkxRequestDataSwap,
    OkxWssDataSwap,
)

__all__ = [
    # Base
    "OkxRequestData",
    "OkxWssData",
    "OkxAccountWssData",
    "OkxMarketWssData",
    "OkxKlineWssData",
    # Swap
    "OkxRequestDataSwap",
    "OkxAccountWssDataSwap",
    "OkxMarketWssDataSwap",
    "OkxKlineWssDataSwap",
    "OkxWssDataSwap",
    # Spot
    "OkxRequestDataSpot",
    "OkxAccountWssDataSpot",
    "OkxMarketWssDataSpot",
    "OkxKlineWssDataSpot",
    "OkxWssDataSpot",
    # Futures
    "OkxRequestDataFutures",
    "OkxAccountWssDataFutures",
    "OkxMarketWssDataFutures",
    "OkxKlineWssDataFutures",
    "OkxWssDataFutures",
]


if __name__ == "__main__":
    pass
