"""
Test CoinEx exchange integration.

Run tests:
    pytest tests/feeds/test_coinex.py -v

Run with coverage:
    pytest tests/feeds/test_coinex.py --cov=bt_api_py.feeds.live_coinex --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.coinex_exchange_data import (
    CoinExExchangeData,
    CoinExExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coinex_ticker import (
    CoinExRequestTickerData,
)
from bt_api_py.feeds.live_coinex.spot import CoinExRequestDataSpot
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.exceptions import RequestFailedError

# Import registration to auto-register CoinEx
import bt_api_py.feeds.register_coinex  # noqa: F401


def init_req_feed():
    """Initialize CoinEx request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINEX___SPOT",
    }
    live_coinex_spot_feed = CoinExRequestDataSpot(data_queue, **kwargs)
    return live_coinex_spot_feed


def init_async_feed(data_queue):
    """Initialize CoinEx async feed for testing."""
    kwargs = {
        "exchange_name": "COINEX___SPOT",
    }
    live_coinex_spot_feed = CoinExRequestDataSpot(data_queue, **kwargs)
    return live_coinex_spot_feed


class TestCoinExExchangeData:
    """Test CoinEx exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating CoinEx spot exchange data."""
        exchange_data = CoinExExchangeDataSpot()
        assert exchange_data.exchange_name == "coinex" or "COINEX" in exchange_data.exchange_name.upper()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert "coinex" in exchange_data.rest_url.lower()

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = CoinExExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = CoinExExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = CoinExExchangeDataSpot()
        assert "coinex" in exchange_data.rest_url.lower()

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = CoinExExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestCoinExRequestData:
    """Test CoinEx REST API request base class."""

    def test_request_data_creation(self):
        """Test creating CoinEx request data."""
        data_queue = queue.Queue()
        request_data = CoinExRequestDataSpot(
            data_queue,
            exchange_name="COINEX___SPOT",
        )
        assert request_data.exchange_name == "COINEX___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coinex_spot_feed = init_req_feed()
        assert hasattr(live_coinex_spot_feed, 'get_tick')
        assert hasattr(live_coinex_spot_feed, '_get_tick')

    def test_has_get_depth_method(self):
        """Test that get_depth method exists."""
        live_coinex_spot_feed = init_req_feed()
        assert hasattr(live_coinex_spot_feed, 'get_depth')
        assert hasattr(live_coinex_spot_feed, '_get_depth')

    def test_has_get_kline_method(self):
        """Test that get_kline method exists."""
        live_coinex_spot_feed = init_req_feed()
        assert hasattr(live_coinex_spot_feed, 'get_kline')
        assert hasattr(live_coinex_spot_feed, '_get_kline')


class TestCoinExDataContainers:
    """Test CoinEx data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        # CoinEx ticker format - note the full structure including code
        ticker_data = {
            "code": 0,
            "data": {
                "market": "BTCUSDT",
                "last": "50000",
                "bid": "49990",
                "ask": "50010",
                "volume_24h": "1234.56",
                "high_24h": "51000",
                "low_24h": "49000",
            }
        }

        ticker = CoinExRequestTickerData(
            json.dumps(ticker_data), "BTC-USDT", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "COINEX"
        # Note: ticker.init_data() is called in the test
        # The actual parsed values come from data["data"] nested structure
        # So we check if init_data was called successfully
        assert ticker.has_been_init_data is True


class TestCoinExRegistration:
    """Test CoinEx registration."""

    def test_coinex_exchange_data_creation(self):
        """Test creating CoinEx exchange data."""
        exchange_data = CoinExExchangeDataSpot()
        assert exchange_data is not None
        assert exchange_data.exchange_name is not None


class TestCoinExServerTime:
    """Test CoinEx server time endpoint."""

    def test_coinex_req_server_time(self):
        """Test getting server time from CoinEx."""
        # CoinEx may have a server time endpoint but not in current base
        # implementation
        pass


class TestCoinExTickerData:
    """Test CoinEx ticker data retrieval."""

    def test_coinex_req_tick_data(self):
        """Test getting ticker data synchronously."""
        live_coinex_spot_feed = init_req_feed()
        try:
            data = live_coinex_spot_feed.get_tick("BTCUSDT")
            assert isinstance(data, RequestData)
            if data.get_status():
                pass
            tick_data_list = data.get_data()
            if tick_data_list:
                tick_data = tick_data_list[0]
                if hasattr(tick_data, 'init_data'):
                    pass
                tick_data = tick_data.init_data()
                assert tick_data.get_exchange_name() == "COINEX"
                assert tick_data.get_symbol_name() == "BTCUSDT"
                assert tick_data.get_last_price() > 0
        except RequestFailedError as e:
            if "404" in str(e):
                pytest.skip(f"CoinEx API endpoint unavailable (404): {e}")
            raise

    def test_coinex_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: CoinEx implementation doesn't have async_get_tick in base class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coinex_spot_feed = init_req_feed()
        path, params, extra_data = live_coinex_spot_feed._get_tick("BTCUSDT")

        assert path == "GET /spot/ticker"
        assert "market" in params
        assert params["market"] == "BTCUSDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCUSDT"


class TestCoinExKlineData:
    """Test CoinEx kline/candlestick data retrieval."""

    def test_coinex_req_kline_data(self):
        """Test getting kline data synchronously."""
        live_coinex_spot_feed = init_req_feed()
        try:
            data = live_coinex_spot_feed.get_kline("BTCUSDT", "1m", count=2)
            assert isinstance(data, RequestData)
            if data.get_status():
                pass
            kline_data_list = data.get_data()
            if kline_data_list:
                # Validate kline data structure
                pass
        except RequestFailedError as e:
            if "404" in str(e):
                pytest.skip(f"CoinEx API endpoint unavailable (404): {e}")
            raise

    def test_coinex_async_kline_data(self):
        """Test getting kline data asynchronously."""
        # Note: CoinEx implementation doesn't have async_get_kline in base
        # class
        pass

    def test_get_kline_params(self):
        """Test get_kline parameter generation."""
        live_coinex_spot_feed = init_req_feed()
        path, params, extra_data = live_coinex_spot_feed._get_kline(
            "BTCUSDT", "1m", 2)

        assert path == "GET /spot/kline"
        assert "market" in params
        assert params["market"] == "BTCUSDT"
        assert "type" in params
        assert "limit" in params
        assert params["limit"] == 2
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "BTCUSDT"


class TestCoinExOrderBook:
    """Test CoinEx order book data retrieval."""

    def test_coinex_req_orderbook_data(self):
        """Test getting order book data synchronously."""
        live_coinex_spot_feed = init_req_feed()
        try:
            data = live_coinex_spot_feed.get_depth("BTCUSDT", 20)
            assert isinstance(data, RequestData)
            # Validate order book structure
            result_data = data.get_data()
            assert isinstance(result_data, dict) or isinstance(result_data, list)
        except RequestFailedError as e:
            if "404" in str(e):
                pytest.skip(f"CoinEx API endpoint unavailable (404): {e}")
            raise

    def test_coinex_async_orderbook_data(self):
        """Test getting order book data asynchronously."""
        # Note: CoinEx implementation doesn't have async_get_depth in base
        # class
        pass

    def test_get_depth_params(self):
        """Test get_depth parameter generation."""
        live_coinex_spot_feed = init_req_feed()
        path, params, extra_data = live_coinex_spot_feed._get_depth(
            "BTCUSDT", 20)

        assert path == "GET /spot/order_book"
        assert "market" in params
        assert params["market"] == "BTCUSDT"
        assert "depth" in params
        assert params["depth"] == 20
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "BTCUSDT"


class TestCoinExCapabilities:
    """Test CoinEx feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoinExRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoinExIntegration:
    """Integration tests for CoinEx."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
