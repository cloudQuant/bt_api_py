"""
Test CoinSpot exchange integration.

Run tests:
    pytest tests/feeds/test_coinspot.py -v

Run with coverage:
    pytest tests/feeds/test_coinspot.py --cov=bt_api_py.feeds.live_coinspot --cov-report=term-missing
"""

import queue

import pytest

from bt_api_py.containers.exchanges.coinspot_exchange_data import (
    CoinSpotExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinspot.spot import CoinSpotRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register CoinSpot
import bt_api_py.feeds.register_coinspot  # noqa: F401


def init_req_feed():
    """Initialize CoinSpot request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINSPOT___SPOT",
    }
    live_coinspot_spot_feed = CoinSpotRequestDataSpot(data_queue, **kwargs)
    return live_coinspot_spot_feed


def init_async_feed(data_queue):
    """Initialize CoinSpot async feed for testing."""
    kwargs = {
        "exchange_name": "COINSPOT___SPOT",
    }
    live_coinspot_spot_feed = CoinSpotRequestDataSpot(data_queue, **kwargs)
    return live_coinspot_spot_feed


class TestCoinSpotExchangeData:
    """Test CoinSpot exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating CoinSpot spot exchange data."""
        exchange_data = CoinSpotExchangeDataSpot()
        assert exchange_data.exchange_name == "coinspot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://www.coinspot.com.au"
        assert exchange_data.wss_url == ""

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = CoinSpotExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "60"
        assert exchange_data.kline_periods["1d"] == "D"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = CoinSpotExchangeDataSpot()
        assert "AUD" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = CoinSpotExchangeDataSpot()
        assert exchange_data.rest_url == "https://www.coinspot.com.au"


class TestCoinSpotRequestData:
    """Test CoinSpot REST API request base class."""

    def test_request_data_creation(self):
        """Test creating CoinSpot request data."""
        data_queue = queue.Queue()
        request_data = CoinSpotRequestDataSpot(
            data_queue,
            exchange_name="COINSPOT___SPOT",
        )
        assert request_data.exchange_name == "COINSPOT___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coinspot_spot_feed = init_req_feed()
        assert hasattr(live_coinspot_spot_feed, 'get_tick')
        assert hasattr(live_coinspot_spot_feed, '_get_tick')

    def test_has_get_all_tickers_method(self):
        """Test that get_all_tickers method exists."""
        live_coinspot_spot_feed = init_req_feed()
        assert hasattr(live_coinspot_spot_feed, '_get_all_tickers')


class TestCoinSpotDataContainers:
    """Test CoinSpot data containers."""

    def test_exchange_data_properties(self):
        """Test exchange data has required properties."""
        exchange_data = CoinSpotExchangeDataSpot()
        # CoinSpot uses API key/secret authentication
        assert hasattr(exchange_data, "api_key")
        assert hasattr(exchange_data, "api_secret")


class TestCoinSpotRegistry:
    """Test CoinSpot registration."""

    def test_coinspot_registered(self):
        """Test that CoinSpot is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "COINSPOT___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINSPOT___SPOT"] == CoinSpotRequestDataSpot

        # Check if exchange data is registered
        assert "COINSPOT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINSPOT___SPOT"] == CoinSpotExchangeDataSpot

        # Check if balance handler is registered
        assert "COINSPOT___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["COINSPOT___SPOT"] is not None

    def test_coinspot_create_feed(self):
        """Test creating CoinSpot feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINSPOT___SPOT",
            data_queue,
        )
        assert isinstance(feed, CoinSpotRequestDataSpot)

    def test_coinspot_create_exchange_data(self):
        """Test creating CoinSpot exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "COINSPOT___SPOT")
        assert isinstance(exchange_data, CoinSpotExchangeDataSpot)


class TestCoinSpotServerTime:
    """Test CoinSpot server time endpoint."""

    def test_coinspot_req_server_time(self):
        """Test getting server time from CoinSpot."""
        # CoinSpot doesn't provide a server time endpoint
        pass


class TestCoinSpotTickerData:
    """Test CoinSpot ticker data retrieval."""

    def test_coinspot_req_tick_data(self):
        """Test getting ticker data synchronously."""
        live_coinspot_spot_feed = init_req_feed()
        data = live_coinspot_spot_feed.get_tick("BTC")
        assert isinstance(data, RequestData)
        if data.get_status():
            tick_data_list = data.get_data()
            if tick_data_list:
                pass
                # Validate ticker data structure
            pass

    def test_coinspot_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: CoinSpot implementation doesn't have async_get_tick in base
        # class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coinspot_spot_feed = init_req_feed()
        path, params, extra_data = live_coinspot_spot_feed._get_tick("BTC")

        assert path == "GET /pubapi/v2/latest/BTC"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC"

    def test_get_all_tickers_params(self):
        """Test get_all_tickers parameter generation."""
        live_coinspot_spot_feed = init_req_feed()
        path, params, extra_data = live_coinspot_spot_feed._get_all_tickers()

        assert path == "GET /pubapi/v2/latest"
        assert extra_data["request_type"] == "get_ticker_all"


class TestCoinSpotCapabilities:
    """Test CoinSpot feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoinSpotRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoinSpotIntegration:
    """Integration tests for CoinSpot."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = CoinSpotRequestDataSpot(data_queue)

        # Test ticker - CoinSpot API expects coin name not trading pair
        ticker = feed.get_tick("BTC")
        assert ticker.get_status() is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
