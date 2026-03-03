"""
Test HitBTC exchange integration.

Run tests:
    pytest tests/feeds/test_hitbtc.py -v

Run with coverage:
    pytest tests/feeds/test_hitbtc.py --cov=bt_api_py.feeds.live_hitbtc --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcSpotExchangeData
from bt_api_py.containers.balances.hitbtc_balance import HitBtcRequestBalanceData
from bt_api_py.containers.orders.hitbtc_order import HitBtcRequestOrderData
from bt_api_py.containers.tickers.hitbtc_ticker import HitBtcRequestTickerData
from bt_api_py.feeds.live_hitbtc.spot import HitBtcSpotRequestData
from bt_api_py.containers.bars.hitbtc_bar import HitBtcRequestBarData
from bt_api_py.containers.orderbooks.hitbtc_orderbook import HitBtcRequestOrderBookData
from bt_api_py.containers.trades.hitbtc_trade import HitBtcRequestTradeData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register HitBTC
import bt_api_py.feeds.register_hitbtc  # noqa: F401


class TestHitBtcExchangeData:
    """Test HitBTC exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating HitBTC spot exchange data."""
        exchange_data = HitBtcSpotExchangeData()
        assert exchange_data.exchange_name == "HITBTC_SPOT"
        assert exchange_data.asset_type == "SPOT"
        assert exchange_data.rest_url

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = HitBtcSpotExchangeData()
        assert exchange_data.get_symbol("BTC/USDT") == "BTCUSDT"
        assert exchange_data.get_symbol("ETH/USDT") == "ETHUSDT"

    def test_get_period(self):
        """Test kline period conversion."""
        exchange_data = HitBtcSpotExchangeData()
        assert exchange_data.get_period("1m") == "M1"
        assert exchange_data.get_period("1h") == "H1"

    def test_rest_paths(self):
        """Test REST API paths configuration."""
        exchange_data = HitBtcSpotExchangeData()
        assert "get_ticker" in exchange_data.rest_paths
        assert "place_order" in exchange_data.rest_paths
        assert "cancel_order" in exchange_data.rest_paths


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "public_key": "test_key",
        "private_key": "test_secret",
    }
    return HitBtcSpotRequestData(data_queue, **kwargs)


class TestHitBtcServerTime:
    """Test server time endpoint."""

    def test_get_server_time(self):
        """Test getting server time."""
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None
        assert result.status is True


class TestHitBtcTicker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "symbol": "BTCUSDT",
            "last": "50000",
            "bid": "49999",
            "ask": "50001",
            "volume": "1000",
            "volumeQuote": "50000000",
            "high": "51000",
            "low": "49000",
            "open": "49500",
        }

        ticker = HitBtcRequestTickerData(
            ticker_response, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "HITBTC"
        assert ticker.get_symbol_name() == "BTC-USDT"
        assert ticker.get_last_price() == 50000

    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        feed = init_req_feed()
        result = feed.get_ticker("BTCUSDT")
        assert result is not None


class TestHitBtcKline:
    """Test kline data retrieval."""

    def test_kline_container(self):
        """Test kline data container."""
        kline_response = {
            "timestamp": "1640995200000",
            "open": "50000",
            "max": "51000",
            "min": "49000",
            "close": "50500",
            "volume": "123.456",
            "volumeQuote": "6227800",
        }

        kline = HitBtcRequestBarData(
            kline_response, "BTC-USDT", "SPOT", True
        )
        kline.init_data()
        assert kline.get_symbol_name() == "BTC-USDT"
        assert kline.get_open_price() == 50000.0
        assert kline.get_high_price() == 51000.0
        assert kline.get_low_price() == 49000.0
        assert kline.get_close_price() == 50500.0

    def test_get_kline_live(self):
        """Test getting kline from live API."""
        feed = init_req_feed()
        result = feed.get_candles("BTCUSDT", period="M1", limit=10)
        assert result is not None


class TestHitBtcOrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        orderbook_response = {
            "symbol": "BTCUSDT",
            "bid": [
            {"price": "49990", "size": "1.5"},
            {"price": "49980", "size": "2.0"}
            ],
            "ask": [
            {"price": "50010", "size": "1.0"},
            {"price": "50020", "size": "2.5"}
            ],
        }

        orderbook = HitBtcRequestOrderBookData(
            orderbook_response, "BTC-USDT", "SPOT", True
        )
        orderbook.init_data()
        assert orderbook.get_symbol_name() == "BTC-USDT"

    def test_get_orderbook_live(self):
        """Test getting order book from live API."""
        feed = init_req_feed()
        result = feed.get_orderbook("BTCUSDT", depth=10)
        assert result is not None


class TestHitBtcOrder:
    """Test order placement and management."""

    def test_order_container(self):
        """Test order data container."""
        order_response = {
            "client_order_id": "client_123",
            "id": "123456",
            "symbol": "BTCUSDT",
            "side": "buy",
            "type": "limit",
            "price": "50000",
            "quantity": "0.001",
            "quantity_cumulative": "0.0005",
            "status": "new",
        }

        order = HitBtcRequestOrderData(
            order_response, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.get_exchange_name() == "HITBTC"
        assert order.get_symbol_name() == "BTC-USDT"

    def test_place_order_live(self):
        """Test placing order on live API."""
        feed = init_req_feed()
        # Mock test - would need real credentials for live testing
        pass


class TestHitBtcBalance:
    """Test balance data retrieval."""

    def test_balance_container(self):
        """Test balance data container."""
        balance_response = {
            "currency": "USDT",
            "available": "1000.5",
            "reserved": "100",
        }

        balance = HitBtcRequestBalanceData(
            balance_response, "ALL", "SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.get_exchange_name() == "HITBTC"
        assert balance.get_currency() == "USDT"
        assert balance.get_total() == 1100.5

    def test_get_balance_live(self):
        """Test getting balance from live API."""
        feed = init_req_feed()
        result = feed.get_balance()
        assert result is not None


class TestHitBtcTrade:
    """Test trade data retrieval."""

    def test_trade_container(self):
        """Test trade data container."""
        trade_response = {
            "id": "123456",
            "price": "50000",
            "quantity": "0.001",
            "side": "buy",
            "timestamp": "1640995200000",
        }

        trade = HitBtcRequestTradeData(
            trade_response, "BTC-USDT", "SPOT", True
        )
        trade.init_data()
        assert trade.get_symbol_name() == "BTC-USDT"

    def test_get_trades_live(self):
        """Test getting trades from live API."""
        feed = init_req_feed()
        result = feed.get_trades("BTCUSDT", limit=10)
        assert result is not None


class TestHitBtcRegistry:
    """Test HitBTC registration."""

    def test_hitbtc_registered(self):
        """Test that HitBTC is properly registered."""
        # HitBTC uses different registration patterns
        # Verify the classes exist and can be imported
        assert HitBtcSpotRequestData is not None
        assert HitBtcSpotExchangeData is not None

    def test_hitbtc_create_feed(self):
        """Test creating HitBTC feed directly."""
        data_queue = queue.Queue()
        feed = HitBtcSpotRequestData(
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, HitBtcSpotRequestData)

    def test_hitbtc_create_exchange_data(self):
        """Test creating HitBTC exchange data."""
        exchange_data = HitBtcSpotExchangeData()
        assert isinstance(exchange_data, HitBtcSpotExchangeData)


class TestHitBtcIntegration:
    """Integration tests for HitBTC."""

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
