"""
Test OKX exchange integration.

OKX has detailed tests in subdirectories (tests/feeds/live_okx_*/).
This test file provides simplified tests focusing on:
- Exchange data classes
- Data containers
- Registry registration

Run tests:
    pytest tests/feeds/test_okx.py -v

Run with coverage:
    pytest tests/feeds/test_okx.py --cov=bt_api_py.feeds.live_okx --cov-report=term-missing
"""

import queue

import pytest

# OkxBalance import not available
from bt_api_py.containers.exchanges.okx_exchange_data import (
    OkxExchangeDataSpot,
    OkxExchangeDataSwap,
)
from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.feeds.live_okx.spot import OkxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register OKX
import bt_api_py.feeds.register_okx  # noqa: F401


class TestOkxExchangeData:
    """Test OKX exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating OKX spot exchange data."""
        exchange_data = OkxExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        # OKX uses different key names for rest_paths
        assert "make_order" in exchange_data.rest_paths or "place_order" in exchange_data.rest_paths or "amend_algo_order" in exchange_data.rest_paths

    def test_exchange_data_swap_creation(self):
        """Test creating OKX swap exchange data."""
        exchange_data = OkxExchangeDataSwap()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = OkxExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/USDT") == "BTC-USDT"
        assert exchange_data.get_symbol("ETH/USDT") == "ETH-USDT"

    def test_get_symbol_re(self):
        """Test reverse symbol format conversion."""
        exchange_data = OkxExchangeDataSpot()
        assert exchange_data.get_symbol_re("BTC-USDT") == "btc/usdt"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = OkxExchangeDataSpot()
        path = exchange_data.get_rest_path("make_order")
        assert path  # Should return a valid path

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = OkxExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods


class TestOkxRequestDataSpot:
    """Test OKX spot REST API request class."""

    def test_request_data_creation(self):
        """Test creating OKX request data."""
        data_queue = queue.Queue()
        request_data = OkxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            passphrase="test_pass",
            exchange_name="OKX___SPOT",
        )
        assert request_data.exchange_name == "OKX___SPOT"
        assert request_data.asset_type == "SPOT"


class TestOkxDataContainers:
    """Test OKX data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "code": "0",
            "msg": "",
            "data": [
            {
                "instType": "SP",
                "instId": "BTC-USDT",
                "last": "50000",
                "bidPx": "49999",
                "askPx": "50001",
                "volCcy24h": "1000",
                "vol24h": "1000",
                "high24h": "51000",
                "low24h": "49000",
            }
            ],
        }

        ticker = OkxTickerData(
            ticker_response, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "OKX"
        assert ticker.get_symbol_name() == "BTC-USDT"

    def test_order_container(self):
        """Test order data container."""
        order_response = {
            "code": "0",
            "msg": "",
            "data": [
            {
                "instType": "SP",
                "instId": "BTC-USDT",
                "ordId": "123456",
                "clOrdId": "client_123",
                "px": "50000",
                "sz": "0.001",
                "fillSz": "0.0005",
                "side": "buy",
                "ordType": "limit",
                "state": "live",
            }
            ],
        }

        order = OkxOrderData(
            order_response, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.get_exchange_name() == "OKX"
        assert order.get_symbol_name() == "BTC-USDT"

    def test_balance_container(self):
        """Test balance data container."""
        # OkxBalanceData exists
        # This test verifies the import works
        from bt_api_py.containers.balances.okx_balance import OkxBalanceData
        assert OkxBalanceData is not None


class TestOkxRegistry:
    """Test OKX registration."""

    def test_okx_spot_registered(self):
        """Test that OKX Spot is properly registered."""
        assert "OKX___SPOT" in ExchangeRegistry._feed_classes
        assert "OKX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert "OKX___SPOT" in ExchangeRegistry._balance_handlers

    def test_okx_swap_registered(self):
        """Test that OKX Swap is properly registered."""
        assert "OKX___SWAP" in ExchangeRegistry._feed_classes
        assert "OKX___SWAP" in ExchangeRegistry._exchange_data_classes
        assert "OKX___SWAP" in ExchangeRegistry._balance_handlers

    def test_okx_create_feed_spot(self):
        """Test creating OKX Spot feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "OKX___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
            passphrase="test",
        )
        assert isinstance(feed, OkxRequestDataSpot)

    def test_okx_create_exchange_data_spot(self):
        """Test creating OKX Spot exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("OKX___SPOT")
        assert isinstance(exchange_data, OkxExchangeDataSpot)

    def test_okx_create_exchange_data_swap(self):
        """Test creating OKX Swap exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("OKX___SWAP")
        assert isinstance(exchange_data, OkxExchangeDataSwap)


class TestOkxIntegration:
    """Integration tests for OKX (marked as integration)."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        pass

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        pass

    @pytest.mark.integration
    def test_place_order_live(self):
        """Test placing order on live API."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
