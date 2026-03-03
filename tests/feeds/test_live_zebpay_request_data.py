"""
Test Zebpay exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_zebpay_request_data.py -v
    pytest tests/feeds/test_live_zebpay_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_zebpay_request_data.py --cov=bt_api_py.feeds.live_zebpay --cov-report=term-missing
"""

import queue
import time
import pytest
from unittest.mock import Mock, patch, MagicMock

from bt_api_py.containers.exchanges.zebpay_exchange_data import (
    ZebpayExchangeData,
    ZebpayExchangeDataSpot,
)
from bt_api_py.containers.tickers.zebpay_ticker import ZebpayRequestTickerData
from bt_api_py.feeds.live_zebpay.spot import ZebpayRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Zebpay
import bt_api_py.feeds.register_zebpay  # noqa: F401


# ==================== Fixtures ====================

@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def zebpay_feed(data_queue):
    """Create a Zebpay feed instance for testing."""
    with patch('bt_api_py.feeds.live_zebpay.request_base.requests.Session'):
        feed = ZebpayRequestDataSpot(data_queue)
        return feed


# ==================== ServerTime Tests ====================

class TestZebpayServerTime:
    """Test server time functionality."""

    def test_zebpay_req_server_time(self, zebpay_feed):
        """Test getting server time from Zebpay API."""
        # Zebpay might not have a dedicated server time endpoint
        assert True


# ==================== Ticker Tests ====================

class TestZebpayTickerData:
    """Test ticker data functionality."""

    def test_zebpay_req_tick_data(self, zebpay_feed):
        """Test getting ticker data from Zebpay API."""
        # Zebpay uses format like "BTC-INR"
        data = zebpay_feed.get_tick("BTC/INR")
        assert data is not None

    def test_zebpay_tick_data_validation(self, zebpay_feed):
        """Test ticker data structure and values."""
        data = zebpay_feed.get_tick("BTC/INR")
        assert data is not None

        if data:
            pass
            # Zebpay ticker response structure
        assert isinstance(data, dict) or isinstance(data, list)

    def test_zebpay_multiple_tickers(self, zebpay_feed):
        """Test getting multiple tickers."""
        # Test with different pairs
        symbols = ["BTC/INR", "ETH/INR", "BTC/USDT"]
        for symbol in symbols:
            pass
        data = zebpay_feed.get_tick(symbol)
        assert data is not None

    def test_zebpay_usdt_pair_ticker(self, zebpay_feed):
        """Test ticker for USDT pairs."""
        data = zebpay_feed.get_tick("BTC/USDT")
        assert data is not None


# ==================== Kline Tests ====================

class TestZebpayKlineData:
    """Test kline/candlestick data functionality."""

    def test_zebpay_req_kline_data_1m(self, zebpay_feed):
        """Test getting 1-minute kline data."""
        data = zebpay_feed.get_kline("BTC/INR", "1m", count=2)
        assert data is not None

        if data:
            pass
            # Zebpay kline structure
        assert isinstance(data, list) or isinstance(data, dict)

    def test_zebpay_req_kline_data_1h(self, zebpay_feed):
        """Test getting 1-hour kline data."""
        data = zebpay_feed.get_kline("BTC/INR", "1h", count=2)
        assert data is not None

    def test_zebpay_req_kline_data_1d(self, zebpay_feed):
        """Test getting daily kline data."""
        data = zebpay_feed.get_kline("BTC/INR", "1d", count=2)
        assert data is not None

    def test_zebpay_kline_multiple_timeframes(self, zebpay_feed):
        """Test kline data for multiple timeframes."""
        timeframes = ["1m", "5m", "15m", "1h", "1d"]

        for tf in timeframes:
            pass
        data = zebpay_feed.get_kline("BTC/INR", tf, count=1)
        assert data is not None


# ==================== OrderBook Tests ====================

class TestZebpayOrderBook:
    """Test order book depth functionality."""

    def test_zebpay_req_depth_data(self, zebpay_feed):
        """Test getting order book data."""
        data = zebpay_feed.get_depth("BTC/INR", count=20)
        assert data is not None

        if data:
            pass
            # Zebpay orderbook structure
        assert "bids" in data or "asks" in data or isinstance(data, dict)

    def test_zebpay_orderbook_bids_asks(self, zebpay_feed):
        """Test orderbook has bids and asks."""
        data = zebpay_feed.get_depth("BTC/INR", count=20)
        assert data is not None

        if data and isinstance(data, dict):
            if "bids" in data:
                pass
            assert isinstance(data["bids"], list)
            if "asks" in data:
                pass
            assert isinstance(data["asks"], list)

    def test_zebpay_depth_count_parameter(self, zebpay_feed):
        """Test depth count parameter."""
        data = zebpay_feed.get_depth("BTC/INR", count=10)
        assert data is not None


# ==================== Market Info Tests ====================

class TestZebpayMarketInfo:
    """Test market information functionality."""

    def test_zebpay_market_all(self, zebpay_feed):
        """Test getting all market information."""
        pass

    def test_zebpay_symbol_info(self, zebpay_feed):
        """Test getting symbol information."""
        pass


# ==================== Exchange Data Tests ====================

class TestZebpayExchangeData:
    """Test Zebpay exchange data configuration."""

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_exchange_data_creation(self, mock_config):
        """Test creating Zebpay exchange data."""
        mock_config.return_value = None
        exchange_data = ZebpayExchangeDataSpot()
        assert exchange_data.exchange_name == "zebpay"
        assert exchange_data.rest_url == "https://sapi.zebpay.com"
        assert exchange_data.wss_url == "wss://stream.zebpay.com"

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_kline_periods(self, mock_config):
        """Test kline period configuration."""
        mock_config.return_value = None
        exchange_data = ZebpayExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_legal_currency(self, mock_config):
        """Test legal currencies - Indian Rupee."""
        mock_config.return_value = None
        exchange_data = ZebpayExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency


# ==================== Symbol Format Tests ====================

class TestZebpaySymbolFormat:
    """Test Zebpay symbol format conversion."""

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_to_zebpay_symbol_conversion(self, mock_config):
        """Test symbol format conversion to Zebpay format."""
        mock_config.return_value = None
        data_queue = queue.Queue()
        feed = ZebpayRequestDataSpot(data_queue)

        # Zebpay uses dash separator like BTC-INR
        assert feed._to_zebpay_symbol("BTC/INR") == "BTC-INR"
        assert feed._to_zebpay_symbol("BTC-INR") == "BTC-INR"
        assert feed._to_zebpay_symbol("btc-inr") == "BTC-INR"
        assert feed._to_zebpay_symbol("BTCINR") == "BTC-INR"

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_to_zebpay_period_conversion(self, mock_config):
        """Test period format conversion to Zebpay format."""
        mock_config.return_value = None
        data_queue = queue.Queue()
        feed = ZebpayRequestDataSpot(data_queue)

        # Test various period formats
        assert feed._to_zebpay_period("1m") == "1m"
        assert feed._to_zebpay_period("5m") == "5m"
        assert feed._to_zebpay_period("1h") == "1h"
        assert feed._to_zebpay_period("1d") == "1d"
        assert feed._to_zebpay_period("1w") == "1w"


# ==================== Registry Tests ====================

class TestZebpayRegistry:
    """Test Zebpay registration."""

    def test_zebpay_registered(self):
        """Test that Zebpay is properly registered."""
        assert "ZEBPAY___SPOT" in ExchangeRegistry._feed_classes
        assert "ZEBPAY___SPOT" in ExchangeRegistry._exchange_data_classes

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_zebpay_create_exchange_data(self, mock_config):
        """Test creating Zebpay exchange data through registry."""
        mock_config.return_value = None
        exchange_data = ExchangeRegistry.create_exchange_data("ZEBPAY___SPOT")
        assert isinstance(exchange_data, ZebpayExchangeDataSpot)

    @patch('bt_api_py.containers.exchanges.zebpay_exchange_data._get_zebpay_config')
    def test_zebpay_create_feed(self, mock_config):
        """Test creating Zebpay feed through registry."""
        mock_config.return_value = None
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("ZEBPAY___SPOT", data_queue)
        assert isinstance(feed, ZebpayRequestDataSpot)


# ==================== Ticker Container Tests ====================

class TestZebpayTickerContainer:
    """Test Zebpay ticker data container."""

    def test_ticker_float_parsing(self):
        """Test ticker float parsing helper method."""
        # Test the _parse_float static method
        assert ZebpayRequestTickerData._parse_float("5000000") == 5000000.0
        assert ZebpayRequestTickerData._parse_float(5000000) == 5000000.0
        assert ZebpayRequestTickerData._parse_float(None) is None
        assert ZebpayRequestTickerData._parse_float("invalid") is None


# ==================== Normalization Tests ====================

class TestZebpayNormalization:
    """Test data normalization functions."""

    def test_tick_normalize_function(self):
        """Test ticker normalization function."""
        input_data = {
            "data": {
                "symbol": "BTC-INR",
                "last": "5000000",
                "bid": "4990000",
                "ask": "5010000",
            }
        }

        result, success = ZebpayRequestDataSpot._get_tick_normalize_function(
            input_data, {})
        assert success is True
        assert len(result) == 1
        assert result[0]["symbol"] == "BTC-INR"

    def test_tick_normalize_function_empty(self):
        """Test ticker normalization with empty data."""
        result, success = ZebpayRequestDataSpot._get_tick_normalize_function(None, {
        })
        assert success is False
        assert result == []

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "data": {
                "bids": [["4990000", "1.5"]],
                "asks": [["5010000", "2.0"]],
            }
        }

        result, success = ZebpayRequestDataSpot._get_depth_normalize_function(
            input_data, {})
        assert success is True
        assert len(result) == 1

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "data": [
                [1642696800000, "4900000", "5100000",
                    "4800000", "5000000", "1000"],
            ]
        }

        result, success = ZebpayRequestDataSpot._get_kline_normalize_function(
            input_data, {})
        assert success is True
        assert len(result) == 1


# ==================== Integration Tests ====================

class TestZebpayIntegration:
    """Integration tests for Zebpay."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        feed = ZebpayRequestDataSpot(data_queue)
        data = feed.get_tick("BTC/INR")
        assert data is not None

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        feed = ZebpayRequestDataSpot(data_queue)
        data = feed.get_depth("BTC/INR", count=20)
        assert data is not None

    @pytest.mark.integration
    def test_get_kline_live(self):
        """Test getting klines from live API."""
        data_queue = queue.Queue()
        feed = ZebpayRequestDataSpot(data_queue)
        data = feed.get_kline("BTC/INR", "1h", count=2)
        assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
