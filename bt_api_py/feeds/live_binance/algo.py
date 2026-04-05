from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataAlgo
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataAlgo(BinanceRequestData):
    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "ALGO")
        self.logger_name = kwargs.get("logger_name", "binance_algo_feed.log")
        self._params = BinanceExchangeDataAlgo()
        self.request_logger = get_logger("binance_algo_feed")
        self.async_logger = get_logger("binance_algo_feed")
