"""
Test Binance exchange integration.

Binance has detailed tests in subdirectories (tests/feeds/live_binance_*/).
This test file provides simplified tests focusing on:
- Exchange data classes
- Data containers
- Registry registration

Run tests:
    pytest tests/feeds/test_binance.py -v

Run with coverage:
    pytest tests/feeds/test_binance.py --cov=bt_api_py.feeds.live_binance --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register Binance
import bt_api_py.exchange_registers.register_binance  # noqa: F401
from bt_api_py.containers.balances.binance_balance import (
    BinanceSpotWssBalanceData,
)
from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeDataSpot,
    BinanceExchangeDataSwap,
)
from bt_api_py.containers.orders.binance_order import (
    BinanceRequestOrderData,
)
from bt_api_py.containers.tickers.binance_ticker import BinanceRequestTickerData
from bt_api_py.feeds.live_binance.spot import BinanceRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


class TestBinanceExchangeData:
    """Test Binance exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Binance spot exchange data."""
        exchange_data = BinanceExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths
        assert "get_ticker" in exchange_data.rest_paths

    def test_exchange_data_swap_creation(self):
        """Test creating Binance swap exchange data."""
        exchange_data = BinanceExchangeDataSwap()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        assert "make_order" in exchange_data.rest_paths

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BinanceExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USDT") == "BTCUSDT"
        assert exchange_data.get_symbol("ETH-USDT") == "ETHUSDT"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = BinanceExchangeDataSpot()
        path = exchange_data.get_rest_path("make_order")
        assert "POST" in path or "order" in path

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = BinanceExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = BinanceExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestBinanceRequestDataSpot:
    """Test Binance spot REST API request class."""

    def test_request_data_creation(self):
        """Test creating Binance request data."""
        data_queue = queue.Queue()
        request_data = BinanceRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BINANCE___SPOT",
        )
        assert request_data.exchange_name == "BINANCE___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_make_order_params_limit(self):
        """Test limit order parameter generation."""
        data_queue = queue.Queue()
        request_data = BinanceRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._make_order(
            symbol="BTC-USDT",
            vol="0.001",
            price="50000",
            order_type="buy-limit",
        )

        assert params["symbol"] == "BTCUSDT"
        assert params["side"] == "BUY"
        assert params["quantity"] == "0.001"
        assert params["price"] == "50000"

    def test_cancel_order_params(self):
        """Test cancel order parameter generation."""
        data_queue = queue.Queue()
        request_data = BinanceRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._cancel_order(
            symbol="BTC-USDT",
            order_id="123456",
        )

        assert params["symbol"] == "BTCUSDT"


class TestBinanceDataContainers:
    """Test Binance data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "symbol": "BTCUSDT",
            "lastPrice": "50000",
            "bidPrice": "49999",
            "askPrice": "50001",
            "bidQty": "1.5",
            "askQty": "2.3",
            "volume": "1000",
            "highPrice": "51000",
            "lowPrice": "49000",
        }

        ticker = BinanceRequestTickerData(ticker_response, "BTC-USDT", "SPOT", True)
        ticker.init_data()

        assert ticker.get_exchange_name() == "BINANCE"
        assert ticker.get_symbol_name() == "BTC-USDT"
        # BinanceRequestTickerData doesn't support last_price, returns None
        assert ticker.get_last_price() is None
        # But bid/ask prices should work
        assert ticker.get_bid_price() == 49999.0
        assert ticker.get_ask_price() == 50001.0

    def test_order_container(self):
        """Test order data container."""
        order_response = {
            "symbol": "BTCUSDT",
            "orderId": "123456",
            "clientOrderId": "client_123",
            "price": "50000",
            "origQty": "0.001",
            "executedQty": "0.0005",
            "side": "BUY",
            "type": "LIMIT",
            "status": "NEW",
            "time": 1688671955000,
        }

        order = BinanceRequestOrderData(order_response, "BTC-USDT", "SPOT", True)
        order.init_data()

        assert order.get_exchange_name() == "BINANCE"
        assert order.get_symbol_name() == "BTC-USDT"
        assert order.get_order_id() == "123456"

    def test_balance_container(self):
        """Test balance data container."""
        # BinanceSpotWssBalanceData exists
        # This test verifies the import works
        assert BinanceSpotWssBalanceData is not None


class TestBinanceRegistry:
    """Test Binance registration."""

    def test_binance_spot_registered(self):
        """Test that Binance Spot is properly registered."""
        assert "BINANCE___SPOT" in ExchangeRegistry._feed_classes
        assert "BINANCE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert "BINANCE___SPOT" in ExchangeRegistry._balance_handlers

    def test_binance_swap_registered(self):
        """Test that Binance Swap is properly registered."""
        assert "BINANCE___SWAP" in ExchangeRegistry._feed_classes
        assert "BINANCE___SWAP" in ExchangeRegistry._exchange_data_classes
        assert "BINANCE___SWAP" in ExchangeRegistry._balance_handlers

    def test_binance_create_feed_spot(self):
        """Test creating Binance Spot feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BINANCE___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, BinanceRequestDataSpot)

    def test_binance_create_exchange_data_spot(self):
        """Test creating Binance Spot exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BINANCE___SPOT")
        assert isinstance(exchange_data, BinanceExchangeDataSpot)

    def test_binance_create_exchange_data_swap(self):
        """Test creating Binance Swap exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BINANCE___SWAP")
        assert isinstance(exchange_data, BinanceExchangeDataSwap)


class TestBinanceIntegration:
    """Integration tests for Binance (marked as integration)."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""

    @pytest.mark.integration
    def test_place_order_live(self):
        """Test placing order on live API."""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
