# -*- coding: utf-8 -*-
from bt_api_py.feeds.live_okx.request_base import OkxRequestData
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData
from bt_api_py.feeds.live_okx.account_wss_base import (
    OkxAccountWssData, OkxMarketWssData, OkxKlineWssData
)
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataFutures
from bt_api_py.functions.log_message import SpdLogManager


class OkxRequestDataFutures(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super(OkxRequestDataFutures, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")
        self.logger_name = kwargs.get("logger_name", "okx_futures_feed.log")
        self._params = OkxExchangeDataFutures()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()


class OkxAccountWssDataFutures(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxAccountWssDataFutures, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxMarketWssDataFutures(OkxMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxMarketWssDataFutures, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxKlineWssDataFutures(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxKlineWssDataFutures, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")


class OkxWssDataFutures(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxWssDataFutures, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURES")
