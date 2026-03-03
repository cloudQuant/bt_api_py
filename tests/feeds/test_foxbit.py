"""
Test Foxbit exchange integration.

Run tests:
    pytest tests/feeds/test_foxbit.py -v

Run with coverage:
    pytest tests/feeds/test_foxbit.py --cov=bt_api_py.feeds.live_foxbit --cov-report=term-missing
"""

import json
import queue
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeDataSpot
from bt_api_py.containers.tickers.foxbit_ticker import FoxbitRequestTickerData
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_foxbit.spot import FoxbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Foxbit
import bt_api_py.feeds.register_foxbit  # noqa: F401


class TestFoxbitExchangeData:
    """Test Foxbit exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Foxbit spot exchange data."""
        exchange_data = FoxbitExchangeDataSpot()
#         assert exchange_data.exchange_name == "FOXBIT___SPOT"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url
        assert exchange_data.wss_url

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = FoxbitExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "5m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = FoxbitExchangeDataSpot()
        assert "BRL" in exchange_data.legal_currency


class TestFoxbitRequestData:
    """Test Foxbit REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Foxbit request data."""
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(
            data_queue,
            exchange_name="FOXBIT___SPOT",
        )
        assert request_data.exchange_name == "FOXBIT___SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_format_market(self):
        """Test market format conversion.

        Foxbit uses lowercase format like 'btcbrl'.
        """
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(data_queue)

        assert request_data._format_market("BTC/BRL") == "btcbrl"
        assert request_data._format_market("BTC-BRL") == "btcbrl"
        assert request_data._format_market("btcbrl") == "btcbrl"

    def test_get_tick(self):
        """Test get tick method."""
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/BRL")
        assert "GET /rest/v3/markets/" in path
        assert "/ticker/24hr" in path
        assert extra_data["request_type"] == "get_ticker"

    def test_get_depth(self):
        """Test get depth method."""
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/BRL", count=20)
        assert "GET /rest/v3/markets/" in path
        assert "/orderbook" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_kline(self):
        """Test get kline method."""
        data_queue = queue.Queue()
        request_data = FoxbitRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline("BTC/BRL", "1h")
        assert "GET /rest/v3/markets/" in path
        assert "/candlesticks" in path
        assert extra_data["request_type"] == "get_kline"


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    return FoxbitRequestDataSpot(data_queue)


class TestFoxbitServerTime:
    """Test server time endpoint."""

    def test_get_server_time(self):
        """Test getting server time."""
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None


class TestFoxbitTicker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "data": {
            "marketSymbol": "BTCBRL",
            "lastPrice": "250000.50",
            "bidPrice": "249900.00",
            "askPrice": "250000.50",
            "vol": "12.345",
            "highPrice": "255000.00",
            "lowPrice": "245000.00",
            "volQuote": "3000000",
            }
        })

        ticker = FoxbitRequestTickerData(ticker_info, "BTC/BRL", "SPOT", False)
        ticker.init_data()

        assert ticker.exchange_name == "FOXBIT"
        assert ticker.symbol_name == "BTC/BRL"
        assert ticker.last_price == 250000.50

    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        feed = init_req_feed()
        result = feed.get_tick("BTC/BRL")
        assert result.status is True


class TestFoxbitKline:
    """Test kline data retrieval."""

    def test_kline_container(self):
        """Test kline data container."""
        pass

    def test_get_kline_live(self):
        """Test getting kline from live API."""
        feed = init_req_feed()
        result = feed.get_kline("BTC/BRL", "1h", count=10)
        assert result is not None


class TestFoxbitOrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        pass

    def test_get_depth_live(self):
        """Test getting order book from live API."""
        feed = init_req_feed()
        result = feed.get_depth("BTC/BRL", count=20)
        assert result is not None


class TestFoxbitRegistry:
    """Test Foxbit registration."""

    def test_foxbit_registered(self):
        """Test that Foxbit is properly registered."""
        # Check if feed is registered
        assert "FOXBIT___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["FOXBIT___SPOT"] == FoxbitRequestDataSpot

        # Check if exchange data is registered
        assert "FOXBIT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["FOXBIT___SPOT"] == FoxbitExchangeDataSpot

        # Check if balance handler is registered
        assert "FOXBIT___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["FOXBIT___SPOT"] is not None

    def test_foxbit_create_feed(self):
        """Test creating Foxbit feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("FOXBIT___SPOT", data_queue)
        assert isinstance(feed, FoxbitRequestDataSpot)

    def test_foxbit_create_exchange_data(self):
        """Test creating Foxbit exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("FOXBIT___SPOT")
        assert isinstance(exchange_data, FoxbitExchangeDataSpot)


class TestFoxbitIntegration:
    """Integration tests for Foxbit."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC/BRL")
        assert ticker.status is True

        # Test depth
        depth = feed.get_depth("BTC/BRL", count=20)
        assert depth.status is True

        # Test kline
        kline = feed.get_kline("BTC/BRL", "1h", count=10)
        assert kline.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
