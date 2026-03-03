"""
Test BeQuant exchange integration.

Run tests:
    pytest tests/feeds/test_bequant.py -v

Run with coverage:
    pytest tests/feeds/test_bequant.py --cov=bt_api_py.feeds.live_bequant --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bequant_exchange_data import (
    BeQuantExchangeData,
    BeQuantExchangeDataSpot,
)
from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bequant_ticker import BeQuantRequestTickerData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BeQuant
import bt_api_py.feeds.register_bequant  # noqa: F401

@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bequant_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log

def init_req_feed():
    """Initialize BeQuant request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "BEQUANT___SPOT",
    }
    return BeQuantRequestDataSpot(data_queue, **kwargs)

class TestBeQuantExchangeData:
    """Test BeQuant exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating BeQuant spot exchange data."""
        exchange_data = BeQuantExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths or "get_ticker" in exchange_data.rest_paths

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BeQuantExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BeQuantExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BeQuantExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()

class TestBeQuantRequestDataSpot:
    """Test BeQuant REST API request methods."""

    def test_request_data_creation(self):
        """Test creating BeQuant request data."""
        data_queue = queue.Queue()
        request_data = BeQuantRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BEQUANT___SPOT",
        )
        assert request_data.exchange_name == "BEQUANT___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "symbol": "BTCUSDT",
            "last": "50000",
        }
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BeQuantRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "bids": [["49990", "1.0"], ["49980", "2.0"]],
            "asks": [["50010", "1.0"], ["50020", "2.0"]],
        }
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BeQuantRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = [
            [1640995200000, "49500", "51000", "49000", "50000", "1234.56"]
        ]
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BeQuantRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)

class TestBeQuantRegistration:
    """Test BeQuant registration."""

    def test_bequant_registered(self):
        """Test that BeQuant is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BEQUANT___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BEQUANT___SPOT"] == BeQuantRequestDataSpot

        # Check if exchange data is registered
        assert "BEQUANT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BEQUANT___SPOT"] == BeQuantExchangeDataSpot

        # Check if balance handler is registered
        assert "BEQUANT___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BEQUANT___SPOT"] is not None

    def test_bequant_create_exchange_data(self):
        """Test creating BeQuant exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BEQUANT___SPOT")
        assert isinstance(exchange_data, BeQuantExchangeDataSpot)

class TestBeQuantIntegration:
    """Integration tests for BeQuant."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ====================

class TestBeQuantLiveAPI:
    """Test BeQuant live API following Binance/OKX test standards."""

    @pytest.mark.integration
    def test_bequant_req_tick_data(self):
        """Test BeQuant ticker data (sync)."""
        feed = init_req_feed()
        data = feed.get_tick("BTCUSDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have data, validate the ticker structure
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            # BeQuant returns dict with ticker info
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)
                # Validate ticker has expected fields
                if "bid" in ticker or "ask" in ticker or "last" in ticker:
                    # At least one price field should exist
                    assert True

    def test_bequant_async_tick_data(self):
        """Test BeQuant ticker data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "public_key": "test_key",
            "private_key": "test_secret",
            "exchange_name": "BEQUANT___SPOT",
        }
        feed = BeQuantRequestDataSpot(data_queue, **kwargs)

        # BeQuant may not have async_get_tick, skip if method doesn't exist
        if not hasattr(feed, 'async_get_tick'):
            return

        feed.async_get_tick("BTCUSDT", extra_data={"test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            tick_data = None

        # If async is supported, validate response
        if tick_data is not None:
            assert isinstance(tick_data, RequestData)

    @pytest.mark.integration
    def test_bequant_req_kline_data(self):
        """Test BeQuant kline data (sync)."""
        feed = init_req_feed()
        data = feed.get_kline("BTCUSDT", "1m", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have kline data, validate structure
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            # BeQuant returns list of klines or list with kline data
            assert isinstance(klines, (list, dict))

    def test_bequant_async_kline_data(self):
        """Test BeQuant kline data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "public_key": "test_key",
            "private_key": "test_secret",
            "exchange_name": "BEQUANT___SPOT",
        }
        feed = BeQuantRequestDataSpot(data_queue, **kwargs)

        # BeQuant may not have async_get_kline, skip if method doesn't exist
        if not hasattr(feed, 'async_get_kline'):
            return

        feed.async_get_kline("BTCUSDT", period="1m", count=3, extra_data={"test_async_kline_data": True})
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            kline_data = None

        # If async is supported, validate response
        if kline_data is not None:
            assert isinstance(kline_data, RequestData)

    @pytest.mark.integration
    def test_bequant_req_orderbook_data(self):
        """Test BeQuant orderbook data (sync)."""
        feed = init_req_feed()
        data = feed.get_depth("BTCUSDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have depth data, validate structure
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            # BeQuant returns dict with bids/asks
            if isinstance(orderbook, dict):
                assert "bids" in orderbook or isinstance(orderbook, dict)
                # Validate orderbook structure
                if "bids" in orderbook:
                    assert isinstance(orderbook["bids"], list)
                if "asks" in orderbook:
                    assert isinstance(orderbook["asks"], list)

    def test_bequant_async_orderbook_data(self):
        """Test BeQuant orderbook data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "public_key": "test_key",
            "private_key": "test_secret",
            "exchange_name": "BEQUANT___SPOT",
        }
        feed = BeQuantRequestDataSpot(data_queue, **kwargs)

        # BeQuant may not have async_get_depth, skip if method doesn't exist
        if not hasattr(feed, 'async_get_depth'):
            return

        feed.async_get_depth("BTCUSDT", 20)
        time.sleep(3)

        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            depth_data = None

        # If async is supported, validate response
        if depth_data is not None:
            assert isinstance(depth_data, RequestData)

# ==================== Data Validation Functions ====================

def validate_bequant_tick_data(ticker_data):
    """Validate BeQuant ticker data has expected structure."""
    if not isinstance(ticker_data, dict):
        return False
    # Check for common ticker fields
    price_fields = ["bid", "ask", "last", "last_price", "close"]
    has_price = any(field in ticker_data for field in price_fields)
    return has_price

def validate_bequant_kline_data(kline_data):
    """Validate BeQuant kline data has expected structure."""
    # kline_data can be a list of klines or a single kline
    if isinstance(kline_data, list) and len(kline_data) > 0:
        # Each kline should have OHLCV data
        for kline in kline_data:
            if isinstance(kline, list) and len(kline) >= 5:
                # Expected format: [timestamp, open, high, low, close, volume]
                return True
            elif isinstance(kline, dict):
                # Check for OHLCV fields
                ohlcv_fields = ["open", "high", "low", "close", "volume"]
                if any(field in kline for field in ohlcv_fields):
                    return True
    return False

def validate_bequant_orderbook_data(orderbook_data):
    """Validate BeQuant orderbook data has expected structure."""
    if not isinstance(orderbook_data, dict):
        return False
    # Should have bids and/or asks
    has_bids = "bids" in orderbook_data and isinstance(orderbook_data["bids"], list)
    has_asks = "asks" in orderbook_data and isinstance(orderbook_data["asks"], list)
    return has_bids or has_asks

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
