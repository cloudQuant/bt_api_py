"""
Register Gemini exchange feeds
"""

from bt_api_py.feeds.live_gemini.spot import GeminiRequestDataSpot
from bt_api_py.logging_factory import get_logger
from bt_api_py.registry import ExchangeRegistry


class GeminiSpotFeed(GeminiRequestDataSpot):
    """Gemini Spot Exchange Feed"""

    @classmethod
    def _capabilities(cls):
        return {
            "GET_TICK",
            "GET_DEPTH",
            "GET_KLINE",
            "GET_TRADE",
            "GET_BALANCE",
            "GET_ACCOUNT",
            "MAKE_ORDER",
            "CANCEL_ORDER",
            "QUERY_ORDER",
            "GET_OPEN_ORDERS",
            "GET_ORDER_HISTORY",
            "MARKET_STREAM",
            "ACCOUNT_STREAM",
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = "GEMINI___SPOT"

        # Set up symbol mapping if needed
        self._params.symbol_dict = kwargs.get("symbol_dict", {})

        # Set up logger
        self.logger_name = kwargs.get("logger_name", "gemini_spot_feed.log")
        self.request_logger = get_logger("gemini_spot_feed")
        self.async_logger = get_logger("gemini_spot_feed")


# Create WebSocket classes for future implementation
class GeminiMarketWssData:
    """Gemini Market WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.symbol = kwargs.get("symbol")

    def subscribe_ticker(self, symbol):
        """Subscribe to ticker updates"""
        pass

    def subscribe_depth(self, symbol, limit_bids=50, limit_asks=50):
        """Subscribe to order book updates"""
        pass

    def subscribe_trades(self, symbol):
        """Subscribe to trade updates"""
        pass

    def subscribe_kline(self, symbol, time_frame):
        """Subscribe to kline updates"""
        pass


class GeminiAccountWssData:
    """Gemini Account WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.asset_type = kwargs.get("asset_type", "SPOT")

    def subscribe_orders(self):
        """Subscribe to order updates"""
        pass

    def subscribe_balances(self):
        """Subscribe to balance updates"""
        pass

    def subscribe_positions(self):
        """Subscribe to position updates"""
        pass


class GeminiWssData:
    """Gemini WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.asset_type = kwargs.get("asset_type", "SPOT")

    def subscribe_market_data(self, symbol, channels):
        """Subscribe to market data channels"""
        pass

    def subscribe_account_data(self):
        """Subscribe to account data channels"""
        pass


def register_gemini() -> None:
    """Register Gemini feeds into the unified exchange registry."""
    ExchangeRegistry.register_feed("GEMINI___SPOT", GeminiSpotFeed)


register_gemini()
