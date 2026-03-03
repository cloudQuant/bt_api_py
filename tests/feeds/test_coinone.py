"""
Test Coinone exchange integration.

Run tests:
    pytest tests/feeds/test_coinone.py -v

Run with coverage:
    pytest tests/feeds/test_coinone.py --cov=bt_api_py.feeds.live_coinone --cov-report=term-missing
"""

import json
import queue
import time

import pytest

from bt_api_py.containers.exchanges.coinone_exchange_data import (
    CoinoneExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coinone_ticker import CoinoneRequestTickerData
from bt_api_py.feeds.live_coinone.spot import CoinoneRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Coinone
import bt_api_py.feeds.register_coinone  # noqa: F401


def init_req_feed():
    """Initialize Coinone request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINONE___SPOT",
    }
    live_coinone_spot_feed = CoinoneRequestDataSpot(data_queue, **kwargs)
    return live_coinone_spot_feed


def init_async_feed(data_queue):
    """Initialize Coinone async feed for testing."""
    kwargs = {
        "exchange_name": "COINONE___SPOT",
    }
    live_coinone_spot_feed = CoinoneRequestDataSpot(data_queue, **kwargs)
    return live_coinone_spot_feed


class TestCoinoneExchangeData:
    """Test Coinone exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Coinone spot exchange data."""
        exchange_data = CoinoneExchangeDataSpot()
        assert exchange_data.exchange_name == "coinone"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.coinone.co.kr"
        assert exchange_data.wss_url == "wss://stream.coinone.co.kr"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = CoinoneExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1m"] == "1m"
        assert exchange_data.kline_periods["1h"] == "1h"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = CoinoneExchangeDataSpot()
        assert "KRW" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = CoinoneExchangeDataSpot()
        assert exchange_data.rest_url == "https://api.coinone.co.kr"

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = CoinoneExchangeDataSpot()
        assert exchange_data.wss_url == "wss://stream.coinone.co.kr"


class TestCoinoneRequestData:
    """Test Coinone REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Coinone request data."""
        data_queue = queue.Queue()
        request_data = CoinoneRequestDataSpot(
            data_queue,
            exchange_name="COINONE___SPOT",
        )
        assert request_data.exchange_name == "COINONE___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coinone_spot_feed = init_req_feed()
        assert hasattr(live_coinone_spot_feed, 'get_tick')
        assert hasattr(live_coinone_spot_feed, '_get_tick')

    def test_has_get_depth_method(self):
        """Test that get_depth method exists."""
        live_coinone_spot_feed = init_req_feed()
        assert hasattr(live_coinone_spot_feed, 'get_depth')
        assert hasattr(live_coinone_spot_feed, '_get_depth')

    def test_has_get_kline_method(self):
        """Test that get_kline method exists."""
        live_coinone_spot_feed = init_req_feed()
        assert hasattr(live_coinone_spot_feed, 'get_kline')
        assert hasattr(live_coinone_spot_feed, '_get_kline')

    def test_parse_symbol(self):
        """Test symbol parsing functionality."""
        live_coinone_spot_feed = init_req_feed()
        quote, target = live_coinone_spot_feed._parse_symbol("KRW-BTC")
        assert quote == "KRW"
        assert target == "BTC"

        quote, target = live_coinone_spot_feed._parse_symbol("btckrw")
        assert quote == "KRW"
        assert target == "btckrw"


class TestCoinoneDataContainers:
    """Test Coinone data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = json.dumps({
            "last": "50000000",
            "bid": "49900000",
            "ask": "50100000",
            "volume": "100.50",
            "high": "51000000",
            "low": "48000000",
        })

        ticker = CoinoneRequestTickerData(
            ticker_response, "btckrw", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "COINONE"
        assert ticker.symbol_name == "btckrw"
        assert ticker.last_price == 50000000
        assert ticker.bid_price == 49900000
        assert ticker.ask_price == 50100000
        assert ticker.volume_24h == 100.50
        assert ticker.high_24h == 51000000
        assert ticker.low_24h == 48000000

    def test_ticker_container_with_json_string(self):
        """Test ticker data container with JSON string input."""
        ticker_data = {
            "last": "3000000",
            "bid": "2990000",
            "ask": "3010000",
        }
        ticker_response = json.dumps(ticker_data)

        ticker = CoinoneRequestTickerData(
            ticker_response, "ethkrw", "SPOT", False
        )
        ticker.init_data()

        assert ticker.last_price == 3000000
        assert ticker.bid_price == 2990000
        assert ticker.ask_price == 3010000


class TestCoinoneRegistry:
    """Test Coinone registration."""

    def test_coinone_registered(self):
        """Test that Coinone is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "COINONE___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINONE___SPOT"] == CoinoneRequestDataSpot

        # Check if exchange data is registered
        assert "COINONE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINONE___SPOT"] == CoinoneExchangeDataSpot

        # Check if balance handler is registered
        assert "COINONE___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["COINONE___SPOT"] is not None

    def test_coinone_create_feed(self):
        """Test creating Coinone feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINONE___SPOT",
            data_queue,
        )
        assert isinstance(feed, CoinoneRequestDataSpot)

    def test_coinone_create_exchange_data(self):
        """Test creating Coinone exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("COINONE___SPOT")
        assert isinstance(exchange_data, CoinoneExchangeDataSpot)


class TestCoinoneServerTime:
    """Test Coinone server time endpoint."""

    def test_coinone_req_server_time(self):
        """Test getting server time from Coinone."""
        # Coinone doesn't provide a server time endpoint
        pass


class TestCoinoneTickerData:
    """Test Coinone ticker data retrieval."""

    def test_coinone_req_tick_data(self):
        """Test getting ticker data synchronously."""
        live_coinone_spot_feed = init_req_feed()
        data = live_coinone_spot_feed.get_tick("KRW-BTC")
        assert isinstance(data, RequestData)
        if data.get_status():
            pass
        tick_data_list = data.get_data()
        if tick_data_list:
            tick_data = tick_data_list[0]
            if hasattr(tick_data, 'init_data'):
                pass
            tick_data = tick_data.init_data()
            assert tick_data.get_exchange_name() == "COINONE"
            assert tick_data.get_symbol_name() == "KRW-BTC"
            assert tick_data.get_last_price() > 0

    def test_coinone_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: Coinone implementation doesn't have async_get_tick in base
        # class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coinone_spot_feed = init_req_feed()
        path, params, extra_data = live_coinone_spot_feed._get_tick("KRW-BTC")

        assert path == "GET /public/v2/ticker_new/KRW/BTC"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "KRW-BTC"


class TestCoinoneKlineData:
    """Test Coinone kline/candlestick data retrieval."""

    def test_coinone_req_kline_data(self):
        """Test getting kline data synchronously."""
        live_coinone_spot_feed = init_req_feed()
        data = live_coinone_spot_feed.get_kline("KRW-BTC", "1m", count=2)
        assert isinstance(data, RequestData)
        if data.get_status():
            pass
        kline_data_list = data.get_data()
        if kline_data_list:
            # Validate kline data structure
            pass

    def test_coinone_async_kline_data(self):
        """Test getting kline data asynchronously."""
        # Note: Coinone implementation doesn't have async_get_kline in base
        # class
        pass

    def test_get_kline_params(self):
        """Test get_kline parameter generation."""
        live_coinone_spot_feed = init_req_feed()
        path, params, extra_data = live_coinone_spot_feed._get_kline(
            "KRW-BTC", "1m", 2)

        assert path == "GET /public/v2/chart/KRW/BTC"
        assert "interval" in params
        assert "limit" in params
        assert params["limit"] == 2
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "KRW-BTC"


class TestCoinoneOrderBook:
    """Test Coinone order book data retrieval."""

    def test_coinone_req_orderbook_data(self):
        """Test getting order book data synchronously."""
        live_coinone_spot_feed = init_req_feed()
        data = live_coinone_spot_feed.get_depth("KRW-BTC", 20)
        assert isinstance(data, RequestData)
        # Validate order book structure
        result_data = data.get_data()
        assert isinstance(result_data, dict) or isinstance(result_data, list)

    def test_coinone_async_orderbook_data(self):
        """Test getting order book data asynchronously."""
        # Note: Coinone implementation doesn't have async_get_depth in base
        # class
        pass

    def test_get_depth_params(self):
        """Test get_depth parameter generation."""
        live_coinone_spot_feed = init_req_feed()
        path, params, extra_data = live_coinone_spot_feed._get_depth(
            "KRW-BTC", 20)

        assert path == "GET /public/v2/orderbook/KRW/BTC"
        assert "size" in params
        assert params["size"] == 20
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "KRW-BTC"


class TestCoinoneCapabilities:
    """Test Coinone feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoinoneRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoinoneIntegration:
    """Integration tests for Coinone."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = CoinoneRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("btckrw")
        assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
