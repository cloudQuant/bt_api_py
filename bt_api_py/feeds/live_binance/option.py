# -*- coding: utf-8 -*-
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataOption
from bt_api_py.functions.log_message import SpdLogManager


class BinanceRequestDataOption(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataOption, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self.logger_name = kwargs.get("logger_name", "binance_option_feed.log")
        self._params = BinanceExchangeDataOption()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()


class BinanceMarketWssDataOption(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataOption, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self._params = BinanceExchangeDataOption()


class BinanceAccountWssDataOption(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceAccountWssDataOption, self).__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataOption()
