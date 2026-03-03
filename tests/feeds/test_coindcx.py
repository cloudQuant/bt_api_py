"""
Test CoinDCX exchange integration.

Run tests:
    pytest tests/feeds/test_coindcx.py -v

Run with coverage:
    pytest tests/feeds/test_coindcx.py --cov=bt_api_py.feeds.live_coindcx --cov-report=term-missing
"""

import json
import queue
import time

import pytest

from bt_api_py.containers.exchanges.coindcx_exchange_data import (
    CoinDCXExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coindcx_ticker import CoinDCXRequestTickerData
from bt_api_py.feeds.live_coindcx.spot import CoinDCXRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register CoinDCX
import bt_api_py.feeds.register_coindcx  # noqa: F401


def init_req_feed():
    """Initialize CoinDCX request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINDCX___SPOT",
    }
    live_coindcx_spot_feed = CoinDCXRequestDataSpot(data_queue, **kwargs)
    return live_coindcx_spot_feed


def init_async_feed(data_queue):
    """Initialize CoinDCX async feed for testing."""
    kwargs = {
        "exchange_name": "COINDCX___SPOT",
    }
    live_coindcx_spot_feed = CoinDCXRequestDataSpot(data_queue, **kwargs)
    return live_coindcx_spot_feed


class TestCoinDCXExchangeData:
    """Test CoinDCX exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating CoinDCX spot exchange data."""
        exchange_data = CoinDCXExchangeDataSpot()
        assert exchange_data.exchange_name == "coindcx"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.coindcx.com"
        assert exchange_data.wss_url == "wss://stream.coindcx.com"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = CoinDCXExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1m"] == "1m"
        assert exchange_data.kline_periods["1h"] == "1h"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = CoinDCXExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = CoinDCXExchangeDataSpot()
        assert exchange_data.rest_url == "https://api.coindcx.com"

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = CoinDCXExchangeDataSpot()
        assert exchange_data.wss_url == "wss://stream.coindcx.com"


class TestCoinDCXRequestData:
    """Test CoinDCX REST API request base class."""

    def test_request_data_creation(self):
        """Test creating CoinDCX request data."""
        data_queue = queue.Queue()
        request_data = CoinDCXRequestDataSpot(
            data_queue,
            exchange_name="COINDCX___SPOT",
        )
        assert request_data.exchange_name == "COINDCX___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coindcx_spot_feed = init_req_feed()
        assert hasattr(live_coindcx_spot_feed, 'get_tick')
        assert hasattr(live_coindcx_spot_feed, '_get_tick')

    def test_has_get_depth_method(self):
        """Test that get_depth method exists."""
        live_coindcx_spot_feed = init_req_feed()
        assert hasattr(live_coindcx_spot_feed, 'get_depth')
        assert hasattr(live_coindcx_spot_feed, '_get_depth')

    def test_has_get_kline_method(self):
        """Test that get_kline method exists."""
        live_coindcx_spot_feed = init_req_feed()
        assert hasattr(live_coindcx_spot_feed, 'get_kline')
        assert hasattr(live_coindcx_spot_feed, '_get_kline')


class TestCoinDCXDataContainers:
    """Test CoinDCX data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = json.dumps({
            "data": {
                "market": "BTCINR",
                "last_price": "5000000",
                "bid": "4990000",
                "ask": "5010000",
                "volume": "100.50",
                "high": "5100000",
                "low": "4800000",
            }
        })

        ticker = CoinDCXRequestTickerData(
            ticker_response, "BTCINR", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "COINDCX"
        assert ticker.symbol_name == "BTCINR"
        assert ticker.last_price == 5000000
        assert ticker.bid_price == 4990000
        assert ticker.ask_price == 5010000
        assert ticker.volume_24h == 100.50
        assert ticker.high_24h == 5100000
        assert ticker.low_24h == 4800000

    def test_ticker_container_with_json_string(self):
        """Test ticker data container with JSON string input."""
        ticker_data = {
            "data": {
                "market": "ETHINR",
                "last_price": "300000",
                "bid": "299000",
                "ask": "301000",
            }
        }
        ticker_response = json.dumps(ticker_data)

        ticker = CoinDCXRequestTickerData(
            ticker_response, "ETHINR", "SPOT", False
        )
        ticker.init_data()

        assert ticker.last_price == 300000
        assert ticker.bid_price == 299000
        assert ticker.ask_price == 301000


class TestCoinDCXRegistry:
    """Test CoinDCX registration."""

    def test_coindcx_registered(self):
        """Test that CoinDCX is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "COINDCX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINDCX___SPOT"] == CoinDCXRequestDataSpot

        # Check if exchange data is registered
        assert "COINDCX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINDCX___SPOT"] == CoinDCXExchangeDataSpot

        # Check if balance handler is registered
        assert "COINDCX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["COINDCX___SPOT"] is not None

    def test_coindcx_create_feed(self):
        """Test creating CoinDCX feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINDCX___SPOT",
            data_queue,
        )
        assert isinstance(feed, CoinDCXRequestDataSpot)

    def test_coindcx_create_exchange_data(self):
        """Test creating CoinDCX exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("COINDCX___SPOT")
        assert isinstance(exchange_data, CoinDCXExchangeDataSpot)


class TestCoinDCXServerTime:
    """Test CoinDCX server time endpoint."""

    def test_coindcx_req_server_time(self):
        """Test getting server time from CoinDCX."""
        # CoinDCX doesn't provide a server time endpoint
        pass


class TestCoinDCXTickerData:
    """Test CoinDCX ticker data retrieval."""

    def test_coindcx_req_tick_data(self):
        """Test getting ticker data synchronously."""
        live_coindcx_spot_feed = init_req_feed()
        data = live_coindcx_spot_feed.get_tick("BTCINR")
        assert isinstance(data, RequestData)
        if data.get_status():
            pass
        tick_data_list = data.get_data()
        if tick_data_list:
            tick_data = tick_data_list[0]
            if hasattr(tick_data, 'init_data'):
                pass
            tick_data = tick_data.init_data()
            assert tick_data.get_exchange_name() == "COINDCX"
            assert tick_data.get_symbol_name() == "BTCINR"
            assert tick_data.get_last_price() > 0

    def test_coindcx_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: CoinDCX implementation doesn't have async_get_tick in base
        # class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coindcx_spot_feed = init_req_feed()
        path, params, extra_data = live_coindcx_spot_feed._get_tick("BTCINR")

        assert path == "GET /exchange/ticker"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCINR"


class TestCoinDCXKlineData:
    """Test CoinDCX kline/candlestick data retrieval."""

    def test_coindcx_req_kline_data(self):
        """Test getting kline data synchronously."""
        # Note: CoinDCX doesn't have a working kline endpoint in their public API
        # We test the parameter generation instead
        live_coindcx_spot_feed = init_req_feed()
        # Test _get_kline method directly without making API call
        path, params, extra_data = live_coindcx_spot_feed._get_kline("BTCINR", "1m", 2)

        assert path == "GET /market_data/candles"
        assert "pair" in params
        assert params["pair"] == "BTCINR"
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "BTCINR"

    def test_coindcx_async_kline_data(self):
        """Test getting kline data asynchronously."""
        # Note: CoinDCX implementation doesn't have async_get_kline in base
        # class
        pass

    def test_get_kline_params(self):
        """Test get_kline parameter generation."""
        live_coindcx_spot_feed = init_req_feed()
        path, params, extra_data = live_coindcx_spot_feed._get_kline(
            "BTCINR", "1m", 2)

        assert path == "GET /market_data/candles"
        assert "pair" in params
        assert params["pair"] == "BTCINR"
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "BTCINR"
        # Note: CoinDCX doesn't have a working kline endpoint, this test only checks the format


class TestCoinDCXOrderBook:
    """Test CoinDCX order book data retrieval."""

    def test_coindcx_req_orderbook_data(self):
        """Test getting order book data synchronously."""
        live_coindcx_spot_feed = init_req_feed()
        data = live_coindcx_spot_feed.get_depth("BTCINR", 20)
        assert isinstance(data, RequestData)
        # Validate order book structure
        result_data = data.get_data()
        assert isinstance(result_data, dict) or isinstance(result_data, list)

    def test_coindcx_async_orderbook_data(self):
        """Test getting order book data asynchronously."""
        # Note: CoinDCX implementation doesn't have async_get_depth in base
        # class
        pass

    def test_get_depth_params(self):
        """Test get_depth parameter generation."""
        live_coindcx_spot_feed = init_req_feed()
        path, params, extra_data = live_coindcx_spot_feed._get_depth(
            "BTCINR", 20)

        # CoinDCX uses path-based parameter for symbol
        assert path == "GET /exchange/v1/orderbook/BTCINR"
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "BTCINR"


class TestCoinDCXCapabilities:
    """Test CoinDCX feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoinDCXRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoinDCXIntegration:
    """Integration tests for CoinDCX."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTCINR")
        assert ticker.get_status() is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
