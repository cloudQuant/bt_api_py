# -*- coding: utf-8 -*-
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataMargin
from bt_api_py.functions.log_message import SpdLogManager


class BinanceRequestDataMargin(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataMargin, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MARGIN")
        self.logger_name = kwargs.get("logger_name", "binance_margin_feed.log")
        self._params = BinanceExchangeDataMargin()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()


class BinanceMarketWssDataMargin(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataMargin, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MARGIN")
        self._params = BinanceExchangeDataMargin()


class BinanceAccountWssDataMargin(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceAccountWssDataMargin, self).__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataMargin()
