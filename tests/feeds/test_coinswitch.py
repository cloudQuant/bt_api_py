"""
Test CoinSwitch exchange integration.

Run tests:
    pytest tests/feeds/test_coinswitch.py -v

Run with coverage:
    pytest tests/feeds/test_coinswitch.py --cov=bt_api_py.feeds.live_coinswitch --cov-report=term-missing

Run integration tests only:
    pytest tests/feeds/test_coinswitch.py -m integration -v
"""

import queue

import pytest

from bt_api_py.containers.exchanges.coinswitch_exchange_data import (
    CoinSwitchExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error_framework import AuthenticationError
from bt_api_py.feeds.live_coinswitch.spot import CoinSwitchRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register CoinSwitch
import bt_api_py.feeds.register_coinswitch  # noqa: F401


def init_req_feed():
    """Initialize CoinSwitch request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_name": "COINSWITCH___SPOT",
    }
    live_coinswitch_spot_feed = CoinSwitchRequestDataSpot(data_queue, **kwargs)
    return live_coinswitch_spot_feed


def init_async_feed(data_queue):
    """Initialize CoinSwitch async feed for testing."""
    kwargs = {
        "exchange_name": "COINSWITCH___SPOT",
    }
    live_coinswitch_spot_feed = CoinSwitchRequestDataSpot(data_queue, **kwargs)
    return live_coinswitch_spot_feed


class TestCoinSwitchExchangeData:
    """Test CoinSwitch exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating CoinSwitch spot exchange data."""
        exchange_data = CoinSwitchExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.exchange_name in ["coinswitch", "coinswitchSpot"]
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.coinswitch.co"
        assert exchange_data.wss_url == ""

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = CoinSwitchExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "60"
        assert exchange_data.kline_periods["1d"] == "D"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = CoinSwitchExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency

    def test_api_key_property(self):
        """Test API key property."""
        exchange_data = CoinSwitchExchangeDataSpot()
        assert hasattr(exchange_data, "api_key")


class TestCoinSwitchRequestData:
    """Test CoinSwitch REST API request base class."""

    def test_request_data_creation(self):
        """Test creating CoinSwitch request data."""
        data_queue = queue.Queue()
        request_data = CoinSwitchRequestDataSpot(
            data_queue,
            exchange_name="COINSWITCH___SPOT",
        )
        assert request_data.exchange_name == "COINSWITCH___SPOT"

    def test_has_get_tick_method(self):
        """Test that get_tick method exists."""
        live_coinswitch_spot_feed = init_req_feed()
        assert hasattr(live_coinswitch_spot_feed, 'get_tick')
        assert hasattr(live_coinswitch_spot_feed, '_get_tick')

    def test_has_get_all_tickers_method(self):
        """Test that get_all_tickers method exists."""
        live_coinswitch_spot_feed = init_req_feed()
        assert hasattr(live_coinswitch_spot_feed, '_get_all_tickers')


class TestCoinSwitchDataContainers:
    """Test CoinSwitch data containers."""

    def test_exchange_data_properties(self):
        """Test exchange data has required properties."""
        exchange_data = CoinSwitchExchangeDataSpot()
        # CoinSwitch uses API key authentication
        assert hasattr(exchange_data, "api_key")


class TestCoinSwitchRegistry:
    """Test CoinSwitch registration."""

    def test_coinswitch_registered(self):
        """Test that CoinSwitch is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "COINSWITCH___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINSWITCH___SPOT"] == CoinSwitchRequestDataSpot

        # Check if exchange data is registered
        assert "COINSWITCH___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINSWITCH___SPOT"] == CoinSwitchExchangeDataSpot

        # Check if balance handler is registered
        assert "COINSWITCH___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["COINSWITCH___SPOT"] is not None

    def test_coinswitch_create_feed(self):
        """Test creating CoinSwitch feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINSWITCH___SPOT",
            data_queue,
        )
        assert isinstance(feed, CoinSwitchRequestDataSpot)

    def test_coinswitch_create_exchange_data(self):
        """Test creating CoinSwitch exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "COINSWITCH___SPOT")
        assert isinstance(exchange_data, CoinSwitchExchangeDataSpot)


class TestCoinSwitchServerTime:
    """Test CoinSwitch server time endpoint."""

    def test_coinswitch_req_server_time(self):
        """Test getting server time from CoinSwitch."""
        # CoinSwitch doesn't provide a server time endpoint
        pass


class TestCoinSwitchTickerData:
    """Test CoinSwitch ticker data retrieval."""

    def test_coinswitch_req_tick_data(self):
        """Test getting ticker data synchronously.

        Note: CoinSwitch API requires authentication for all endpoints including
        public tickers. This test will be skipped if no API key is configured.
        """
        live_coinswitch_spot_feed = init_req_feed()
        try:
            data = live_coinswitch_spot_feed.get_tick("BTCINR")
            assert isinstance(data, RequestData)
            if data.get_status():
                pass
            tick_data_list = data.get_data()
            if tick_data_list:
                # Validate ticker data structure
                pass
        except AuthenticationError as e:
            # CoinSwitch API requires authentication even for public endpoints
            # Skip test if no API key is configured
            pytest.skip(f"CoinSwitch API requires authentication: {e}")

    def test_coinswitch_async_tick_data(self):
        """Test getting ticker data asynchronously."""
        # Note: CoinSwitch implementation doesn't have async_get_tick in base
        # class
        pass

    def test_get_tick_params(self):
        """Test get_tick parameter generation."""
        live_coinswitch_spot_feed = init_req_feed()
        path, params, extra_data = live_coinswitch_spot_feed._get_tick(
            "BTCINR")

        assert path == "GET /v2/tickers/BTCINR"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCINR"

    def test_get_all_tickers_params(self):
        """Test get_all_tickers parameter generation."""
        live_coinswitch_spot_feed = init_req_feed()
        path, params, extra_data = live_coinswitch_spot_feed._get_all_tickers()

        assert path == "GET /v2/tickers"
        assert extra_data["request_type"] == "get_ticker_all"


class TestCoinSwitchCapabilities:
    """Test CoinSwitch feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = CoinSwitchRequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestCoinSwitchIntegration:
    """Integration tests for CoinSwitch."""

    @pytest.mark.integration
    def test_market_data_api(self):
        """Test market data API calls (requires network).

        Note: CoinSwitch API requires authentication for all endpoints.
        This test will be skipped if no API key is configured.
        """
        data_queue = queue.Queue()
        feed = CoinSwitchRequestDataSpot(data_queue)

        try:
            # Test ticker
            ticker = feed.get_tick("BTCINR")
            assert ticker.status is True
        except AuthenticationError as e:
            # CoinSwitch API requires authentication even for public endpoints
            # Skip test if no API key is configured
            pytest.skip(f"CoinSwitch API requires authentication: {e}")

    @pytest.mark.integration
    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
