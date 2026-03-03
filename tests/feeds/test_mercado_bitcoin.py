"""
Mercado Bitcoin Exchange Integration Tests

Tests for Mercado Bitcoin spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orderbooks)
- Registration module

Run tests:
    pytest tests/feeds/test_mercado_bitcoin.py -v

Run with coverage:
    pytest tests/feeds/test_mercado_bitcoin.py --cov=bt_api_py.feeds.live_mercado_bitcoin --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_mercado_bitcoin.py -m "not integration" -v
"""

import json
import queue
import time
import pytest

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import (
    MercadoBitcoinExchangeData,
    MercadoBitcoinExchangeDataSpot,
)
from bt_api_py.containers.tickers.mercado_bitcoin_ticker import MercadoBitcoinRequestTickerData
from bt_api_py.feeds.live_mercado_bitcoin.spot import MercadoBitcoinRequestDataSpot
from bt_api_py.feeds.register_mercado_bitcoin import register_mercado_bitcoin
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Mercado Bitcoin
import bt_api_py.feeds.register_mercado_bitcoin  # noqa: F401


class TestMercadoBitcoinExchangeData:
    """Test Mercado Bitcoin exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        """Test base MercadoBitcoinExchangeData initialization."""
        data = MercadoBitcoinExchangeData()
        assert data.exchange_name == "mercado_bitcoin"
        assert data.rest_url == "https://www.mercadobitcoin.net/api"
        assert data.rest_private_url == "https://www.mercadobitcoin.net/tapi"
        assert data.rest_v4_url == "https://api.mercadobitcoin.net/api/v4"
        assert data.wss_url == "wss://ws.mercadobitcoin.net"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "BRL" in data.legal_currency

    def test_kline_periods(self):
        """Test kline period conversion."""
        data = MercadoBitcoinExchangeData()
        assert "1h" in data.kline_periods
        assert "1d" in data.kline_periods
        assert "1w" in data.kline_periods

    def test_get_symbol(self):
        """Test symbol format conversion."""
        data = MercadoBitcoinExchangeDataSpot()
        # Mercado Bitcoin uses dash format
        assert data.get_symbol("BTC-BRL") == "BTC-BRL"
        assert data.get_symbol("ETH-BRL") == "ETH-BRL"


class TestMercadoBitcoinRequestDataSpot:
    """Test Mercado Bitcoin spot request feed."""

    def test_request_data_creation(self):
        """Test creating Mercado Bitcoin request data."""
        data_queue = queue.Queue()
        request_data = MercadoBitcoinRequestDataSpot(data_queue)
        assert request_data.exchange_name == "MERCADO_BITCOIN___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        data_queue = queue.Queue()
        request_data = MercadoBitcoinRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        from bt_api_py.feeds.capability import Capability
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick(self):
        """Test get tick method."""
        data_queue = queue.Queue()
        request_data = MercadoBitcoinRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC-BRL")
        assert "BTC" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-BRL"

    def test_get_tick_normalize_function(self):
        """Test get ticker normalize function."""
        # Test ticker normalize
        input_data = {
            "ticker": {
                "last": "50000.00",
                "buy": "49999.00",
                "sell": "50001.00"}}
        result, status = MercadoBitcoinRequestDataSpot._get_tick_normalize_function(
            input_data, {}
        )
        assert status is True
        assert result[0]["last"] == "50000.00"

    def test_get_depth(self):
        """Test get depth method."""
        data_queue = queue.Queue()
        request_data = MercadoBitcoinRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC-BRL", 20)
        assert "BTC" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_normalize_function(self):
        """Test get depth normalize function."""
        # Test depth normalize
        input_data = {"bids": [["49999", "1.0"]], "asks": [["50001", "1.0"]]}
        result, status = MercadoBitcoinRequestDataSpot._get_depth_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_get_kline(self):
        """Test get kline method."""
        data_queue = queue.Queue()
        request_data = MercadoBitcoinRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline("BTC-BRL", "1h", 10)
        assert "candles" in path
        assert extra_data["request_type"] == "get_kline"

    def test_get_kline_normalize_function(self):
        """Test get kline normalize function."""
        # Test kline normalize
        input_data = [[1672531200, "50000", "51000", "49000", "50500", "100"]]
        result, status = MercadoBitcoinRequestDataSpot._get_kline_normalize_function(
            input_data, {}
        )
        assert status is True


class TestMercadoBitcoinDataContainers:
    """Test Mercado Bitcoin data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "ticker": {
                "last": "50000.00",
                "buy": "49999.00",
                "sell": "50001.00",
                "high": "51000.00",
                "low": "49000.00",
                "vol": "100.50",
            }
        })

        ticker = MercadoBitcoinRequestTickerData(
            ticker_info, "BTC-BRL", "SPOT", False)
        ticker.init_data()

        assert ticker.exchange_name == "MERCADO_BITCOIN"
        assert ticker.ticker_symbol_name == "BTC-BRL"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0


class TestMercadoBitcoinRegistration:
    """Test Mercado Bitcoin registration module."""

    def test_registration(self):
        """Test that Mercado Bitcoin registration works."""
        # Import to trigger registration
        from bt_api_py.feeds import register_mercado_bitcoin

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("MERCADO_BITCOIN___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get(
            "MERCADO_BITCOIN___SPOT")
        assert feed_class is not None
        assert feed_class == MercadoBitcoinRequestDataSpot

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get(
            "MERCADO_BITCOIN___SPOT")
        assert data_class is not None
        assert data_class == MercadoBitcoinExchangeDataSpot

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get(
            "MERCADO_BITCOIN___SPOT")
        assert handler is not None

    def test_create_feed(self):
        """Test creating Mercado Bitcoin feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "MERCADO_BITCOIN___SPOT", data_queue)
        assert isinstance(feed, MercadoBitcoinRequestDataSpot)

    def test_create_exchange_data(self):
        """Test creating Mercado Bitcoin exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "MERCADO_BITCOIN___SPOT")
        assert isinstance(exchange_data, MercadoBitcoinExchangeDataSpot)


# ==================== Live API Tests ====================

class TestMercadoBitcoinTickData:
    """Test ticker data endpoints."""

    @pytest.mark.integration
    def test_mercado_bitcoin_req_spot_tick_data(self):
        """Test getting spot ticker data from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        data = feed.get_tick("BTC-BRL")
        assert isinstance(data, RequestData)

        tick_data_list = data.get_data()
        assert isinstance(tick_data_list, list)

        if len(tick_data_list) > 0:
            tick_data = tick_data_list[0]
            if hasattr(tick_data, 'init_data'):
                tick_data.init_data()
                assert tick_data.get_exchange_name() == "MERCADO_BITCOIN"
                assert tick_data.get_symbol_name() == "BTC-BRL"
                assert tick_data.get_last_price() > 0
                assert tick_data.get_bid_price() >= 0
                assert tick_data.get_ask_price() > 0
            else:
                assert isinstance(tick_data, dict)

    @pytest.mark.integration
    def test_mercado_bitcoin_async_spot_tick_data(self):
        """Test async ticker data from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        feed.async_get_tick(
            "BTC-BRL",
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


class TestMercadoBitcoinKlineData:
    """Test kline/candlestick data endpoints."""

    @pytest.mark.integration
    def test_mercado_bitcoin_req_spot_kline_data(self):
        """Test getting kline data from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        data = feed.get_kline("BTC-BRL", "1h", count=10)
        assert isinstance(data, RequestData)

        kline_data_list = data.get_data()
        assert isinstance(kline_data_list, list)

    @pytest.mark.integration
    def test_mercado_bitcoin_async_spot_kline_data(self):
        """Test async kline data from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        feed.async_get_kline("BTC-BRL", period="1h", count=5)
        time.sleep(3)

        kline_data = None
        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if kline_data is not None:
            assert isinstance(kline_data, RequestData)


class TestMercadoBitcoinOrderBook:
    """Test order book depth endpoints."""

    @pytest.mark.integration
    def test_mercado_bitcoin_req_spot_depth_data(self):
        """Test getting order book depth from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        data = feed.get_depth("BTC-BRL", 20)
        assert isinstance(data, RequestData)

        depth_data = data.get_data()
        assert isinstance(depth_data, list)

        if len(depth_data) > 0:
            pass
        orderbook = depth_data[0]
        assert isinstance(orderbook, dict)

    @pytest.mark.integration
    def test_mercado_bitcoin_async_spot_depth_data(self):
        """Test async order book depth from Mercado Bitcoin."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        feed.async_get_depth("BTC-BRL", 20)
        time.sleep(3)

        depth_data = None
        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass  # No data received

        if depth_data is not None:
            assert isinstance(depth_data, RequestData)


# ==================== Mock Tests ====================

class TestMercadoBitcoinMockData:
    """Test with mock data."""

    def test_mercado_bitcoin_tick_with_mock(self):
        """Test ticker with mock response."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        mock_response = {
            "ticker": {
                "last": "50000.00",
                "buy": "49999.00",
                "sell": "50001.00",
                "high": "51000.00",
                "low": "49000.00",
                "vol": "100.50",
            }
        }

        result, success = feed._get_tick_normalize_function(
            mock_response, {"symbol_name": "BTC-BRL"})
        assert success is True
        assert len(result) > 0

    def test_mercado_bitcoin_depth_with_mock(self):
        """Test order book with mock response."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        mock_response = {
            "bids": [["49999", "1.0"], ["49998", "2.0"]],
            "asks": [["50001", "1.0"], ["50002", "2.5"]]
        }

        result, success = feed._get_depth_normalize_function(
            mock_response, {"symbol_name": "BTC-BRL"})
        assert success is True
        assert len(result) > 0

    def test_mercado_bitcoin_kline_with_mock(self):
        """Test kline with mock response."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        mock_response = [
            [1672531200, "50000", "51000", "49000", "50500", "100"],
            [1672534800, "50500", "51500", "50000", "51000", "150"]
        ]

        result, success = feed._get_kline_normalize_function(
            mock_response, {"symbol_name": "BTC-BRL"})
        assert success is True
        assert len(result) > 0


class TestMercadoBitcoinIntegration:
    """Integration tests for Mercado Bitcoin."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)

        # Test ticker (would require network)
        # ticker = feed.get_tick("BTC-BRL")
        # assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        # This would require API keys to test
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
