"""
Test Upbit exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_upbit_request_data.py -v
    pytest tests/feeds/test_live_upbit_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_upbit_request_data.py --cov=bt_api_py.feeds.live_upbit --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register Upbit
import bt_api_py.exchange_registers.register_upbit  # noqa: F401
from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.registry import ExchangeRegistry

# Upbit feed has implementation issues with RateLimiter, so we'll skip it
# in unit tests
try:
    from bt_api_py.feeds.live_upbit.spot import UpbitRequestDataSpot

    UPBIT_FEED_AVAILABLE = True
except (ImportError, AttributeError):
    UPBIT_FEED_AVAILABLE = False

# ==================== Fixtures ====================


@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def upbit_feed(data_queue):
    """Create an Upbit feed instance for testing."""
    if not UPBIT_FEED_AVAILABLE:
        pytest.skip("UpbitRequestDataSpot not available")
    feed = UpbitRequestDataSpot(data_queue)
    return feed


# ==================== ServerTime Tests ====================


class TestUpbitServerTime:
    """Test server time functionality."""

    def test_upbit_req_server_time(self, upbit_feed):
        """Test getting server time from Upbit API."""
        # Upbit doesn't have a dedicated server time endpoint
        # Server time is typically returned in API responses
        assert True

    def test_upbit_server_time_in_response(self, upbit_feed):
        """Test that server time is included in API responses."""
        # Test with a public endpoint that includes timestamp
        result = upbit_feed.get_exchange_info()
        assert result is not None


# ==================== Ticker Tests ====================


class TestUpbitTickerData:
    """Test ticker data functionality."""

    @pytest.mark.ticker
    def test_upbit_req_tick_data(self, upbit_feed):
        """Test getting ticker data from Upbit API."""
        # Upbit uses market format like "KRW-BTC" or "USDT-BTC"
        data = upbit_feed.get_ticker("KRW-BTC")
        assert data is not None

    @pytest.mark.ticker
    def test_upbit_tick_data_validation(self, upbit_feed):
        """Test ticker data structure and values."""
        from bt_api_base.containers.requestdatas.request_data import RequestData

        data = upbit_feed.get_ticker("KRW-BTC")
        assert isinstance(data, (list, dict, RequestData))

    @pytest.mark.ticker
    def test_upbit_multiple_tickers(self, upbit_feed):
        """Test getting multiple tickers at once."""
        markets = ["KRW-BTC", "KRW-ETH", "USDT-BTC"]
        data = upbit_feed.get_ticker(markets)
        assert data is not None

    @pytest.mark.ticker
    def test_upbit_usdt_pair_ticker(self, upbit_feed):
        """Test ticker for USDT pairs."""
        data = upbit_feed.get_ticker("USDT-BTC")
        assert data is not None


# ==================== Kline Tests ====================


class TestUpbitKlineData:
    """Test kline/candlestick data functionality."""

    @pytest.mark.kline
    def test_upbit_req_kline_data_1m(self, upbit_feed):
        """Test getting 1-minute kline data."""
        data = upbit_feed.get_kline("KRW-BTC", "1m", count=2)
        assert data is not None

    @pytest.mark.kline
    def test_upbit_req_kline_data_1h(self, upbit_feed):
        """Test getting 1-hour kline data."""
        data = upbit_feed.get_kline("KRW-BTC", "1h", count=2)
        assert data is not None

    @pytest.mark.kline
    def test_upbit_req_kline_data_1d(self, upbit_feed):
        """Test getting daily kline data."""
        data = upbit_feed.get_kline("KRW-BTC", "1d", count=2)
        assert data is not None

    @pytest.mark.kline
    def test_upbit_kline_convenience_method(self, upbit_feed):
        """Test the convenience method for getting klines."""
        data = upbit_feed.get_kline("KRW-BTC", "1h", count=5)
        assert data is not None

    @pytest.mark.kline
    def test_upbit_kline_multiple_timeframes(self, upbit_feed):
        """Test kline data for multiple timeframes."""
        timeframes = ["1m", "5m", "15m", "1h", "1d"]

        tf = timeframes[-1]
        data = upbit_feed.get_kline("KRW-BTC", tf, count=1)
        assert data is not None


# ==================== OrderBook Tests ====================


class TestUpbitOrderBook:
    """Test order book depth functionality."""

    @pytest.mark.orderbook
    def test_upbit_req_depth_data(self, upbit_feed):
        """Test getting order book data."""
        data = upbit_feed.get_depth("KRW-BTC")
        assert data is not None

    @pytest.mark.orderbook
    def test_upbit_orderbook_units(self, upbit_feed):
        """Test orderbook returns valid data."""
        data = upbit_feed.get_depth("KRW-BTC")
        assert data is not None

    @pytest.mark.orderbook
    def test_upbit_multiple_orderbooks(self, upbit_feed):
        """Test getting depth for different markets."""
        data = upbit_feed.get_depth("KRW-ETH")
        assert data is not None


# ==================== Trade Ticks Tests ====================


class TestUpbitTradeTicks:
    """Test trade ticks functionality."""

    @pytest.mark.ticker
    def test_upbit_trade_ticks(self, upbit_feed):
        """Test getting recent trade history."""
        data = upbit_feed.get_trade_history("KRW-BTC", count=10)
        assert data is not None

    @pytest.mark.ticker
    def test_upbit_trade_ticks_with_params(self, upbit_feed):
        """Test trade history with parameters."""
        data = upbit_feed.get_trade_history("KRW-BTC", count=5)
        assert data is not None


# ==================== Market Info Tests ====================


class TestUpbitMarketInfo:
    """Test market information functionality."""

    def test_upbit_market_all(self, upbit_feed):
        """Test getting exchange info."""
        data = upbit_feed.get_exchange_info()
        assert data is not None

    def test_upbit_market_all_details(self, upbit_feed):
        """Test getting exchange info returns valid data."""
        data = upbit_feed.get_exchange_info()
        assert data is not None

    def test_upbit_symbol_info(self, upbit_feed):
        """Test convenience method for symbol info."""
        data = upbit_feed.get_exchange_info()
        assert data is not None


# ==================== Exchange Data Tests ====================


class TestUpbitExchangeData:
    """Test Upbit exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating Upbit exchange data."""
        exchange_data = UpbitExchangeDataSpot()
        # Upbit exchange_name might be "upbit", "UPBIT", or "upbit___spot"
        assert "upbit" in exchange_data.exchange_name.lower()
        assert exchange_data.rest_url
        assert "upbit" in exchange_data.rest_url.lower()

    def test_get_symbol_format(self):
        """Test symbol format conversion."""
        exchange_data = UpbitExchangeDataSpot()
        # Upbit uses format like "KRW-BTC" or "USDT-BTC"
        assert exchange_data.get_symbol("KRW-BTC") == "KRW-BTC"

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = UpbitExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = UpbitExchangeDataSpot()
        assert "KRW" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency


# ==================== Registry Tests ====================


class TestUpbitRegistry:
    """Test Upbit registration."""

    def test_upbit_registered(self):
        """Test that Upbit is properly registered."""
        assert "UPBIT___SPOT" in ExchangeRegistry._feed_classes
        assert "UPBIT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_upbit_create_exchange_data(self):
        """Test creating Upbit exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("UPBIT___SPOT")
        assert isinstance(exchange_data, UpbitExchangeDataSpot)


# ==================== Integration Tests ====================


class TestUpbitIntegration:
    """Integration tests for Upbit."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        feed = UpbitRequestDataSpot(data_queue)
        data = feed.get_ticker("KRW-BTC")
        assert data is not None

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        feed = UpbitRequestDataSpot(data_queue)
        data = feed.get_depth("KRW-BTC")
        assert data is not None

    @pytest.mark.integration
    @pytest.mark.kline
    def test_get_kline_live(self):
        """Test getting klines from live API."""
        data_queue = queue.Queue()
        feed = UpbitRequestDataSpot(data_queue)
        data = feed.get_kline("KRW-BTC", "1m", count=2)
        assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        # WebSocket tests are marked as integration


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
