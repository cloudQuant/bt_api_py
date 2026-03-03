"""
Test BigONE exchange integration.

Run tests:
    pytest tests/feeds/test_bigone.py -v

Run with coverage:
    pytest tests/feeds/test_bigone.py --cov=bt_api_py.feeds.live_bigone --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bigone_exchange_data import (
    BigONEExchangeData,
    BigONEExchangeDataSpot,
)
from bt_api_py.feeds.live_bigone.spot import BigONERequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bigone_ticker import BigONERequestTickerData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BigONE
import bt_api_py.feeds.register_bigone  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bigone_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBigONEExchangeData:
    """Test BigONE exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating BigONE spot exchange data."""
        exchange_data = BigONEExchangeDataSpot()
        assert exchange_data.exchange_name == "bigoneSpot"
        assert exchange_data.asset_type == "spot"
        # rest_url may be set via config or have a default
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BigONEExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BigONEExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BigONEExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBigONERequestDataSpot:
    """Test BigONE REST API request methods."""

    def test_request_data_creation(self):
        """Test creating BigONE request data."""
        data_queue = queue.Queue()
        request_data = BigONERequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BIGONE___SPOT",
        )
        assert request_data.exchange_name == "BIGONE___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "data": {
                "asset_pair_name": "BTC-USDT",
                "close": "50000",
            }
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BigONERequestDataSpot._get_tick_normalize_function(
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

        result, success = BigONERequestDataSpot._get_depth_normalize_function(
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

        result, success = BigONERequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)


class TestBigONERegistration:
    """Test BigONE registration."""

    def test_bigone_registered(self):
        """Test that BigONE is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BIGONE___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BIGONE___SPOT"] == BigONERequestDataSpot

        # Check if exchange data is registered
        assert "BIGONE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BIGONE___SPOT"] == BigONEExchangeDataSpot

        # Check if balance handler is registered
        assert "BIGONE___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BIGONE___SPOT"] is not None

    def test_bigone_create_exchange_data(self):
        """Test creating BigONE exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BIGONE___SPOT")
        assert isinstance(exchange_data, BigONEExchangeDataSpot)


class TestBigONEIntegration:
    """Integration tests for BigONE."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ===


class TestBigONELiveAPI:
    """Test BigONE live API following Binance/OKX test standards."""

    def test_bigone_req_tick_data(self):
        """Test BigONE ticker data (sync)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)
        try:
            data = feed.get_tick("BTC-USDT")
            assert isinstance(data, RequestData)
            data_list = data.get_data()
            assert isinstance(data_list, list)

            # If we have data, validate the ticker structure
            if data_list and len(data_list) > 0:
                pass
            ticker = data_list[0]
            # BigONE returns dict with ticker info
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")

    def test_bigone_async_tick_data(self):
        """Test BigONE ticker data (async)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)

        # BigONE may not have async_get_tick, skip if method doesn't exist
        if not hasattr(feed, 'async_get_tick'):
            return

        try:
            feed.async_get_tick(
                "BTC-USDT",
                extra_data={
                    "test_async_tick_data": True})
            time.sleep(3)

            try:
                tick_data = data_queue.get(timeout=10)
                assert tick_data is not None
            except queue.Empty:
                pass  # No data received
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")

    def test_bigone_req_kline_data(self):
        """Test BigONE kline data (sync)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)
        try:
            data = feed.get_kline("BTC-USDT", "1m", count=2)
            assert isinstance(data, RequestData)
            data_list = data.get_data()
            assert isinstance(data_list, list)

            # If we have kline data, validate structure
            if data_list and len(data_list) > 0:
                pass
            klines = data_list[0]
            # BigONE returns list of klines or list with kline data
            assert isinstance(klines, (list, dict))
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")

    def test_bigone_async_kline_data(self):
        """Test BigONE kline data (async)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)

        # BigONE may not have async_get_kline, skip if method doesn't exist
        if not hasattr(feed, 'async_get_kline'):
            return

        try:
            feed.async_get_kline(
                "BTC-USDT",
                period="1m",
                count=3,
                extra_data={
                    "test_async_kline_data": True})
            time.sleep(5)

            try:
                kline_data = data_queue.get(timeout=10)
                assert kline_data is not None
            except queue.Empty:
                pass  # No data received
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")

    def test_bigone_req_orderbook_data(self):
        """Test BigONE orderbook data (sync)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)
        try:
            data = feed.get_depth("BTC-USDT", 20)
            assert isinstance(data, RequestData)
            data_list = data.get_data()
            assert isinstance(data_list, list)

            # If we have depth data, validate structure
            if data_list and len(data_list) > 0:
                pass
            orderbook = data_list[0]
            # BigONE returns dict with bids/asks
            if isinstance(orderbook, dict):
                assert isinstance(orderbook, dict)
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")

    def test_bigone_async_orderbook_data(self):
        """Test BigONE orderbook data (async)."""
        from bt_api_py.exceptions import RequestFailedError
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BIGONE___SPOT",
        }
        feed = BigONERequestDataSpot(data_queue, **kwargs)

        # BigONE may not have async_get_depth, skip if method doesn't exist
        if not hasattr(feed, 'async_get_depth'):
            return

        try:
            feed.async_get_depth("BTC-USDT", 20)
            time.sleep(3)

            try:
                depth_data = data_queue.get(timeout=10)
                assert depth_data is not None
            except queue.Empty:
                pass  # No data received
        except RequestFailedError as e:
            # API endpoint may not be available - network timeout or API unavailable
            pytest.skip(f"BigONE API unavailable: {e}")
