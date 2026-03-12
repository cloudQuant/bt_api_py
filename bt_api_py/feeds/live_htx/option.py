"""HTX Option Trading Feed

Uses api.hbdm.com with /option-api/ and /option-ex/ prefixes.
Symbol format: BTC-USDT (uppercase, dash-separated).
Market data uses 'contract_code' parameter instead of 'symbol'.
"""

from typing import Any

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataOption
from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot, HtxMarketWssDataSpot
from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap
from bt_api_py.logging_factory import get_logger


class HtxRequestDataOption(HtxRequestDataUsdtSwap):
    """HTX Option REST API feed.

    Shares the same method structure as USDT swap (contract_code param),
    just with different exchange data (paths, base URL).
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self.logger_name = kwargs.get("logger_name", "htx_option_feed.log")
        self._params = HtxExchangeDataOption()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")


class HtxMarketWssDataOption(HtxMarketWssDataSpot):
    """HTX Option Market WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataOption())
        kwargs.setdefault("asset_type", "OPTION")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataOption(HtxAccountWssDataSpot):
    """HTX Option Account WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataOption())
        kwargs.setdefault("asset_type", "OPTION")
        super().__init__(data_queue, **kwargs)
