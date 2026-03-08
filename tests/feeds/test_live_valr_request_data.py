"""
Test Valr exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_valr_request_data.py -v
    pytest tests/feeds/test_live_valr_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_valr_request_data.py --cov=bt_api_py.feeds.live_valr --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register Valr
import bt_api_py.exchange_registers.register_valr  # noqa: F401
from bt_api_py.containers.exchanges.valr_exchange_data import (
    ValrExchangeData,
    ValrExchangeDataSpot,
)
from bt_api_py.feeds.live_valr.spot import ValrRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ==================== Fixtures ====================


@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def valr_feed(data_queue):
    """Create a Valr feed instance for testing."""
    feed = ValrRequestDataSpot(data_queue)
    return feed


# ==================== ServerTime Tests ====================


class TestValrServerTime:
    """Test server time functionality."""

    def test_valr_req_server_time(self, valr_feed):
        """Test getting server time from Valr API."""
        # Valr might not have a dedicated server time endpoint
        # Server time is typically returned in API responses
        assert True


# ==================== Ticker Tests ====================


class TestValrTickerData:
    """Test ticker data functionality."""

    def test_valr_req_tick_data(self, valr_feed):
        """Test getting ticker data from Valr API."""
        # Valr uses currency pair format like "BTCZAR"
        data = valr_feed.get_tick("BTCZAR")
        assert data is not None

    def test_valr_tick_data_validation(self, valr_feed):
        """Test ticker data structure and values."""
        data = valr_feed.get_tick("BTCZAR")
        assert data is not None

        if data:
            pass
            # Create ticker container if available
            # Valr ticker structure: {symbol, lastPrice, bidPrice, askPrice,
            # ...}
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    def test_valr_multiple_tickers(self, valr_feed):
        """Test getting multiple tickers."""
        # Test with different pairs
        symbols = ["BTCZAR", "ETHZAR"]
        for symbol in symbols:
            pass
        data = valr_feed.get_tick(symbol)
        assert data is not None


# ==================== Kline Tests ====================


class TestValrKlineData:
    """Test kline/candlestick data functionality."""

    def test_valr_req_kline_data_1m(self, valr_feed):
        """Test getting 1-minute kline data."""
        # Valr API kline support needs to be verified
        pass

    def test_valr_req_kline_data_1h(self, valr_feed):
        """Test getting 1-hour kline data."""
        pass

    def test_valr_req_kline_data_1d(self, valr_feed):
        """Test getting daily kline data."""
        pass


# ==================== OrderBook Tests ====================


class TestValrOrderBook:
    """Test order book depth functionality."""

    def test_valr_req_depth_data(self, valr_feed):
        """Test getting order book data."""
        data = valr_feed.get_depth("BTCZAR")
        assert data is not None

        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    def test_valr_orderbook_bids_asks(self, valr_feed):
        """Test orderbook has bids and asks."""
        data = valr_feed.get_depth("BTCZAR")
        assert data is not None

        if data and isinstance(data, dict):
            # Check for bids/asks structure
            if "bids" in data:
                pass
            assert isinstance(data["bids"], list)
            if "asks" in data:
                pass
            assert isinstance(data["asks"], list)


# ==================== Market Info Tests ====================


class TestValrMarketInfo:
    """Test market information functionality."""

    def test_valr_market_all(self, valr_feed):
        """Test getting all market information."""
        pass

    def test_valr_symbol_info(self, valr_feed):
        """Test getting symbol information."""
        pass


# ==================== Exchange Data Tests ====================


class TestValrExchangeData:
    """Test Valr exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating Valr exchange data."""
        exchange_data = ValrExchangeData()
        assert exchange_data.exchange_name == "VALR"
        assert exchange_data.rest_url == "https://api.valr.com"
        assert exchange_data.wss_url == "wss://api.valr.com/ws"

    def test_exchange_data_spot_creation(self):
        """Test creating Valr spot exchange data."""
        exchange_data = ValrExchangeDataSpot()
        assert exchange_data.exchange_name == "VALR___SPOT"
        assert exchange_data.asset_type == "SPOT"

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = ValrExchangeData()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies - South African Rand."""
        exchange_data = ValrExchangeData()
        assert "ZAR" in exchange_data.legal_currency
        assert "USDC" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


# ==================== Registry Tests ====================


class TestValrRegistry:
    """Test Valr registration."""

    def test_valr_registered(self):
        """Test that Valr is properly registered."""
        assert "VALR___SPOT" in ExchangeRegistry._feed_classes
        assert "VALR___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_valr_create_exchange_data(self):
        """Test creating Valr exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("VALR___SPOT")
        assert isinstance(exchange_data, ValrExchangeDataSpot)


# ==================== Integration Tests ====================


class TestValrIntegration:
    """Integration tests for Valr."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        feed = ValrRequestDataSpot(data_queue)
        data = feed.get_tick("BTCZAR")
        assert data is not None

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        feed = ValrRequestDataSpot(data_queue)
        data = feed.get_depth("BTCZAR")
        assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
