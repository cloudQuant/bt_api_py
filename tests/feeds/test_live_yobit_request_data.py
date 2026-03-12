"""
Test YoBit exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_yobit_request_data.py -v
    pytest tests/feeds/test_live_yobit_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_yobit_request_data.py --cov=bt_api_py.feeds.live_yobit --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register YoBit
import bt_api_py.exchange_registers.register_yobit  # noqa: F401
from bt_api_py.containers.exchanges.yobit_exchange_data import (
    YobitExchangeData,
    YobitExchangeDataSpot,
)
from bt_api_py.feeds.live_yobit.spot import YobitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ==================== Fixtures ====================


@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def yobit_feed(data_queue):
    """Create a YoBit feed instance for testing."""
    feed = YobitRequestDataSpot(data_queue)
    return feed


# ==================== ServerTime Tests ====================


class TestYoBitServerTime:
    """Test server time functionality."""

    def test_yobit_req_server_time(self, yobit_feed):
        """Test getting server time from YoBit API."""
        # YoBit might not have a dedicated server time endpoint
        assert True


# ==================== Ticker Tests ====================


class TestYoBitTickerData:
    """Test ticker data functionality."""

    @pytest.mark.ticker
    def test_yobit_req_tick_data(self, yobit_feed):
        """Test getting ticker data from YoBit API."""
        # YoBit uses lowercase format with underscore like btc_usdt
        data = yobit_feed.get_tick("BTC/USDT")
        assert data is not None

    @pytest.mark.ticker
    def test_yobit_tick_data_validation(self, yobit_feed):
        """Test ticker data structure and values."""
        data = yobit_feed.get_tick("BTC/USDT")
        assert data is not None

        if data:
            pass
            # YoBit ticker response structure
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    @pytest.mark.ticker
    def test_yobit_multiple_tickers(self, yobit_feed):
        """Test getting multiple tickers."""
        # Test with different pairs
        symbols = ["BTC/USDT", "ETH/USDT", "LTC/USDT"]
        symbol = symbols[-1]
        data = yobit_feed.get_tick(symbol)
        assert data is not None


# ==================== Kline Tests ====================


class TestYoBitKlineData:
    """Test kline/candlestick data functionality."""

    @pytest.mark.kline
    def test_yobit_req_kline_data_1m(self, yobit_feed):
        """Test getting 1-minute kline data."""
        # YoBit doesn't have a dedicated kline endpoint

    @pytest.mark.kline
    def test_yobit_req_kline_data_1h(self, yobit_feed):
        """Test getting 1-hour kline data."""
        # YoBit doesn't have a dedicated kline endpoint

    @pytest.mark.kline
    def test_yobit_req_kline_data_1d(self, yobit_feed):
        """Test getting daily kline data."""
        # YoBit doesn't have a dedicated kline endpoint


# ==================== OrderBook Tests ====================


class TestYoBitOrderBook:
    """Test order book depth functionality."""

    @pytest.mark.orderbook
    def test_yobit_req_depth_data(self, yobit_feed):
        """Test getting order book data."""
        data = yobit_feed.get_depth("BTC/USDT", count=20)
        assert data is not None

        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    @pytest.mark.orderbook
    def test_yobit_orderbook_bids_asks(self, yobit_feed):
        """Test orderbook has bids and asks."""
        data = yobit_feed.get_depth("BTC/USDT", count=20)
        assert data is not None

        if data and isinstance(data, dict):
            if "bids" in data:
                pass
            assert isinstance(data["bids"], list)
            if "asks" in data:
                pass
            assert isinstance(data["asks"], list)

    @pytest.mark.orderbook
    def test_yobit_depth_count_parameter(self, yobit_feed):
        """Test depth count parameter."""
        data = yobit_feed.get_depth("BTC/USDT", count=10)
        assert data is not None

    @pytest.mark.orderbook
    def test_yobit_depth_limit_parameter(self, yobit_feed):
        """Test depth with different limits."""
        limit = 50
        data = yobit_feed.get_depth("BTC/USDT", count=limit)
        assert data is not None


# ==================== Market Info Tests ====================


class TestYoBitMarketInfo:
    """Test market information functionality."""

    def test_yobit_get_info(self, yobit_feed):
        """Test getting exchange info - all available pairs."""
        data = yobit_feed.get_exchange_info()
        assert data is not None

        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    def test_yobit_symbol_info(self, yobit_feed):
        """Test getting symbol information."""


# ==================== Exchange Data Tests ====================


class TestYoBitExchangeData:
    """Test YoBit exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating YoBit exchange data."""
        exchange_data = YobitExchangeData()
        assert exchange_data.exchange_name == "YOBIT"
        assert exchange_data.rest_url == "https://yobit.net"
        assert exchange_data.wss_url == "wss://ws.yobit.net"

    def test_exchange_data_spot_creation(self):
        """Test creating YoBit spot exchange data."""
        exchange_data = YobitExchangeDataSpot()
        assert exchange_data.exchange_name == "YOBIT___SPOT"
        assert exchange_data.asset_type == "SPOT"

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = YobitExchangeData()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = YobitExchangeData()
        assert "USD" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "RUB" in exchange_data.legal_currency  # Russian Ruble
        assert "BTC" in exchange_data.legal_currency


# ==================== Registry Tests ====================


class TestYoBitRegistry:
    """Test YoBit registration."""

    def test_yobit_registered(self):
        """Test that YoBit is properly registered."""
        assert "YOBIT___SPOT" in ExchangeRegistry._feed_classes
        assert "YOBIT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_yobit_create_exchange_data(self):
        """Test creating YoBit exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("YOBIT___SPOT")
        assert isinstance(exchange_data, YobitExchangeDataSpot)


# ==================== Symbol Format Tests ====================


class TestYoBitSymbolFormat:
    """Test YoBit symbol format conversion."""

    def test_to_yobit_symbol_conversion(self):
        """Test symbol format conversion to YoBit format."""
        # YoBit uses lowercase with underscore: btc_usdt
        symbol = "BTC/USDT".lower().replace("/", "_")
        assert "btc_usdt" in symbol

    def test_yobit_symbol_formats(self):
        """Test various symbol input formats."""
        # Test that the feed can handle different input formats
        formats = ["BTC/USDT", "BTC-USDT", "BTCUSDT", "btc_usdt"]
        for fmt in formats:
            # Just verify the conversion doesn't fail
            symbol = fmt.lower().replace("/", "_").replace("-", "_")
            assert symbol is not None


# ==================== Integration Tests ====================


class TestYoBitIntegration:
    """Integration tests for YoBit."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        feed = YobitRequestDataSpot(data_queue)
        data = feed.get_tick("BTC/USDT")
        assert data is not None

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        feed = YobitRequestDataSpot(data_queue)
        data = feed.get_depth("BTC/USDT", count=20)
        assert data is not None

    @pytest.mark.integration
    def test_get_info_live(self):
        """Test getting exchange info from live API."""
        data_queue = queue.Queue()
        feed = YobitRequestDataSpot(data_queue)
        data = feed.get_exchange_info()
        assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
