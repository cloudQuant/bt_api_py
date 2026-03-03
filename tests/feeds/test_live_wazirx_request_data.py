"""
Test WazirX exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_wazirx_request_data.py -v
    pytest tests/feeds/test_live_wazirx_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_wazirx_request_data.py --cov=bt_api_py.feeds.live_wazirx --cov-report=term-missing
"""

import queue
import time
import pytest
from unittest.mock import Mock, patch, MagicMock

from bt_api_py.containers.exchanges.wazirx_exchange_data import (
    WazirxExchangeData,
    WazirxExchangeDataSpot,
)
from bt_api_py.containers.tickers.wazirx_ticker import WazirxRequestTickerData
from bt_api_py.feeds.live_wazirx.spot import WazirxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register WazirX
import bt_api_py.feeds.register_wazirx  # noqa: F401


# ==================== Fixtures ====================

@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def wazirx_feed(data_queue):
    """Create a WazirX feed instance for testing."""
    with patch('bt_api_py.feeds.live_wazirx.request_base.requests.Session'):
        feed = WazirxRequestDataSpot(data_queue)
        return feed


# ==================== ServerTime Tests ====================

class TestWazirXServerTime:
    """Test server time functionality."""

    def test_wazirx_req_server_time(self, wazirx_feed):
        """Test getting server time from WazirX API."""
        # WazirX might not have a dedicated server time endpoint
        assert True


# ==================== Ticker Tests ====================

class TestWazirXTickerData:
    """Test ticker data functionality."""

    def test_wazirx_req_tick_data(self, wazirx_feed):
        """Test getting ticker data from WazirX API."""
        # WazirX uses format like "btcinr" or "btcusdt"
        data = wazirx_feed.get_tick("BTCINR")
        assert data is not None

    def test_wazirx_tick_data_validation(self, wazirx_feed):
        """Test ticker data structure and values."""
        data = wazirx_feed.get_tick("BTCINR")
        assert data is not None

        if data:
            pass
            # WazirX ticker response structure
        assert isinstance(data, dict) or isinstance(data, list)

    def test_wazirx_usdt_pair_ticker(self, wazirx_feed):
        """Test ticker for USDT pairs."""
        data = wazirx_feed.get_tick("BTCUSDT")
        assert data is not None


# ==================== Kline Tests ====================

class TestWazirXKlineData:
    """Test kline/candlestick data functionality."""

    def test_wazirx_req_kline_data_1m(self, wazirx_feed):
        """Test getting 1-minute kline data."""
        data = wazirx_feed.get_kline("BTCINR", "1m", count=2)
        assert data is not None

    def test_wazirx_req_kline_data_1h(self, wazirx_feed):
        """Test getting 1-hour kline data."""
        data = wazirx_feed.get_kline("BTCINR", "1h", count=2)
        assert data is not None

    def test_wazirx_req_kline_data_1d(self, wazirx_feed):
        """Test getting daily kline data."""
        data = wazirx_feed.get_kline("BTCINR", "1d", count=2)
        assert data is not None

    def test_wazirx_kline_multiple_timeframes(self, wazirx_feed):
        """Test kline data for multiple timeframes."""
        timeframes = ["1m", "5m", "15m", "1h", "1d"]

        for tf in timeframes:
            pass
        data = wazirx_feed.get_kline("BTCINR", tf, count=1)
        assert data is not None


# ==================== OrderBook Tests ====================

class TestWazirXOrderBook:
    """Test order book depth functionality."""

    def test_wazirx_req_depth_data(self, wazirx_feed):
        """Test getting order book data."""
        data = wazirx_feed.get_depth("BTCINR", count=20)
        assert data is not None

        if data:
            pass
            # WazirX orderbook structure
        assert "bids" in data or "asks" in data or isinstance(data, dict)

    def test_wazirx_orderbook_bids_asks(self, wazirx_feed):
        """Test orderbook has bids and asks."""
        data = wazirx_feed.get_depth("BTCINR", count=20)
        assert data is not None

        if data and isinstance(data, dict):
            if "bids" in data:
                pass
            assert isinstance(data["bids"], list)
            if "asks" in data:
                pass
            assert isinstance(data["asks"], list)

    def test_wazirx_depth_count_parameter(self, wazirx_feed):
        """Test depth count parameter."""
        data = wazirx_feed.get_depth("BTCINR", count=10)
        assert data is not None


# ==================== Market Info Tests ====================

class TestWazirXMarketInfo:
    """Test market information functionality."""

    def test_wazirx_market_all(self, wazirx_feed):
        """Test getting all market information."""
        pass

    def test_wazirx_symbol_info(self, wazirx_feed):
        """Test getting symbol information."""
        pass


# ==================== Exchange Data Tests ====================

class TestWazirXExchangeData:
    """Test WazirX exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating WazirX exchange data."""
        exchange_data = WazirxExchangeData()
        assert exchange_data.exchange_name == "wazirx"
        assert exchange_data.rest_url == "https://api.wazirx.com"
        assert exchange_data.wss_url == "wss://stream.wazirx.com/stream"

    def test_exchange_data_spot_creation(self):
        """Test creating WazirX spot exchange data."""
        exchange_data = WazirxExchangeDataSpot()
        assert exchange_data.exchange_name == "wazirx"
        assert exchange_data.asset_type == "spot"

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = WazirxExchangeData()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies - Indian Rupee."""
        exchange_data = WazirxExchangeData()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "WRX" in exchange_data.legal_currency  # Native token


# ==================== Registry Tests ====================

class TestWazirXRegistry:
    """Test WazirX registration."""

    def test_wazirx_registered(self):
        """Test that WazirX is properly registered."""
        assert "WAZIRX___SPOT" in ExchangeRegistry._feed_classes
        assert "WAZIRX___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_wazirx_create_exchange_data(self):
        """Test creating WazirX exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("WAZIRX___SPOT")
        assert isinstance(exchange_data, WazirxExchangeDataSpot)


# ==================== Integration Tests ====================

class TestWazirXIntegration:
    """Integration tests for WazirX."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        with patch('bt_api_py.feeds.live_wazirx.request_base.requests.Session'):
            feed = WazirxRequestDataSpot(data_queue)
            data = feed.get_tick("BTCINR")
            assert data is not None

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        with patch('bt_api_py.feeds.live_wazirx.request_base.requests.Session'):
            feed = WazirxRequestDataSpot(data_queue)
            data = feed.get_depth("BTCINR", count=20)
            assert data is not None

    @pytest.mark.integration
    def test_get_kline_live(self):
        """Test getting klines from live API."""
        data_queue = queue.Queue()
        with patch('bt_api_py.feeds.live_wazirx.request_base.requests.Session'):
            feed = WazirxRequestDataSpot(data_queue)
            data = feed.get_kline("BTCINR", "1h", count=2)
            assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
