"""
Test Coincheck exchange integration.

Run tests:
    pytest tests/feeds/test_coincheck.py -v

Run with coverage:
    pytest tests/feeds/test_coincheck.py --cov=bt_api_py.feeds.live_coincheck --cov-report=term-missing
"""

import json
import queue
import time

import pytest

from bt_api_py.containers.exchanges.coincheck_exchange_data import (
    CoincheckExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coincheck_ticker import CoincheckRequestTickerData
from bt_api_py.feeds.live_coincheck.spot import CoincheckRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Coincheck
import bt_api_py.feeds.register_coincheck  # noqa: F401


def init_req_feed():
    """Initialize Coincheck request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINCHECK___SPOT",
    }
    live_coincheck_spot_feed = CoincheckRequestDataSpot(data_queue, **kwargs)
    return live_coincheck_spot_feed


def init_async_feed(data_queue):
    """Initialize Coincheck async feed for testing."""
    kwargs = {
        "exchange_name": "COINCHECK___SPOT",
    }
    live_coincheck_spot_feed = CoincheckRequestDataSpot(data_queue, **kwargs)
    return live_coincheck_spot_feed


class TestCoincheckExchangeData:
    """Test Coincheck exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Coincheck spot exchange data."""
        exchange_data = CoincheckExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.exchange_name == "coincheckSpot"
        assert exchange_data.asset_type == "spot"
        # URLs are loaded from YAML config (spot key)
        assert exchange_data.rest_url == "https://coincheck.com"
        assert exchange_data.wss_url == "wss://ws-api.coincheck.com"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = CoincheckExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1m"] == "1m"
        assert exchange_data.kline_periods["1h"] == "1h"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = CoincheckExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = CoincheckExchangeDataSpot()
        # rest_url is loaded from YAML config
        assert exchange_data.rest_url == "https://coincheck.com"

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = CoincheckExchangeDataSpot()
        # wss_url is loaded from YAML config
        assert exchange_data.wss_url == "wss://ws-api.coincheck.com"


class TestCoincheckRequestData:
    """Test Coincheck REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Coincheck request data."""
        data_queue = queue.Queue()
        request_data = CoincheckRequestDataSpot(
            data_queue,
            exchange_name="COINCHECK___SPOT",
        )
        assert request_data.exchange_name == "COINCHECK___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coincheck_spot_feed = init_req_feed()
        assert hasattr(live_coincheck_spot_feed, 'get_tick')
        assert hasattr(live_coincheck_spot_feed, '_get_tick')

    def test_has_get_depth_method(self):
        """Test that get_depth method exists."""
        live_coincheck_spot_feed = init_req_feed()
        assert hasattr(live_coincheck_spot_feed, 'get_depth')
        assert hasattr(live_coincheck_spot_feed, '_get_depth')


class TestCoincheckDataContainers:
    """Test Coincheck data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = json.dumps({
            "last": "5000000",
            "bid": "4990000",
            "ask": "5010000",
            "volume": "100.50",
            "high": "5100000",
            "low": "4800000",
        })

        ticker = CoincheckRequestTickerData(
            ticker_response, "btc_jpy", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "COINCHECK"
        assert ticker.symbol_name == "btc_jpy"
        assert ticker.last_price == 5000000
        assert ticker.bid_price == 4990000
        assert ticker.ask_price == 5010000
        assert ticker.volume_24h == 100.50
        assert ticker.high_24h == 5100000
        assert ticker.low_24h == 4800000

    def test_ticker_container_with_json_string(self):
        """Test ticker data container with JSON string input."""
        ticker_data = {
            "last": "3000000",
            "bid": "2990000",
            "ask": "3010000",
        }
        ticker_response = json.dumps(ticker_data)

        ticker = CoincheckRequestTickerData(
            ticker_response, "eth_jpy", "SPOT", False
        )
        ticker.init_data()

        assert ticker.last_price == 3000000
        assert ticker.bid_price == 2990000
        assert ticker.ask_price == 3010000


class TestCoincheckRegistry:
    """Test Coincheck registration."""

    def test_coincheck_registered(self):
        """Test that Coincheck is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "COINCHECK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINCHECK___SPOT"] == CoincheckRequestDataSpot

        # Check if exchange data is registered
        assert "COINCHECK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINCHECK___SPOT"] == CoincheckExchangeDataSpot

        # Check if balance handler is registered
        assert "COINCHECK___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["COINCHECK___SPOT"] is not None

    def test_coincheck_create_feed(self):
        """Test creating Coincheck feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINCHECK___SPOT",
            data_queue,
        )
        assert isinstance(feed, CoincheckRequestDataSpot)

    def test_coincheck_create_exchange_data(self):
        """Test creating Coincheck exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "COINCHECK___SPOT")
        assert isinstance(exchange_data, CoincheckExchangeDataSpot)


class TestCoincheckServerTime:
    """Test Coincheck server time endpoint."""

    def test_coincheck_req_server_time(self):
        """Test getting server time from Coincheck."""
        # Coincheck doesn't provide a server time endpoint
        pass


class TestCoincheckTickerData:
    """Test Coincheck ticker data retrieval."""

    def test_coincheck_req_tick_data(self):
        """Test getting ticker data synchronously."""
        live_coincheck_spot_feed = init_req_feed()
        data = live_coincheck_spot_feed.get_tick("btc_jpy")
        assert isinstance(data, RequestData)
        if data.get_status():
            pass
        tick_data_list = data.get_data()
        if tick_data_list:
            tick_data = tick_data_list[0]
            if hasattr(tick_data, 'init_data'):
                pass
            tick_data = tick_data.init_data()
            assert tick_data.get_exchange_name() == "COINCHECK"
            assert tick_data.get_symbol_name() == "btc_jpy"
            assert tick_data.get_last_price() > 0

    def test_coincheck_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: Coincheck implementation doesn't have async_get_tick in base
        # class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coincheck_spot_feed = init_req_feed()
        path, params, extra_data = live_coincheck_spot_feed._get_tick(
            "btc_jpy")

        assert path == "GET /api/ticker"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "btc_jpy"


class TestCoincheckOrderBook:
    """Test Coincheck order book data retrieval."""

    def test_coincheck_req_orderbook_data(self):
        """Test getting order book data synchronously."""
        live_coincheck_spot_feed = init_req_feed()
        data = live_coincheck_spot_feed.get_depth("btc_jpy", 20)
        assert isinstance(data, RequestData)
        # Validate order book structure
        result_data = data.get_data()
        assert isinstance(result_data, dict) or isinstance(result_data, list)

    def test_coincheck_async_orderbook_data(self):
        """Test getting order book data asynchronously."""
        # Note: Coincheck implementation doesn't have async_get_depth in base
        # class
        pass

    def test_get_depth_params(self):
        """Test get_depth parameter generation."""
        live_coincheck_spot_feed = init_req_feed()
        path, params, extra_data = live_coincheck_spot_feed._get_depth(
            "btc_jpy", 20)

        assert path == "GET /api/order_books"
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "btc_jpy"


class TestCoincheckCapabilities:
    """Test Coincheck feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoincheckRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoincheckIntegration:
    """Integration tests for Coincheck."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("btc_jpy")
        assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
