"""
Test EXMO exchange integration.

Run tests:
    pytest tests/feeds/test_exmo.py -v

Run with coverage:
    pytest tests/feeds/test_exmo.py --cov=bt_api_py.feeds.live_exmo --cov-report=term-missing
"""

import json
import queue
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeDataSpot
from bt_api_py.containers.tickers.exmo_ticker import ExmoRequestTickerData
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.feeds.live_exmo.spot import ExmoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register EXMO
import bt_api_py.feeds.register_exmo  # noqa: F401


class TestExmoExchangeData:
    """Test EXMO exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating EXMO spot exchange data."""
        exchange_data = ExmoExchangeDataSpot()
#         assert exchange_data.exchange_name == "EXMO___SPOT"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url
        assert exchange_data.wss_url

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = ExmoExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "5m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = ExmoExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestExmoRequestData:
    """Test EXMO REST API request base class."""

    def test_request_data_creation(self):
        """Test creating EXMO request data."""
        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(
            data_queue,
            exchange_name="EXMO___SPOT",
        )
        assert request_data.exchange_name == "EXMO___SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick(self):
        """Test get tick method."""
        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/USDT")
        assert path == "GET /ticker"
        assert extra_data["request_type"] == "get_ticker"
        assert extra_data["symbol_name"] == "BTC/USDT"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(data_queue)

        input_data = {
            "BTC_USDT": {
            "buy_price": "50000.0",
            "sell_price": "50100.0",
            "last_trade": "50050.0",
            "high": "51000.0",
            "low": "48000.0",
            "vol": "1234.56",
            }
        }
        extra_data = {"symbol_name": "BTC/USDT"}

        result, success = request_data._get_tick_normalize_function(input_data, extra_data)
        assert success is True
        assert len(result) > 0

    def test_get_depth(self):
        """Test get depth method."""
        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/USDT", count=20)
        assert path == "GET /order_book"
        assert extra_data["request_type"] == "get_depth"

    def test_get_kline(self):
        """Test get kline method."""
        data_queue = queue.Queue()
        request_data = ExmoRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_kline("BTC/USDT", "1h")
        assert path == "GET /candles_history"
        assert extra_data["request_type"] == "get_kline"


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    return ExmoRequestDataSpot(data_queue)


class TestExmoServerTime:
    """Test server time endpoint."""

    def test_get_server_time(self):
        """Test getting server time."""
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None


class TestExmoTicker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "buy_price": "50000.0",
            "sell_price": "50100.0",
            "last_trade": "50050.0",
            "high": "51000.0",
            "low": "48000.0",
            "vol": "1234.56",
            "avg": "50000.0",
        })

        ticker = ExmoRequestTickerData(ticker_info, "BTC/USDT", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "EXMO"
        assert ticker.get_symbol_name() == "BTC/USDT"
        assert ticker.get_last_price() > 0

    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        feed = init_req_feed()
        result = feed.get_tick("BTC/USDT")
        assert result.status is True


class TestExmoKline:
    """Test kline data retrieval."""

    def test_kline_container(self):
        """Test kline data container."""
        pass

    def test_get_kline_live(self):
        """Test getting kline from live API."""
        feed = init_req_feed()
        result = feed.get_kline("BTC/USDT", "1h", count=10)
        assert result is not None


class TestExmoOrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        pass

    def test_get_depth_live(self):
        """Test getting order book from live API."""
        feed = init_req_feed()
        result = feed.get_depth("BTC/USDT", count=20)
        assert result is not None


class TestExmoRegistry:
    """Test EXMO registration."""

    def test_exmo_registered(self):
        """Test that EXMO is properly registered."""
        # Check if feed is registered
        assert "EXMO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["EXMO___SPOT"] == ExmoRequestDataSpot

        # Check if exchange data is registered
        assert "EXMO___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["EXMO___SPOT"] == ExmoExchangeDataSpot

        # Check if balance handler is registered
        assert "EXMO___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["EXMO___SPOT"] is not None

    def test_exmo_create_feed(self):
        """Test creating EXMO feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("EXMO___SPOT", data_queue)
        assert isinstance(feed, ExmoRequestDataSpot)

    def test_exmo_create_exchange_data(self):
        """Test creating EXMO exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("EXMO___SPOT")
        assert isinstance(exchange_data, ExmoExchangeDataSpot)


class TestExmoIntegration:
    """Integration tests for EXMO."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC/USDT")
        assert ticker.status is True

        # Test depth
        depth = feed.get_depth("BTC/USDT", count=20)
        assert depth.status is True

        # Test kline
        kline = feed.get_kline("BTC/USDT", "1h", count=10)
        assert kline.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
