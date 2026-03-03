"""
Crypto.com Spot trading feed implementation.
"""

from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.feeds.live_cryptocom.request_base import CryptoComRequestData


class CryptoComRequestDataSpot(CryptoComRequestData):
    """Crypto.com Spot trading feed."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = "SPOT"

    def initialize_feed(self):
        """Initialize the feed with exchange-specific settings."""
        self._params = CryptoComExchangeDataSpot()
        self.exchange_name = "CRYPTOCOM___SPOT"

        # Set feed-specific parameters
        self.rest_url = self._params.rest_url
        self.wss_url = self._params.wss_url
        self.acct_wss_url = self._params.acct_wss_url

    @classmethod
    def _capabilities(cls):
        return {
            "GET_TICK",
            "GET_DEPTH",
            "GET_KLINE",
            "GET_TRADE",
            "MAKE_ORDER",
            "CANCEL_ORDER",
            "QUERY_ORDER",
            "QUERY_OPEN_ORDERS",
            "GET_BALANCE",
            "GET_ACCOUNT",
            "GET_EXCHANGE_INFO",
            "GET_SERVER_TIME",
        }