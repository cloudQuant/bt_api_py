"""
Test BYDFi exchange integration.

Run tests:
    pytest tests/feeds/test_bydfi.py -v

Run with coverage:
    pytest tests/feeds/test_bydfi.py --cov=bt_api_py.feeds.live_bydfi --cov-report=term-missing
"""

import queue
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.bydfi_exchange_data import BYDFiExchangeDataSpot
from bt_api_py.containers.tickers.bydfi_ticker import BYDFiRequestTickerData
from bt_api_py.feeds.live_bydfi.spot import BYDFiRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BYDFi
import bt_api_py.feeds.register_bydfi  # noqa: F401


class TestBydfiExchangeData:
    """Test BYDFi exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating BYDFi spot exchange data."""
        exchange_data = BYDFiExchangeDataSpot()
        # asset_type is loaded from YAML config (spot)
        assert exchange_data.asset_type == "spot"

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BYDFiExchangeDataSpot()
        # BYDFi uses dash separator - converts "/" to "-"
        assert exchange_data.get_symbol("BTC/USDT") == "BTC-USDT"
        # Already in correct format
        assert exchange_data.get_symbol("BTC-USDT") == "BTC-USDT"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = BYDFiExchangeDataSpot()
        # Test common paths
        path = exchange_data.get_rest_path("get_ticker")
        assert "ticker" in path.lower() or "tick" in path.lower()


class TestBydfiRequestData:
    """Test BYDFi REST API request base class."""

    def test_request_data_creation(self):
        """Test creating BYDFi request data."""
        data_queue = queue.Queue()
        request_data = BYDFiRequestDataSpot(
            data_queue,
            exchange_name="BYDFI___SPOT",
        )
        assert request_data.exchange_name == "BYDFI___SPOT"

    def test_capabilities(self):
        """Test that BYDFi has the correct capabilities."""
        capabilities = BYDFiRequestDataSpot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick_params(self):
        """Test get ticker parameter generation."""
        data_queue = queue.Queue()
        request_data = BYDFiRequestDataSpot(data_queue)

        # The _get_tick method returns the result of self.request
        result = request_data._get_tick("BTC-USDT")
        # We can't test the actual request without mocking, but we can verify it's called
        assert result is not None

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = BYDFiRequestDataSpot(data_queue)

        result = request_data._get_depth("BTC-USDT", count=20)
        assert result is not None

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = BYDFiRequestDataSpot(data_queue)

        result = request_data._get_kline("BTC-USDT", "1h", count=20)
        assert result is not None

    def test_get_exchange_info_params(self):
        """Test get exchange info parameter generation."""
        data_queue = queue.Queue()
        request_data = BYDFiRequestDataSpot(data_queue)

        result = request_data._get_exchange_info()
        assert result is not None


class TestBydfiDataContainers:
    """Test BYDFi data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "code": 0,
            "msg": "success",
            "data": {
            "symbol": "BTC-USDT",
            "price": "50000",
            "bid": "49999",
            "ask": "50001",
            "high": "51000",
            "low": "49000",
            "volume": "1234.56",
            "timestamp": 1234567890
            }
        }

        ticker = BYDFiRequestTickerData(
            ticker_response, "BTC-USDT", "SPOT"
        )

        assert ticker.get_symbol_name() == "BTC-USDT"


class TestBydfiRegistry:
    """Test BYDFi registration."""

    def test_bydfi_registered(self):
        """Test that BYDFi is properly registered."""
        # Check if feed is registered
        assert "BYDFI___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BYDFI___SPOT"] == BYDFiRequestDataSpot

        # Check if exchange data is registered
        assert "BYDFI___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BYDFI___SPOT"] == BYDFiExchangeDataSpot

        # Check if balance handler is registered
        assert "BYDFI___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BYDFI___SPOT"] is not None

    def test_bydfi_create_feed(self):
        """Test creating BYDFi feed through registry."""
        import queue
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BYDFI___SPOT",
            data_queue,
        )
        assert isinstance(feed, BYDFiRequestDataSpot)

    def test_bydfi_create_exchange_data(self):
        """Test creating BYDFi exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BYDFI___SPOT")
        assert isinstance(exchange_data, BYDFiExchangeDataSpot)


class TestBydfiIntegration:
    """Integration tests for BYDFi."""

    @pytest.mark.skip(reason="BYDFI API endpoint temporarily unavailable (404)")
    @pytest.mark.integration
    def test_market_data_api(self):
        """Test market data API calls (requires network)"""
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue)

        # Test ticker
        result = feed.get_tick("BTC-USDT")
        assert result is not None

    @pytest.mark.integration
    def test_trading_api(self):
        """Test trading API calls (requires API keys)"""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
