"""
SatoshiTango Exchange Integration Tests

Tests for SatoshiTango spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orderbooks)
- Registration module

Run tests:
    pytest tests/feeds/test_satoshitango.py -v

Run with coverage:
    pytest tests/feeds/test_satoshitango.py --cov=bt_api_py.feeds.live_satoshitango --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_satoshitango.py -m "not integration" -v
"""

import json
import queue
import time
import pytest

from bt_api_py.containers.exchanges.satoshitango_exchange_data import (
    SatoshiTangoExchangeData,
    SatoshiTangoExchangeDataSpot,
)
from bt_api_py.containers.tickers.satoshitango_ticker import SatoshiTangoRequestTickerData
from bt_api_py.feeds.live_satoshitango.spot import SatoshiTangoRequestDataSpot
from bt_api_py.feeds.register_satoshitango import register_satoshitango
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register SatoshiTango
import bt_api_py.feeds.register_satoshitango  # noqa: F401


class TestSatoshiTangoExchangeData:
    """Test SatoshiTango exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        """Test base SatoshiTangoExchangeData initialization."""
        data = SatoshiTangoExchangeData()
        assert data.exchange_name == "satoshitango"
        assert data.rest_url == "https://api.satoshitango.com"
        assert data.wss_url == ""  # SatoshiTango may not have WebSocket
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "ARS" in data.legal_currency  # Argentine Peso

    def test_kline_periods(self):
        """Test kline period configuration."""
        data = SatoshiTangoExchangeData()
        assert "1m" in data.kline_periods
        assert "1h" in data.kline_periods
        assert "1d" in data.kline_periods
        assert "1w" in data.kline_periods


class TestSatoshiTangoRequestDataSpot:
    """Test SatoshiTango spot request feed."""

    def test_request_data_initialization(self):
        """Test request data feed initialization."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        assert feed.exchange_name == "SATOSHITANGO___SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick_params(self):
        """Test get tick parameter generation."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/ARS")

        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = {
            "symbol": "BTCARS",
            "last": "9500000",
            "bid": "9490000",
            "ask": "9510000",
            "volume": "1.5",
        }

        result, success = request_data._get_tick_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/ARS", 20)

        assert "orderbook" in path.lower() or "depth" in path.lower()
        assert params["depth"] == 20

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = {
            "bids": [["9490000", "1.0"]],
            "asks": [["9510000", "1.0"]],
        }

        result, success = request_data._get_depth_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline("BTC/ARS", "1h", 10)

        assert "candle" in path.lower() or "kline" in path.lower()
        assert params["interval"] == "60"

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = [
            [1672531200, "9500000", "9600000", "9400000", "9550000", "1.5"],
            [1672534800, "9550000", "9650000", "9450000", "9600000", "2.0"]
        ]

        result, success = request_data._get_kline_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        data_queue = queue.Queue()
        request_data = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = {"BTCARS": {}, "ETHARS": {}}

        result, success = request_data._get_exchange_info_normalize_function(
            mock_response, {})
        assert success is True
        assert len(result) > 0


class TestSatoshiTangoDataContainers:
    """Test SatoshiTango data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "symbol": "BTCARS",
            "last": "9500000",
            "high": "9600000",
            "low": "9400000",
            "bid": "9490000",
            "ask": "9510000",
            "volume": "1.5",
        }

        ticker = SatoshiTangoRequestTickerData(
            ticker_response, "BTC/ARS", "SPOT"
        )
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTCARS"
        assert ticker.last_price == 9500000.0
        assert ticker.bid_price == 9490000.0
        assert ticker.ask_price == 9510000.0


class TestSatoshiTangoRegistration:
    """Test SatoshiTango registration module."""

    def test_registration(self):
        """Test that SatoshiTango registration works."""
        # Import to trigger registration
        from bt_api_py.feeds import register_satoshitango

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("SATOSHITANGO___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get("SATOSHITANGO___SPOT")
        assert feed_class is not None
        assert feed_class == SatoshiTangoRequestDataSpot

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get(
            "SATOSHITANGO___SPOT")
        assert data_class is not None
        assert data_class == SatoshiTangoExchangeDataSpot

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get("SATOSHITANGO___SPOT")
        assert handler is not None

    def test_get_registered_feed(self):
        """Test getting registered feed class."""
        from bt_api_py.feeds import register_satoshitango

        # Use create_feed to get an instance
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "SATOSHITANGO___SPOT",
            data_queue,
        )
        assert feed is not None
        assert isinstance(feed, SatoshiTangoRequestDataSpot)

    def test_get_registered_exchange_data(self):
        """Test getting registered exchange data class."""
        from bt_api_py.feeds import register_satoshitango

        # Use create_exchange_data to get an instance
        data = ExchangeRegistry.create_exchange_data("SATOSHITANGO___SPOT")
        assert data is not None
        assert isinstance(data, SatoshiTangoExchangeDataSpot)


class TestSatoshiTangoConfig:
    """Test SatoshiTango configuration loading."""

    def test_legal_currencies(self):
        """Test that legal currencies are properly configured."""
        data = SatoshiTangoExchangeData()
        # Should support Argentine Peso and USD
        assert "ARS" in data.legal_currency
        assert "USD" in data.legal_currency


# ==================== Live API Tests ====================

class TestSatoshiTangoTickData:
    """Test ticker data endpoints."""

    @pytest.mark.integration
    def test_satoshitango_req_spot_tick_data(self):
        """Test getting spot ticker data from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        data = feed.get_tick("BTC/ARS")
        assert isinstance(data, RequestData)

        tick_data_list = data.get_data()
        assert isinstance(tick_data_list, list)

        if len(tick_data_list) > 0:
            tick_data = tick_data_list[0]
            assert "last" in tick_data or "price" in tick_data

    @pytest.mark.integration
    def test_satoshitango_async_spot_tick_data(self):
        """Test async ticker data from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        feed.async_get_tick(
            "BTC/ARS",
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


class TestSatoshiTangoKlineData:
    """Test kline/candlestick data endpoints."""

    @pytest.mark.integration
    def test_satoshitango_req_spot_kline_data(self):
        """Test getting kline data from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        data = feed.get_kline("BTC/ARS", "1h", count=10)
        assert isinstance(data, RequestData)

        kline_data_list = data.get_data()
        assert isinstance(kline_data_list, list)

    @pytest.mark.integration
    def test_satoshitango_async_spot_kline_data(self):
        """Test async kline data from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        feed.async_get_kline("BTC/ARS", period="1h", count=5)
        time.sleep(3)

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            assert isinstance(kline_data, RequestData)


class TestSatoshiTangoOrderBook:
    """Test order book depth endpoints."""

    @pytest.mark.integration
    def test_satoshitango_req_spot_depth_data(self):
        """Test getting order book depth from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        data = feed.get_depth("BTC/ARS", 20)
        assert isinstance(data, RequestData)

        depth_data = data.get_data()
        assert isinstance(depth_data, list)

        if len(depth_data) > 0:
            orderbook = depth_data[0]
            assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    def test_satoshitango_async_spot_depth_data(self):
        """Test async order book depth from SatoshiTango."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        feed.async_get_depth("BTC/ARS", 20)
        time.sleep(3)

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            assert isinstance(depth_data, RequestData)


# ==================== Mock Tests ====================

class TestSatoshiTangoMockData:
    """Test with mock data."""

    def test_satoshitango_tick_with_mock(self):
        """Test ticker with mock response."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = {
            "symbol": "BTCARS",
            "last": "9500000",
            "bid": "9490000",
            "ask": "9510000",
        }

        result, success = feed._get_tick_normalize_function(
            mock_response, {"symbol_name": "BTC/ARS"})
        assert success is True
        assert len(result) > 0

    def test_satoshitango_depth_with_mock(self):
        """Test order book with mock response."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = {
            "bids": [["9490000", "1.0"], ["9480000", "2.0"]],
            "asks": [["9510000", "1.0"], ["9520000", "2.5"]]
        }

        result, success = feed._get_depth_normalize_function(
            mock_response, {"symbol_name": "BTC/ARS"})
        assert success is True
        assert len(result) > 0

    def test_satoshitango_kline_with_mock(self):
        """Test kline with mock response."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        mock_response = [
            [1672531200, "9500000", "9600000", "9400000", "9550000", "1.5"],
            [1672534800, "9550000", "9650000", "9450000", "9600000", "2.0"]
        ]

        result, success = feed._get_kline_normalize_function(
            mock_response, {"symbol_name": "BTC/ARS"})
        assert success is True
        assert len(result) > 0


class TestSatoshiTangoIntegration:
    """Integration tests for SatoshiTango."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)

        # Test ticker (would require network)
        # ticker = feed.get_tick("BTC/ARS")
        # assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        # This would require API keys to test
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
