"""HTX Option Trading Feed

Uses api.hbdm.com with /option-api/ and /option-ex/ prefixes.
Symbol format: BTC-USDT (uppercase, dash-separated).
Market data uses 'contract_code' parameter instead of 'symbol'.
"""

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataOption
from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot, HtxMarketWssDataSpot
from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import get_project_log_path


class HtxRequestDataOption(HtxRequestDataUsdtSwap):
    """HTX Option REST API feed.

    Shares the same method structure as USDT swap (contract_code param),
    just with different exchange data (paths, base URL).
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self.logger_name = kwargs.get("logger_name", "htx_option_feed.log")
        self._params = HtxExchangeDataOption()
        self.request_logger = SpdLogManager(
            get_project_log_path(self.logger_name), "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            get_project_log_path(self.logger_name), "async_request", 0, 0, False
        ).create_logger()


class HtxMarketWssDataOption(HtxMarketWssDataSpot):
    """HTX Option Market WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataOption())
        kwargs.setdefault("asset_type", "OPTION")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataOption(HtxAccountWssDataSpot):
    """HTX Option Account WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataOption())
        kwargs.setdefault("asset_type", "OPTION")
        super().__init__(data_queue, **kwargs)
