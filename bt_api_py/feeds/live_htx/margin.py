"""HTX Margin Trading Feed.

Margin trading shares spot's market data and order endpoints,
but uses different account types (margin-api source).
"""

from typing import Any

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataMargin
from bt_api_py.feeds.live_htx.spot import (
    HtxAccountWssDataSpot,
    HtxMarketWssDataSpot,
    HtxRequestDataSpot,
)


class HtxRequestDataMargin(HtxRequestDataSpot):
    """HTX Margin REST API data feed."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize HTX Margin request data feed.

        Args:
            data_queue: Data queue for pushing market data
            **kwargs: Additional keyword arguments
        """
        kwargs.setdefault("exchange_data", HtxExchangeDataMargin())
        kwargs.setdefault("asset_type", "MARGIN")
        super().__init__(data_queue, **kwargs)


class HtxMarketWssDataMargin(HtxMarketWssDataSpot):
    """HTX Margin Market WebSocket data feed."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize HTX Margin market WebSocket data feed.

        Args:
            data_queue: Data queue for pushing market data
            **kwargs: Additional keyword arguments
        """
        kwargs.setdefault("exchange_data", HtxExchangeDataMargin())
        kwargs.setdefault("asset_type", "MARGIN")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataMargin(HtxAccountWssDataSpot):
    """HTX Margin Account WebSocket data feed."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize HTX Margin account WebSocket data feed.

        Args:
            data_queue: Data queue for pushing account data
            **kwargs: Additional keyword arguments
        """
        kwargs.setdefault("exchange_data", HtxExchangeDataMargin())
        kwargs.setdefault("asset_type", "MARGIN")
        super().__init__(data_queue, **kwargs)
