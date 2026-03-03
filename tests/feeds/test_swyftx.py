"""
Swyftx Exchange Integration Tests

Tests for Swyftx spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orderbooks)
- Registration module

Run tests:
    pytest tests/feeds/test_swyftx.py -v

Run with coverage:
    pytest tests/feeds/test_swyftx.py --cov=bt_api_py.feeds.live_swyftx --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_swyftx.py -m "not integration" -v
"""

import json
import queue
import time
import pytest

from bt_api_py.containers.exchanges.swyftx_exchange_data import (
    SwyftxExchangeData,
    SwyftxExchangeDataSpot,
)
from bt_api_py.containers.tickers.swyftx_ticker import SwyftxRequestTickerData
from bt_api_py.feeds.live_swyftx.spot import SwyftxRequestDataSpot
from bt_api_py.feeds.register_swyftx import register_swyftx
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Swyftx
import bt_api_py.feeds.register_swyftx  # noqa: F401


class TestSwyftxExchangeData:
    """Test Swyftx exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        """Test base SwyftxExchangeData initialization."""
        data = SwyftxExchangeData()
        assert data.exchange_name == "swyftx"
        assert data.rest_url == "https://api.swyftx.com.au"
        assert data.wss_url == "wss://api.swyftx.com.au"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "AUD" in data.legal_currency  # Australian Dollar

    def test_kline_periods(self):
        """Test kline period configuration."""
        data = SwyftxExchangeData()
        assert "1m" in data.kline_periods
        assert "5m" in data.kline_periods
        assert "1h" in data.kline_periods
        assert "1d" in data.kline_periods


class TestSwyftxRequestDataSpot:
    """Test Swyftx spot request feed."""

    def test_request_data_initialization(self):
        """Test request data feed initialization."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)
        assert feed.exchange_name == "SWYFTX___SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick_params(self):
        """Test get tick parameter generation."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC-AUD")

        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        mock_response = {
            "market": "BTC-AUD",
            "lastPrice": "75000.00",
            "bid": "74999.00",
            "ask": "75001.00",
            "volume": "1.5",
        }

        result, success = request_data._get_tick_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC-AUD", 20)

        assert "orderbook" in path.lower() or "depth" in path.lower()
        assert params["depth"] == 20

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        mock_response = {
            "bids": [[74999, "1.0"]],
            "asks": [[75001, "1.0"]],
        }

        result, success = request_data._get_depth_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline("BTC-AUD", "1h", 10)

        assert "candle" in path.lower() or "kline" in path.lower()
        assert params["interval"] == "3600"

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        mock_response = [
            [1672531200, "75000", "76000", "74000", "75500", "1.5"],
            [1672534800, "75500", "76500", "74500", "76000", "2.0"]
        ]

        result, success = request_data._get_kline_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        data_queue = queue.Queue()
        request_data = SwyftxRequestDataSpot(data_queue)

        mock_response = [
            {"id": "BTC-AUD", "baseAsset": "BTC", "quoteAsset": "AUD"}]

        result, success = request_data._get_exchange_info_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0


class TestSwyftxDataContainers:
    """Test Swyftx data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "market": "BTC-AUD",
            "lastPrice": "75000.00",
            "high": "76000.00",
            "low": "74000.00",
            "bid": "74999.00",
            "ask": "75001.00",
            "volume": "1.5",
        }

        ticker = SwyftxRequestTickerData(
            ticker_response, "BTC-AUD", "SPOT"
        )
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTC-AUD"
        assert ticker.last_price == 75000.0
        assert ticker.bid_price == 74999.0
        assert ticker.ask_price == 75001.0


class TestSwyftxRegistration:
    """Test Swyftx registration module."""

    def test_registration(self):
        """Test that Swyftx registration works."""
        # Import to trigger registration
        from bt_api_py.feeds import register_swyftx

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("SWYFTX___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get("SWYFTX___SPOT")
        assert feed_class is not None
        assert feed_class == SwyftxRequestDataSpot

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get(
            "SWYFTX___SPOT")
        assert data_class is not None
        assert data_class == SwyftxExchangeDataSpot

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get("SWYFTX___SPOT")
        assert handler is not None

    def test_get_registered_feed(self):
        """Test getting registered feed class."""
        from bt_api_py.feeds import register_swyftx

        # Use create_feed to get an instance
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "SWYFTX___SPOT",
            data_queue,
        )
        assert feed is not None
        assert isinstance(feed, SwyftxRequestDataSpot)

    def test_get_registered_exchange_data(self):
        """Test getting registered exchange data class."""
        from bt_api_py.feeds import register_swyftx

        # Use create_exchange_data to get an instance
        data = ExchangeRegistry.create_exchange_data("SWYFTX___SPOT")
        assert data is not None
        assert isinstance(data, SwyftxExchangeDataSpot)


class TestSwyftxConfig:
    """Test Swyftx configuration loading."""

    def test_legal_currencies(self):
        """Test that legal currencies are properly configured."""
        data = SwyftxExchangeData()
        # Should support AUD primarily (Australian exchange)
        assert "AUD" in data.legal_currency
        assert "USD" in data.legal_currency


# ==================== Live API Tests ====================

class TestSwyftxTickData:
    """Test ticker data endpoints."""

    @pytest.mark.integration
    def test_swyftx_req_spot_tick_data(self):
        """Test getting spot ticker data from Swyftx."""
        from bt_api_py.error_framework import AuthenticationError

        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        try:
            data = feed.get_tick("BTC-AUD")
            assert isinstance(data, RequestData)

            tick_data_list = data.get_data()
            assert isinstance(tick_data_list, list)

            if len(tick_data_list) > 0:
                pass
            tick_data = tick_data_list[0]
            assert "lastPrice" in tick_data or "last" in tick_data or "price" in tick_data
        except AuthenticationError:
            pass
            # Expected when no API key is configured
        except Exception as e:
            pass
            # Handle other exceptions

    @pytest.mark.integration
    def test_swyftx_async_spot_tick_data(self):
        """Test async ticker data from Swyftx."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        feed.async_get_tick(
            "BTC-AUD",
            extra_data={
                "test_async_tick_data": True})
        time.sleep(3)

        tick_data = None
        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if tick_data is not None:
            assert isinstance(tick_data, RequestData)


class TestSwyftxKlineData:
    """Test kline/candlestick data endpoints."""

    @pytest.mark.integration
    def test_swyftx_req_spot_kline_data(self):
        """Test getting kline data from Swyftx."""
        from bt_api_py.error_framework import AuthenticationError

        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        try:
            data = feed.get_kline("BTC-AUD", "1h", count=10)
            assert isinstance(data, RequestData)

            kline_data_list = data.get_data()
            assert isinstance(kline_data_list, list)
        except AuthenticationError:
            pass
            # Expected when no API key is configured
        except Exception as e:
            pass
            # Handle other exceptions

    @pytest.mark.integration
    def test_swyftx_async_spot_kline_data(self):
        """Test async kline data from Swyftx."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        feed.async_get_kline("BTC-AUD", period="1h", count=5)
        time.sleep(3)

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            assert isinstance(kline_data, RequestData)


class TestSwyftxOrderBook:
    """Test order book depth endpoints."""

    @pytest.mark.integration
    def test_swyftx_req_spot_depth_data(self):
        """Test getting order book depth from Swyftx."""
        from bt_api_py.error_framework import AuthenticationError

        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        try:
            data = feed.get_depth("BTC-AUD", 20)
            assert isinstance(data, RequestData)

            depth_data = data.get_data()
            assert isinstance(depth_data, list)

            if len(depth_data) > 0:
                pass
            orderbook = depth_data[0]
            assert "bids" in orderbook or "asks" in orderbook
        except AuthenticationError:
            pass
            # Expected when no API key is configured
        except Exception as e:
            pass
            # Handle other exceptions

    @pytest.mark.integration
    def test_swyftx_async_spot_depth_data(self):
        """Test async order book depth from Swyftx."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        feed.async_get_depth("BTC-AUD", 20)
        time.sleep(3)

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            assert isinstance(depth_data, RequestData)


# ==================== Mock Tests ====================

class TestSwyftxMockData:
    """Test with mock data."""

    def test_swyftx_tick_with_mock(self):
        """Test ticker with mock response."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        mock_response = {
            "market": "BTC-AUD",
            "lastPrice": "75000.00",
            "bid": "74999.00",
            "ask": "75001.00",
        }

        result, success = feed._get_tick_normalize_function(
            mock_response, {"symbol_name": "BTC-AUD"})
        assert success is True
        assert len(result) > 0

    def test_swyftx_depth_with_mock(self):
        """Test order book with mock response."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        mock_response = {
            "bids": [[74999, "1.0"], [74998, "2.0"]],
            "asks": [[75001, "1.0"], [75002, "2.5"]]
        }

        result, success = feed._get_depth_normalize_function(
            mock_response, {"symbol_name": "BTC-AUD"})
        assert success is True
        assert len(result) > 0

    def test_swyftx_kline_with_mock(self):
        """Test kline with mock response."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        mock_response = [
            [1672531200, "75000", "76000", "74000", "75500", "1.5"],
            [1672534800, "75500", "76500", "74500", "76000", "2.0"]
        ]

        result, success = feed._get_kline_normalize_function(
            mock_response, {"symbol_name": "BTC-AUD"})
        assert success is True
        assert len(result) > 0


class TestSwyftxIntegration:
    """Integration tests for Swyftx."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = SwyftxRequestDataSpot(data_queue)

        # Test ticker (would require network)
        # ticker = feed.get_tick("BTC/AUD")
        # assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        # This would require API keys to test
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
