from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataOption
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataOption(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self.logger_name = kwargs.get("logger_name", "binance_option_feed.log")
        self._params = BinanceExchangeDataOption()
        self.request_logger = get_logger("binance_option_feed")
        self.async_logger = get_logger("binance_option_feed")


class BinanceMarketWssDataOption(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "OPTION")
        self._params = BinanceExchangeDataOption()


class BinanceAccountWssDataOption(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataOption()
