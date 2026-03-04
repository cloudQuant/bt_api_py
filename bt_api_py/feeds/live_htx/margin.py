"""HTX Margin Trading Feed

Margin trading shares spot's market data and order endpoints,
but uses different account types (margin-api source).
"""

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataMargin
from bt_api_py.feeds.live_htx.spot import (
    HtxAccountWssDataSpot,
    HtxMarketWssDataSpot,
    HtxRequestDataSpot,
)
from bt_api_py.logging_factory import get_logger


class HtxRequestDataMargin(HtxRequestDataSpot):
    """HTX Margin trading REST API feed.

    Inherits all spot methods since margin uses the same endpoints
    with source=margin-api parameter.
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MARGIN")
        self.logger_name = kwargs.get("logger_name", "htx_margin_feed.log")
        self._params = HtxExchangeDataMargin()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")


class HtxMarketWssDataMargin(HtxMarketWssDataSpot):
    """HTX Margin Market WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataMargin())
        kwargs.setdefault("asset_type", "MARGIN")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataMargin(HtxAccountWssDataSpot):
    """HTX Margin Account WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataMargin())
        kwargs.setdefault("asset_type", "MARGIN")
        super().__init__(data_queue, **kwargs)
