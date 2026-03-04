from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataFutures
from bt_api_py.feeds.live_okx.account_wss_base import (
    OkxAccountWssData,
    OkxKlineWssData,
    OkxMarketWssData,
)
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData
from bt_api_py.feeds.live_okx.request_base import OkxRequestData
from bt_api_py.logging_factory import get_logger


class OkxRequestDataFutures(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")
        self.logger_name = kwargs.get("logger_name", "okx_futures_feed.log")
        self._params = OkxExchangeDataFutures()
        self.request_logger = get_logger("okx_futures_feed")
        self.async_logger = get_logger("okx_futures_feed")


class OkxAccountWssDataFutures(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxMarketWssDataFutures(OkxMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxKlineWssDataFutures(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxWssDataFutures(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")
