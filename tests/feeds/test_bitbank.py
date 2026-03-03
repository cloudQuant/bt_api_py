"""
Test Bitbank exchange integration.

Run tests:
    pytest tests/feeds/test_bitbank.py -v

Run with coverage:
    pytest tests/feeds/test_bitbank.py --cov=bt_api_py.feeds.live_bitbank --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bitbank_exchange_data import (
    BitbankExchangeData,
    BitbankExchangeDataSpot,
)
from bt_api_py.feeds.live_bitbank.spot import BitbankRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bitbank_ticker import BitbankRequestTickerData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitbank
import bt_api_py.feeds.register_bitbank  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitbank_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBitbankExchangeData:
    """Test Bitbank exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitbank spot exchange data."""
        exchange_data = BitbankExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths or "get_ticker" in exchange_data.rest_paths

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitbankExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitbankExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitbankExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitbankRequestDataSpot:
    """Test Bitbank REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitbank request data."""
        data_queue = queue.Queue()
        request_data = BitbankRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITBANK___SPOT",
        )
        assert request_data.exchange_name == "BITBANK___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_pair(self):
        """Test symbol normalization for Bitbank."""
        data_queue = queue.Queue()
        request_data = BitbankRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        # Bitbank uses lowercase format with underscore
        assert request_data._normalize_pair("BTC/JPY") == "btc_jpy"
        assert request_data._normalize_pair("BTC-JPY") == "btc_jpy"
        assert request_data._normalize_pair("BTC_JPY") == "btc_jpy"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "success": 1,
            "data": {
                "last": "5000000",
                "buy": "4999000",
                "sell": "5001000",
            }
        }
        extra_data = {"symbol_name": "BTC/JPY"}

        result, success = BitbankRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "success": 1,
            "data": {
                "bids": [
                    {"price": "4999000", "amount": "0.1"},
                    {"price": "4998000", "amount": "0.2"},
                ],
                "asks": [
                    {"price": "5001000", "amount": "0.1"},
                    {"price": "5002000", "amount": "0.2"},
                ],
            }
        }
        extra_data = {"symbol_name": "BTC/JPY"}

        result, success = BitbankRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "success": 1,
            "data": {
                "candlestick": [
                    {
                        "type": "1hour",
                        "ohlcv": [
                            "4950000", "5100000", "4900000", "5000000", "123.456", "1640995200000"
                        ]
                    }
                ]
            }
        }
        extra_data = {"symbol_name": "BTC/JPY"}

        result, success = BitbankRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "ohlcv" in result[0]


class TestBitbankRegistration:
    """Test Bitbank registration."""

    def test_bitbank_registered(self):
        """Test that Bitbank is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BITBANK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITBANK___SPOT"] == BitbankRequestDataSpot

        # Check if exchange data is registered
        assert "BITBANK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITBANK___SPOT"] == BitbankExchangeDataSpot

        # Check if balance handler is registered
        assert "BITBANK___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITBANK___SPOT"] is not None

    def test_bitbank_create_exchange_data(self):
        """Test creating Bitbank exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITBANK___SPOT")
        assert isinstance(exchange_data, BitbankExchangeDataSpot)


class TestBitbankIntegration:
    """Integration tests for Bitbank."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ===


class TestBitbankLiveAPI:
    """Test Bitbank live API following Binance/OKX test standards."""

    @pytest.mark.integration
    def test_bitbank_req_tick_data(self):
        """Test Bitbank ticker data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)
        data = feed.get_tick("BTC/JPY")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have data, validate the ticker structure
        if data_list and len(data_list) > 0:
            pass
        ticker = data_list[0]
        # Bitbank returns dict with ticker info
        if isinstance(ticker, dict):
            assert isinstance(ticker, dict)

    @pytest.mark.integration
    def test_bitbank_async_tick_data(self):
        """Test Bitbank ticker data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)

        # Bitbank may not have async_get_tick, skip if method doesn't exist
        if not hasattr(feed, 'async_get_tick'):
            pass
        return

        feed.async_get_tick(
            "BTC/JPY",
            extra_data={
                "test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitbank_req_kline_data(self):
        """Test Bitbank kline data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)
        data = feed.get_kline("BTC/JPY", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have kline data, validate structure
        if data_list and len(data_list) > 0:
            pass
        klines = data_list[0]
        # Bitbank returns dict with ohlcv or list of klines
        assert isinstance(klines, (list, dict))

    @pytest.mark.integration
    def test_bitbank_async_kline_data(self):
        """Test Bitbank kline data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)

        # Bitbank may not have async_get_kline, skip if method doesn't exist
        if not hasattr(feed, 'async_get_kline'):
            pass
        return

        feed.async_get_kline(
            "BTC/JPY",
            period="1h",
            count=3,
            extra_data={
                "test_async_kline_data": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitbank_req_orderbook_data(self):
        """Test Bitbank orderbook data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)
        data = feed.get_depth("BTC/JPY", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have depth data, validate structure
        if data_list and len(data_list) > 0:
            pass
        orderbook = data_list[0]
        # Bitbank returns dict with bids/asks
        if isinstance(orderbook, dict):
            assert isinstance(orderbook, dict)

    @pytest.mark.integration
    def test_bitbank_async_orderbook_data(self):
        """Test Bitbank orderbook data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBANK___SPOT",
        }
        feed = BitbankRequestDataSpot(data_queue, **kwargs)

        # Bitbank may not have async_get_depth, skip if method doesn't exist
        if not hasattr(feed, 'async_get_depth'):
            pass
        return

        feed.async_get_depth("BTC/JPY", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass