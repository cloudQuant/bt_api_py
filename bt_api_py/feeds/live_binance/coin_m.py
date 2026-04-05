from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataCoinM
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataCoinM(BinanceRequestData):
    """Binance COIN-M futures request data handler."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize Binance COIN-M request data handler.

        Args:
            data_queue: Queue for storing data.
            **kwargs: Additional keyword arguments including:
                - asset_type: Asset type (default: "COIN_M")
                - logger_name: Logger name (default: "binance_coin_m_feed.log")

        """
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_M")
        self.logger_name = kwargs.get("logger_name", "binance_coin_m_feed.log")
        self._params = BinanceExchangeDataCoinM()
        self.request_logger = get_logger("binance_coin_m_feed")
        self.async_logger = get_logger("binance_coin_m_feed")


class BinanceMarketWssDataCoinM(BinanceMarketWssData):
    """Binance COIN-M futures market WebSocket data handler."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize Binance COIN-M market WebSocket data handler.

        Args:
            data_queue: Queue for storing data.
            **kwargs: Additional keyword arguments including:
                - asset_type: Asset type (default: "COIN_M")

        """
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_M")
        self._params = BinanceExchangeDataCoinM()


class BinanceAccountWssDataCoinM(BinanceAccountWssData):
    """Binance COIN-M futures account WebSocket data handler."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize Binance COIN-M account WebSocket data handler.

        Args:
            data_queue: Queue for storing data.
            **kwargs: Additional keyword arguments.

        """
        super().__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataCoinM()
