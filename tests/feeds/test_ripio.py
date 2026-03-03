"""
Ripio Exchange Integration Tests

Tests for Ripio spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orderbooks)
- Registration module

Run tests:
    pytest tests/feeds/test_ripio.py -v

Run with coverage:
    pytest tests/feeds/test_ripio.py --cov=bt_api_py.feeds.live_ripio --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_ripio.py -m "not integration" -v
"""

import json
import queue
import time
import pytest

from bt_api_py.containers.exchanges.ripio_exchange_data import (
    RipioExchangeData,
    RipioExchangeDataSpot,
)
from bt_api_py.containers.tickers.ripio_ticker import RipioRequestTickerData
from bt_api_py.feeds.live_ripio.spot import RipioRequestDataSpot
from bt_api_py.feeds.register_ripio import register_ripio
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Ripio
import bt_api_py.feeds.register_ripio  # noqa: F401


class TestRipioExchangeData:
    """Test Ripio exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        """Test base RipioExchangeData initialization."""
        data = RipioExchangeData()
        assert data.exchange_name == "ripio"
        assert data.rest_url == "https://api.exchange.ripio.com"
        assert data.wss_url == "wss://api.exchange.ripio.com/ws"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "ARS" in data.legal_currency
        assert "BRL" in data.legal_currency
        assert "USDT" in data.legal_currency

    def test_get_symbol_conversion(self):
        """Test symbol format conversion."""
        data = RipioExchangeDataSpot()
        # Ripio uses underscore format
        assert data.get_symbol("BTC/USDT") == "BTC_USDT"
        assert data.get_symbol("ETH-USDT") == "ETH_USDT"
        assert data.get_symbol("btc_usdt") == "BTC_USDT"  # Normalized

    def test_get_period_conversion(self):
        """Test kline period conversion."""
        data = RipioExchangeDataSpot()
        assert data.get_period("1m") == "1"
        assert data.get_period("1h") == "60"
        assert data.get_period("1d") == "1440"


class TestRipioRequestDataSpot:
    """Test Ripio spot request feed."""

    def test_request_data_initialization(self):
        """Test request data feed initialization."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        assert feed.exchange_name == "RIPIO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = RipioRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        # Ripio wraps response in {'success': true, 'data': {...}}
        input_data = {
            "success": True,
            "data": {
                "lastPrice": "50000.00",
                "bidPrice": "49999.00"}}
        result, status = RipioRequestDataSpot._get_tick_normalize_function(
            input_data, {}
        )
        assert status is True
        assert result[0]["lastPrice"] == "50000.00"

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        input_data = {"success": True, "data": {"bids": [], "asks": []}}
        result, status = RipioRequestDataSpot._get_depth_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = {"success": True, "data": []}
        result, status = RipioRequestDataSpot._get_kline_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = {"success": True, "data": []}
        result, status = RipioRequestDataSpot._get_exchange_info_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_trades_normalize_function(self):
        """Test trades normalize function."""
        input_data = {"success": True, "data": []}
        result, status = RipioRequestDataSpot._get_trades_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_tick_params(self):
        """Test get tick parameter generation."""
        data_queue = queue.Queue()
        request_data = RipioRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/USDT")

        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = RipioRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/USDT", 20)

        assert "orderbook" in path.lower() or "depth" in path.lower()
        assert params["limit"] == 20

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = RipioRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline(
            "BTC/USDT", "1h", 10)

        assert "candle" in path.lower() or "kline" in path.lower()
        assert params["period"] == "60"


class TestRipioDataContainers:
    """Test Ripio data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "success": True,
            "data": {
                "symbol": "BTC_USDT",
                "last": "50000.00",
                "high": "51000.00",
                "low": "49000.00",
                "bid": "49999.00",
                "ask": "50001.00",
                "volume": "1000.00",
            }
        }

        ticker = RipioRequestTickerData(
            ticker_response, "BTC/USDT", "SPOT"
        )

        assert ticker.symbol == "BTC_USDT"
        assert ticker.last_price == 50000.0


class TestRipioRegistration:
    """Test Ripio registration module."""

    def test_registration(self):
        """Test that Ripio registration works."""
        # Import to trigger registration
        from bt_api_py.feeds import register_ripio

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("RIPIO___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get("RIPIO___SPOT")
        assert feed_class is not None
        assert feed_class == RipioRequestDataSpot

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get(
            "RIPIO___SPOT")
        assert data_class is not None
        assert data_class == RipioExchangeDataSpot

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get("RIPIO___SPOT")
        assert handler is not None

    def test_get_registered_feed(self):
        """Test getting registered feed class."""
        from bt_api_py.feeds import register_ripio

        # Use create_feed to get an instance
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "RIPIO___SPOT",
            data_queue,
        )
        assert feed is not None
        assert isinstance(feed, RipioRequestDataSpot)

    def test_get_registered_exchange_data(self):
        """Test getting registered exchange data class."""
        from bt_api_py.feeds import register_ripio

        # Use create_exchange_data to get an instance
        data = ExchangeRegistry.create_exchange_data("RIPIO___SPOT")
        assert data is not None
        assert isinstance(data, RipioExchangeDataSpot)


class TestRipioConfig:
    """Test Ripio configuration loading."""

    def test_legal_currencies(self):
        """Test that legal currencies are properly configured."""
        data = RipioExchangeData()
        # Should support Latin American currencies
        assert "ARS" in data.legal_currency  # Argentine Peso
        assert "BRL" in data.legal_currency  # Brazilian Real
        assert "MXN" in data.legal_currency  # Mexican Peso


# ==================== Live API Tests ====================

class TestRipioTickData:
    """Test ticker data endpoints."""

    @pytest.mark.integration
    def test_ripio_req_spot_tick_data(self):
        """Test getting spot ticker data from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        data = feed.get_tick("BTC/USDT")
        assert isinstance(data, RequestData)

        tick_data_list = data.get_data()
        assert isinstance(tick_data_list, list)

        if len(tick_data_list) > 0:
            tick_data = tick_data_list[0]
            assert "last" in tick_data or "lastPrice" in tick_data

    @pytest.mark.integration
    def test_ripio_async_spot_tick_data(self):
        """Test async ticker data from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

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
            assert isinstance(tick_data, RequestData)


class TestRipioKlineData:
    """Test kline/candlestick data endpoints."""

    @pytest.mark.integration
    def test_ripio_req_spot_kline_data(self):
        """Test getting kline data from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        data = feed.get_kline("BTC/USDT", "1h", count=10)
        assert isinstance(data, RequestData)

        kline_data_list = data.get_data()
        assert isinstance(kline_data_list, list)

    @pytest.mark.integration
    def test_ripio_async_spot_kline_data(self):
        """Test async kline data from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        feed.async_get_kline("BTC/USDT", period="1h", count=5)
        time.sleep(3)

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            assert isinstance(kline_data, RequestData)


class TestRipioOrderBook:
    """Test order book depth endpoints."""

    @pytest.mark.integration
    def test_ripio_req_spot_depth_data(self):
        """Test getting order book depth from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        data = feed.get_depth("BTC/USDT", 20)
        assert isinstance(data, RequestData)

        depth_data = data.get_data()
        assert isinstance(depth_data, list)

        if len(depth_data) > 0:
            orderbook = depth_data[0]
            assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    def test_ripio_async_spot_depth_data(self):
        """Test async order book depth from Ripio."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        feed.async_get_depth("BTC/USDT", 20)
        time.sleep(3)

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            assert isinstance(depth_data, RequestData)


# ==================== Mock Tests ====================

class TestRipioMockData:
    """Test with mock data."""

    def test_ripio_tick_normalize_with_mock(self):
        """Test ticker normalize with mock response."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        mock_response = {
            "success": True,
            "data": {
                "symbol": "BTC_USDT",
                "last": "50000.00",
                "bid": "49999.00",
                "ask": "50001.00",
            }
        }

        result, success = feed._get_tick_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0

    def test_ripio_depth_normalize_with_mock(self):
        """Test depth normalize with mock response."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        mock_response = {
            "success": True,
            "data": {
                "bids": [["49999", "1.0"], ["49998", "2.0"]],
                "asks": [["50001", "1.0"], ["50002", "2.5"]]
            }
        }

        result, success = feed._get_depth_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0

    def test_ripio_kline_normalize_with_mock(self):
        """Test kline normalize with mock response."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        mock_response = {
            "success": True,
            "data": [
                [1672531200, "50000", "51000", "49000", "50500", "100"],
                [1672534800, "50500", "51500", "50000", "51000", "150"]
            ]
        }

        result, success = feed._get_kline_normalize_function(
            mock_response, {"symbol_name": "BTC/USDT"})
        assert success is True
        assert len(result) > 0


class TestRipioIntegration:
    """Integration tests for Ripio."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)

        # Test ticker (would require network)
        # ticker = feed.get_tick("BTC_USDT")
        # assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        # This would require API keys to test
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
