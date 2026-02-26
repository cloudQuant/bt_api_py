# -*- coding: utf-8 -*-
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataAlgo
from bt_api_py.functions.log_message import SpdLogManager


class BinanceRequestDataAlgo(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataAlgo, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "ALGO")
        self.logger_name = kwargs.get("logger_name", "binance_algo_feed.log")
        self._params = BinanceExchangeDataAlgo()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()
