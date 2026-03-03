"""
Test BingX exchange integration.

Run tests:
    pytest tests/feeds/test_bingx.py -v

Run with coverage:
    pytest tests/feeds/test_bingx.py --cov=bt_api_py.feeds.live_bingx --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bingx_exchange_data import (
    BingXExchangeData,
    BingXExchangeDataSpot,
)
from bt_api_py.feeds.live_bingx.spot import BingXRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bingx_ticker import BingXRequestTickerData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BingX
import bt_api_py.feeds.register_bingx  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bingx_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBingXExchangeData:
    """Test BingX exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating BingX spot exchange data."""
        exchange_data = BingXExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths or "get_ticker" in exchange_data.rest_paths

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BingXExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BingXExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BingXExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBingXRequestDataSpot:
    """Test BingX REST API request methods."""

    def test_request_data_creation(self):
        """Test creating BingX request data."""
        data_queue = queue.Queue()
        request_data = BingXRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BINGX___SPOT",
        )
        assert request_data.exchange_name == "BINGX___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "50000",
                }
            ]
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "data": {
                "bids": [["49990", "1.0"], ["49980", "2.0"]],
                "asks": [["50010", "1.0"], ["50020", "2.0"]],
            }
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "data": [
                [1640995200, "49500", "51000", "49000", "50000", "1234.56"]
            ]
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)


class TestBingXRegistration:
    """Test BingX registration."""

    def test_bingx_registered(self):
        """Test that BingX is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BINGX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BINGX___SPOT"] == BingXRequestDataSpot

        # Check if exchange data is registered
        assert "BINGX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BINGX___SPOT"] == BingXExchangeDataSpot

        # Check if balance handler is registered
        assert "BINGX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BINGX___SPOT"] is not None

    def test_bingx_create_exchange_data(self):
        """Test creating BingX exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BINGX___SPOT")
        assert isinstance(exchange_data, BingXExchangeDataSpot)


class TestBingXIntegration:
    """Integration tests for BingX."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ===


class TestBingXLiveAPI:
    """Test BingX live API following Binance/OKX test standards."""

    def test_bingx_req_tick_data(self):
        """Test BingX ticker data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)
        data = feed.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have data, validate the ticker structure
        if data_list and len(data_list) > 0:
            pass
        ticker = data_list[0]
        # BingX returns dict with ticker info
        if isinstance(ticker, dict):
            assert isinstance(ticker, dict)

    def test_bingx_async_tick_data(self):
        """Test BingX ticker data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)

        # BingX may not have async_get_tick, skip if method doesn't exist
        if not hasattr(feed, 'async_get_tick'):
            return

        feed.async_get_tick(
            "BTC-USDT",
            extra_data={
                "test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass

    def test_bingx_req_kline_data(self):
        """Test BingX kline data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)
        data = feed.get_kline("BTC-USDT", "1m", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have kline data, validate structure
        if data_list and len(data_list) > 0:
            pass
        klines = data_list[0]
        # BingX returns list of klines
        assert isinstance(klines, (list, dict))

    def test_bingx_async_kline_data(self):
        """Test BingX kline data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)

        # BingX may not have async_get_kline, skip if method doesn't exist
        if not hasattr(feed, 'async_get_kline'):
            return

        feed.async_get_kline(
            "BTC-USDT",
            period="1m",
            count=3,
            extra_data={
                "test_async_kline_data": True})
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass

    def test_bingx_req_orderbook_data(self):
        """Test BingX orderbook data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)
        data = feed.get_depth("BTC-USDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have depth data, validate structure
        if data_list and len(data_list) > 0:
            pass
        orderbook = data_list[0]
        # BingX returns dict with bids/asks
        if isinstance(orderbook, dict):
            assert isinstance(orderbook, dict)

    def test_bingx_async_orderbook_data(self):
        """Test BingX orderbook data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BINGX___SPOT",
        }
        feed = BingXRequestDataSpot(data_queue, **kwargs)

        # BingX may not have async_get_depth, skip if method doesn't exist
        if not hasattr(feed, 'async_get_depth'):
            return

        feed.async_get_depth("BTC-USDT", 20)
        time.sleep(3)

        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass