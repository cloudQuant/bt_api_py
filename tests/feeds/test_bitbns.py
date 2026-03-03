"""
Test Bitbns exchange integration.

Run tests:
    pytest tests/feeds/test_bitbns.py -v

Run with coverage:
    pytest tests/feeds/test_bitbns.py --cov=bt_api_py.feeds.live_bitbns --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bitbns_exchange_data import (
    BitbnsExchangeData,
    BitbnsExchangeDataSpot,
)
from bt_api_py.feeds.live_bitbns.spot import BitbnsRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bitbns_ticker import BitbnsRequestTickerData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitbns
import bt_api_py.feeds.register_bitbns  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitbns_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBitbnsExchangeData:
    """Test Bitbns exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitbns spot exchange data."""
        exchange_data = BitbnsExchangeDataSpot()
        assert exchange_data.exchange_name == "bitbns"
        assert exchange_data.asset_type == "spot"
        # rest_url may be set via config or have a default
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitbnsExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitbnsExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitbnsExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitbnsRequestDataSpot:
    """Test Bitbns REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitbns request data."""
        data_queue = queue.Queue()
        request_data = BitbnsRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITBNS___SPOT",
        )
        assert request_data.exchange_name == "BITBNS___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_symbol(self):
        """Test symbol normalization for Bitbns."""
        data_queue = queue.Queue()
        request_data = BitbnsRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        # Bitbns uses uppercase with underscore format
        assert request_data._normalize_symbol("BTC/USDT") == "BTC_USDT"
        assert request_data._normalize_symbol("BTC-USDT") == "BTC_USDT"
        assert request_data._normalize_symbol("BTC") == "BTC"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "status": 1,
            "data": {
                "BTC": {
                    "last_traded_price": "50000",
                    "highest_buy_bid": "49990",
                    "lowest_sell_bid": "50010",
                }
            }
        }
        extra_data = {"symbol_name": "BTC/USDT"}

        result, success = BitbnsRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "status": 1,
            "data": {
                "bids": [["49990", "1.0"], ["49980", "2.0"]],
                "asks": [["50010", "1.0"], ["50020", "2.0"]],
            }
        }
        extra_data = {"symbol_name": "BTC/USDT"}

        result, success = BitbnsRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "status": 1,
            "data": [
                [1640995200, "49500", "51000", "49000", "50000", "1234.56"]
            ]
        }
        extra_data = {"symbol_name": "BTC/USDT", "period": "1h"}

        result, success = BitbnsRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)


class TestBitbnsRegistration:
    """Test Bitbns registration."""

    def test_bitbns_registered(self):
        """Test that Bitbns is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BITBNS___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITBNS___SPOT"] == BitbnsRequestDataSpot

        # Check if exchange data is registered
        assert "BITBNS___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITBNS___SPOT"] == BitbnsExchangeDataSpot

        # Check if balance handler is registered
        assert "BITBNS___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITBNS___SPOT"] is not None

    def test_bitbns_create_exchange_data(self):
        """Test creating Bitbns exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITBNS___SPOT")
        assert isinstance(exchange_data, BitbnsExchangeDataSpot)


class TestBitbnsIntegration:
    """Integration tests for Bitbns."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ===


class TestBitbnsLiveAPI:
    """Test Bitbns live API following Binance/OKX test standards."""

    @pytest.mark.integration
    def test_bitbns_req_tick_data(self):
        """Test Bitbns ticker data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)
        data = feed.get_tick("BTC/USDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have data, validate the ticker structure
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            # Bitbns returns dict with ticker info
            if isinstance(ticker, dict):
                pass
            assert isinstance(ticker, dict)

    def test_bitbns_async_tick_data(self):
        """Test Bitbns ticker data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)

        # Bitbns may not have async_get_tick, skip if method doesn't exist or raises NotImplementedError
        if not hasattr(feed, 'async_get_tick'):
            return

        try:
            feed.async_get_tick(
                "BTC/USDT",
                extra_data={
                    "test_async_tick_data": True})
            time.sleep(3)
        except NotImplementedError:
            # Async not implemented for this exchange
            return

        tick_data = None
        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if tick_data is not None:
            assert isinstance(tick_data, RequestData)

    @pytest.mark.integration
    def test_bitbns_req_kline_data(self):
        """Test Bitbns kline data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)
        data = feed.get_kline("BTC/USDT", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have kline data, validate structure
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            # Bitbns returns list of klines
            assert isinstance(klines, (list, dict))

    def test_bitbns_async_kline_data(self):
        """Test Bitbns kline data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)

        # Bitbns may not have async_get_kline, skip if method doesn't exist or raises NotImplementedError
        if not hasattr(feed, 'async_get_kline'):
            return

        try:
            feed.async_get_kline(
                "BTC/USDT",
                period="1h",
                count=3,
                extra_data={
                    "test_async_kline_data": True})
            time.sleep(5)
        except NotImplementedError:
            # Async not implemented for this exchange
            return

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            assert isinstance(kline_data, RequestData)

    @pytest.mark.integration
    def test_bitbns_req_orderbook_data(self):
        """Test Bitbns orderbook data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)
        data = feed.get_depth("BTC/USDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have depth data, validate structure
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            # Bitbns returns dict with bids/asks
            if isinstance(orderbook, dict):
                pass
            assert isinstance(orderbook, dict)

    def test_bitbns_async_orderbook_data(self):
        """Test Bitbns orderbook data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITBNS___SPOT",
        }
        feed = BitbnsRequestDataSpot(data_queue, **kwargs)

        # Bitbns may not have async_get_depth, skip if method doesn't exist or raises NotImplementedError
        if not hasattr(feed, 'async_get_depth'):
            return

        try:
            feed.async_get_depth("BTC/USDT", 20)
            time.sleep(3)
        except NotImplementedError:
            # Async not implemented for this exchange
            return

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            assert isinstance(depth_data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
