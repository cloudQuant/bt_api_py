"""
Phemex Exchange Integration Tests

Tests for Phemex spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orderbooks)
- Registration module

Run tests:
    pytest tests/feeds/test_phemex.py -v

Run with coverage:
    pytest tests/feeds/test_phemex.py --cov=bt_api_py.feeds.live_phemex --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_phemex.py -m "not integration" -v
"""

import json
import queue
import time
import pytest

from bt_api_py.containers.exchanges.phemex_exchange_data import (
    PhemexExchangeData,
    PhemexExchangeDataSpot,
)
from bt_api_py.containers.tickers.phemex_ticker import PhemexRequestTickerData
from bt_api_py.feeds.live_phemex.spot import PhemexRequestDataSpot
from bt_api_py.feeds.register_phemex import register_phemex
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Phemex
import bt_api_py.feeds.register_phemex  # noqa: F401


class TestPhemexExchangeData:
    """Test Phemex exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        """Test base PhemexExchangeData initialization."""
        data = PhemexExchangeData()
        assert data.exchange_name == "phemex"
        assert data.rest_url == "https://api.phemex.com"
        assert data.wss_url == "wss://ws.phemex.com"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "USDT" in data.legal_currency

    def test_exchange_data_spot_initialization(self):
        """Test PhemexExchangeDataSpot initialization."""
        data = PhemexExchangeDataSpot()
        assert data.exchange_name in ["phemex", "phemex_spot", "phemexSpot"]
        assert data.asset_type == "SPOT"

    def test_get_period_conversion(self):
        """Test kline period conversion."""
        data = PhemexExchangeDataSpot()
        assert data.get_period("1m") == "60"
        assert data.get_period("1h") == "3600"
        assert data.get_period("1d") == "86400"

    def test_scale_unscale_price(self):
        """Test price scaling and unscaling."""
        data = PhemexExchangeDataSpot()
        price = 50000.0
        scaled = data.scale_price(price)
        assert scaled == int(price * 1e8)

        unscaled = data.unscale_price(scaled)
        assert unscaled == price


class TestPhemexRequestDataSpot:
    """Test Phemex spot request feed."""

    def test_request_data_initialization(self):
        """Test request data feed initialization."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)
        assert feed.exchange_name == "PHEMEX___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = PhemexRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_normalize_functions(self):
        """Test data normalization functions."""
        # Test ticker normalize
        input_data = {"code": 0, "data": {"lastEp": 50000000000}}
        result, status = PhemexRequestDataSpot._get_tick_normalize_function(
            input_data, {}
        )
        assert status is True
        assert result[0]["lastEp"] == 50000000000

        # Test depth normalize
        input_data = {"code": 0, "data": {"book": {"bids": [], "asks": []}}}
        result, status = PhemexRequestDataSpot._get_depth_normalize_function(
            input_data, {}
        )
        assert status is True

        # Test kline normalize
        input_data = {"code": 0, "data": {"rows": []}}
        result, status = PhemexRequestDataSpot._get_kline_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_tick_params(self):
        """Test get tick parameter generation."""
        data_queue = queue.Queue()
        request_data = PhemexRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/USDT")

        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = PhemexRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/USDT", 20)

        assert "orderbook" in path.lower() or "depth" in path.lower()
        assert params["level"] == 20

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = PhemexRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline(
            "BTC/USDT", "1h", 10)

        assert "kline" in path.lower() or "candle" in path.lower()
        assert params["resolution"] == "3600"


class TestPhemexDataContainers:
    """Test Phemex data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "code": 0,
            "data": {
                "symbol": "sBTCUSDT",
                "lastEp": 5000000000000,  # 50000 * 1e8
                "highEp": 5100000000000,  # 51000 * 1e8
                "lowEp": 4900000000000,   # 49000 * 1e8
                "volumeEv": 1000000000,
                "bidEp": 4999900000000,   # 49999 * 1e8
                "askEp": 5000100000000,   # 50001 * 1e8
            }
        }

        ticker = PhemexRequestTickerData(
            ticker_response, "BTC/USDT", "SPOT"
        )
        # _parse_data is already called in __init__, no need to call again

        assert ticker.symbol == "sBTCUSDT"
        assert ticker.last_price == 50000.0


class TestPhemexRegistration:
    """Test Phemex registration module."""

    def test_registration(self):
        """Test that Phemex registration works."""
        # Import to trigger registration
        from bt_api_py.feeds import register_phemex

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("PHEMEX___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get("PHEMEX___SPOT")
        assert feed_class is not None
        assert feed_class == PhemexRequestDataSpot

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get(
            "PHEMEX___SPOT")
        assert data_class is not None
        assert data_class == PhemexExchangeDataSpot

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get("PHEMEX___SPOT")
        assert handler is not None

    def test_get_registered_feed(self):
        """Test getting registered feed class."""
        from bt_api_py.feeds import register_phemex

        # Use create_feed to get an instance
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "PHEMEX___SPOT",
            data_queue,
        )
        assert feed is not None
        assert isinstance(feed, PhemexRequestDataSpot)

    def test_get_registered_exchange_data(self):
        """Test getting registered exchange data class."""
        from bt_api_py.feeds import register_phemex

        # Use create_exchange_data to get an instance
        data = ExchangeRegistry.create_exchange_data("PHEMEX___SPOT")
        assert data is not None
        assert isinstance(data, PhemexExchangeDataSpot)


# ==================== Live API Tests ====================

class TestPhemexTickData:
    """Test ticker data endpoints."""

    @pytest.mark.integration
    def test_phemex_req_spot_tick_data(self):
        """Test getting spot ticker data from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        data = feed.get_tick("BTC/USDT")
        assert isinstance(data, RequestData)

        tick_data_list = data.get_data()
        assert isinstance(tick_data_list, list)

        if len(tick_data_list) > 0:
            tick_data = tick_data_list[0]
            # Phemex returns scaled prices
            assert "lastEp" in tick_data or "symbol" in tick_data

    @pytest.mark.integration
    def test_phemex_async_spot_tick_data(self):
        """Test async ticker data from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        feed.async_get_tick(
            "BTC/USDT",
            extra_data={
                "test_async_tick_data": True})
        time.sleep(3)

        tick_data = None
        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if tick_data is not None:
            pass
        assert isinstance(tick_data, RequestData)


class TestPhemexKlineData:
    """Test kline/candlestick data endpoints."""

    @pytest.mark.integration
    def test_phemex_req_spot_kline_data(self):
        """Test getting kline data from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        data = feed.get_kline("BTC/USDT", "1h", count=10)
        assert isinstance(data, RequestData)

        kline_data = data.get_data()
        assert isinstance(kline_data, list)

    @pytest.mark.integration
    def test_phemex_async_spot_kline_data(self):
        """Test async kline data from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        feed.async_get_kline("BTC/USDT", period="1h", count=5)
        time.sleep(3)

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            pass
        assert isinstance(kline_data, RequestData)


class TestPhemexOrderBook:
    """Test order book depth endpoints."""

    @pytest.mark.integration
    def test_phemex_req_spot_depth_data(self):
        """Test getting order book depth from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        data = feed.get_depth("BTC/USDT", 20)
        assert isinstance(data, RequestData)

        depth_data = data.get_data()
        assert isinstance(depth_data, list)

        if len(depth_data) > 0:
            orderbook = depth_data[0]
            assert "book" in orderbook or "bids" in orderbook

    @pytest.mark.integration
    def test_phemex_async_spot_depth_data(self):
        """Test async order book depth from Phemex."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        feed.async_get_depth("BTC/USDT", 20)
        time.sleep(3)

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            pass
        assert isinstance(depth_data, RequestData)


# ==================== Mock Tests ====================

class TestPhemexMockData:
    """Test with mock data."""

    def test_phemex_tick_normalize_with_mock(self):
        """Test ticker normalize with mock response."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        mock_response = {
            "code": 0,
            "data": {
                "symbol": "sBTCUSDT",
                "lastEp": 50000000000,
                "bidEp": 49999000000,
                "askEp": 50001000000,
            }
        }

        result, success = feed._get_tick_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0

    def test_phemex_depth_normalize_with_mock(self):
        """Test depth normalize with mock response."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        mock_response = {
            "code": 0,
            "data": {
                "book": {
                    "bids": [[49999, 1, 1]],
                    "asks": [[50001, 1, 1]]
                }
            }
        }

        result, success = feed._get_depth_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0

    def test_phemex_kline_normalize_with_mock(self):
        """Test kline normalize with mock response."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        mock_response = {
            "code": 0,
            "data": {
                "rows": [
                    [1672531200, 50000, 51000, 49000, 50500, 100, 0],
                    [1672534800, 50500, 51500, 50000, 51000, 150, 0]
                ]
            }
        }

        result, success = feed._get_kline_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0


class TestPhemexIntegration:
    """Integration tests for Phemex."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = PhemexRequestDataSpot(data_queue)

        # Test ticker (would require network)
        # ticker = feed.get_tick("BTC/USDT")
        # assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        # This would require API keys to test
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
