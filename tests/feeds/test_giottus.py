"""
Test Giottus exchange integration.

Run tests:
    pytest tests/feeds/test_giottus.py -v

Run with coverage:
    pytest tests/feeds/test_giottus.py --cov=bt_api_py.feeds.live_giottus --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeDataSpot
from bt_api_py.containers.tickers.giottus_ticker import GiottusRequestTickerData
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.feeds.live_giottus.spot import GiottusRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Giottus
import bt_api_py.feeds.register_giottus  # noqa: F401


class TestGiottusExchangeData:
    """Test Giottus exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Giottus spot exchange data."""
        exchange_data = GiottusExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert exchange_data.asset_type == "spot"

    def test_rest_url(self):
        """Test REST URL configuration."""
        exchange_data = GiottusExchangeDataSpot()
        assert "giottus.com" in exchange_data.rest_url

    def test_wss_url(self):
        """Test WebSocket URL configuration."""
        exchange_data = GiottusExchangeDataSpot()
        assert exchange_data.wss_url
        assert "wss://" in exchange_data.wss_url

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = GiottusExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = GiottusExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "public_key": "test_key",
        "private_key": "test_secret",
    }
    return GiottusRequestDataSpot(data_queue, **kwargs)


class TestGiottusRequestDataSpot:
    """Test Giottus spot REST API request class."""

    def test_request_data_creation(self):
        """Test creating Giottus request data."""
        data_queue = queue.Queue()
        request_data = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="GIOTTUS___SPOT",
        )
        assert request_data.exchange_name == "GIOTTUS___SPOT"

    def test_get_tick_params(self):
        """Test get ticker parameter generation."""
        data_queue = queue.Queue()
        request_data = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._get_tick("BTC-INR")
        assert path is not None
        assert params is not None

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._get_depth("BTC-INR", count=20)
        assert path is not None
        assert params is not None

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._get_kline("BTC-INR", period="1h", count=20)
        assert path is not None
        assert params is not None


class TestGiottusServerTime:
    """Test server time endpoint."""

    def test_get_server_time(self):
        """Test getting server time."""
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None


class TestGiottusTicker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "success": True,
            "data": {
            "symbol": "BTCINR",
            "last": "50000",
            "bid": "49999",
            "ask": "50001",
            "volume": "1000",
            "high": "51000",
            "low": "49000",
            },
        }

        ticker = GiottusRequestTickerData(
            ticker_response, symbol_name="BTC-INR", asset_type="SPOT",
            has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "GIOTTUS"
        assert ticker.get_symbol_name() == "BTC-INR"

    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        feed = init_req_feed()
        result = feed.get_tick("BTC-INR")
        assert result.status is True


class TestGiottusKline:
    """Test kline data retrieval."""

    def test_kline_container(self):
        """Test kline data container."""
        pass

    def test_get_kline_live(self):
        """Test getting kline from live API."""
        feed = init_req_feed()
        result = feed.get_kline("BTC-INR", "1h", count=10)
        assert result is not None


class TestGiottusOrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        pass

    def test_get_depth_live(self):
        """Test getting order book from live API."""
        feed = init_req_feed()
        result = feed.get_depth("BTC-INR", count=20)
        assert result is not None


class TestGiottusRegistry:
    """Test Giottus registration."""

    def test_giottus_registered(self):
        """Test that Giottus is properly registered."""
        assert "GIOTTUS___SPOT" in ExchangeRegistry._feed_classes
        assert "GIOTTUS___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_giottus_create_feed(self):
        """Test creating Giottus feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "GIOTTUS___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, GiottusRequestDataSpot)

    def test_giottus_create_exchange_data(self):
        """Test creating Giottus exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("GIOTTUS___SPOT")
        assert isinstance(exchange_data, GiottusExchangeDataSpot)


class TestGiottusIntegration:
    """Integration tests for Giottus."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC-INR")
        assert ticker.status is True

        # Test depth
        depth = feed.get_depth("BTC-INR", count=20)
        assert depth.status is True

        # Test kline
        kline = feed.get_kline("BTC-INR", "1h", count=10)
        assert kline.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
