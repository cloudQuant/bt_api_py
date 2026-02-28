from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataCoinM
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.log_message import SpdLogManager


class BinanceRequestDataCoinM(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_M")
        self.logger_name = kwargs.get("logger_name", "binance_coin_m_feed.log")
        self._params = BinanceExchangeDataCoinM()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()


class BinanceMarketWssDataCoinM(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_M")
        self._params = BinanceExchangeDataCoinM()


class BinanceAccountWssDataCoinM(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataCoinM()
