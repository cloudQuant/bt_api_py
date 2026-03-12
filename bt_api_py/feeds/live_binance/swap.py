from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataSwap(BinanceRequestData):
    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "binance_swap_feed.log")
        self._params = BinanceExchangeDataSwap()
        self.request_logger = get_logger("binance_swap_feed")
        self.async_logger = get_logger("binance_swap_feed")


class BinanceMarketWssDataSwap(BinanceMarketWssData):
    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self._params = BinanceExchangeDataSwap()


class BinanceAccountWssDataSwap(BinanceAccountWssData):
    pass
