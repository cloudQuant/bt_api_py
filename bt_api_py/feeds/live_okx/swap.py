from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.feeds.live_okx.account_wss_base import (
    OkxAccountWssData,
    OkxKlineWssData,
    OkxMarketWssData,
)
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData
from bt_api_py.feeds.live_okx.request_base import OkxRequestData
from bt_api_py.functions.log_message import SpdLogManager


class OkxRequestDataSwap(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "okx_swap_feed.log")
        self._params = OkxExchangeDataSwap()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()


class OkxAccountWssDataSwap(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxMarketWssDataSwap(OkxMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxKlineWssDataSwap(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxWssDataSwap(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
