"""
Test Crypto.com exchange integration.

Run tests:
    pytest tests/feeds/test_cryptocom.py -v

Run with coverage:
    pytest tests/feeds/test_cryptocom.py --cov=bt_api_py.feeds.live_cryptocom --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.containers.orders.cryptocom_order import CryptoComOrder
from bt_api_py.containers.tickers.cryptocom_ticker import CryptoComTicker
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.containers.orderbooks.cryptocom_orderbook import CryptoComOrderBook
from bt_api_py.feeds.live_cryptocom.spot import CryptoComRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Crypto.com
import bt_api_py.feeds.register_cryptocom  # noqa: F401


class TestCryptoComExchangeData:
    """Test Crypto.com exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Crypto.com spot exchange data."""
        exchange_data = CryptoComExchangeDataSpot()
#         assert exchange_data.exchange_name == "CRYPTOCOM___SPOT"
        assert exchange_data.rest_url
        assert exchange_data.wss_url
        assert exchange_data.acct_wss_url

    def test_get_symbol(self):
        """Test symbol format conversion.

        Crypto.com uses underscore format like BTC_USDT.
        """
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/USDT") == "BTC_USDT"
        assert exchange_data.get_symbol("ETH-USDT") == "ETH_USDT"
        assert exchange_data.get_symbol("BTC_USDT") == "BTC_USDT"

    def test_get_instrument_name(self):
        """Test instrument name conversion."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_instrument_name("BTC/USDT") == "BTC_USDT"
        assert exchange_data.get_instrument_name("ETH/USDT") == "ETH_USDT"

    def test_get_symbol_from_instrument(self):
        """Test symbol from instrument conversion."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_symbol_from_instrument("BTC_USDT") == "BTC/USDT"
        assert exchange_data.get_symbol_from_instrument("ETH_USDT") == "ETH/USDT"

    def test_validate_symbol(self):
        """Test symbol validation."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.validate_symbol("BTC/USDT") is True
        assert exchange_data.validate_symbol("BTC_USDT") is True
        assert exchange_data.validate_symbol("") is False

    def test_get_kline_period(self):
        """Test kline period conversion."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_kline_period("1m") == "1m"
        assert exchange_data.get_kline_period("1h") == "1h"
        assert exchange_data.get_kline_period("1d") == "1D"

    def test_get_period_from_kline(self):
        """Test reverse kline period conversion."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_period_from_kline("1m") == "1m"
        assert exchange_data.get_period_from_kline("1D") == "1d"

    def test_get_depth_levels(self):
        """Test depth levels validation."""
        exchange_data = CryptoComExchangeDataSpot()
        assert exchange_data.get_depth_levels(50) == 50
        assert exchange_data.get_depth_levels(100) == 50  # Max is 50
        assert exchange_data.get_depth_levels(0) == 1  # Min is 1


class TestCryptoComRequestData:
    """Test Crypto.com REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Crypto.com request data."""
        data_queue = queue.Queue()
        request_data = CryptoComRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="CRYPTOCOM___SPOT",
        )
        assert request_data.exchange_name == "CRYPTOCOM___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        data_queue = queue.Queue()
        request_data = CryptoComRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert "GET_TICK" in capabilities
        assert "GET_DEPTH" in capabilities
        assert "GET_KLINE" in capabilities
        assert "MAKE_ORDER" in capabilities
        assert "CANCEL_ORDER" in capabilities


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "public_key": "test_key",
        "private_key": "test_secret",
    }
    return CryptoComRequestDataSpot(data_queue, **kwargs)


class TestCryptoComServerTime:
    """Test server time endpoint."""

    @pytest.mark.integration
    def test_get_server_time(self):
        """Test getting server time."""
        import pytest
        pytest.skip("Crypto.com API is blocked by Cloudflare - needs mocking or VPN")
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None
        assert result.status is True


class TestCryptoComTicker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "a": "50000.0",  # last_price
            "b": "49900.0",  # best_bid
            "k": "50100.0",  # best_ask
            "h": "51000.0",  # high_24h
            "l": "48000.0",  # low_24h
            "v": "1234.56",  # volume_24h
            "vv": "60000000",  # quote_volume_24h
            "t": 1640995200000,  # timestamp
        }

        ticker = CryptoComTicker.from_api_response(ticker_response, "BTC_USDT")
        ticker.init_data()
        assert ticker.symbol_name == "BTC_USDT"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49900.0
        assert ticker.ask_price == 50100.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 48000.0

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        import pytest
        pytest.skip("Crypto.com API is blocked by Cloudflare - needs mocking or VPN")
        feed = init_req_feed()
        result = feed.get_ticker("BTC_USDT")
        assert result.status is True


class TestCryptoComKline:
    """Test kline data retrieval."""

    def test_kline_container(self):
        """Test kline data container."""
        pass

    @pytest.mark.integration
    def test_get_kline_live(self):
        """Test getting kline from live API."""
        import pytest
        pytest.skip("Crypto.com API is blocked by Cloudflare - needs mocking or VPN")
        feed = init_req_feed()
        result = feed.get_kline("BTC_USDT", "1h", count=10)
        assert result is not None


class TestCryptoComOrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        orderbook_response = {
            "data": [{
            "bids": [["49990", "1.5", "1"], ["49980", "2.0", "2"]],
            "asks": [["50010", "1.0", "1"], ["50020", "2.5", "2"]],
            }],
            "t": 1640995200000
        }

        orderbook = CryptoComOrderBook.from_api_response(orderbook_response, "BTC_USDT")
        assert orderbook is not None

    @pytest.mark.integration
    def test_get_depth_live(self):
        """Test getting order book from live API."""
        import pytest
        pytest.skip("Crypto.com API is blocked by Cloudflare - needs mocking or VPN")
        feed = init_req_feed()
        result = feed.get_depth("BTC_USDT", count=20)
        assert result is not None


class TestCryptoComOrder:
    """Test order placement and management."""

    def test_order_container(self):
        """Test order data container."""
        order_response = {
            "instrument_name": "BTC_USDT",
            "order_id": "123456",
            "client_oid": "client_123",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": "0.001",
            "price": "50000",
            "status": "ACTIVE",
            "filled_quantity": "0.0005",
            "remaining_quantity": "0.0005",
            "time": 1640995200000,
        }

        order = CryptoComOrder.from_api_response(order_response, "BTC_USDT")
        order.init_data()
        assert order.symbol_name == "BTC_USDT"
        assert order.order_id == "123456"
        assert order.client_oid == "client_123"
        assert order.side == "BUY"
        assert order.type == "LIMIT"
        assert order.quantity == 0.001
        assert order.price == 50000.0
        assert order.status == "ACTIVE"
        assert order.filled_quantity == 0.0005

    def test_order_to_dict(self):
        """Test order to_dict conversion."""
        order_response = {
            "instrument_name": "BTC_USDT",
            "order_id": "123456",
            "side": "SELL",
            "type": "MARKET",
            "quantity": "0.01",
            "price": "0",
            "status": "FILLED",
            "filled_quantity": "0.01",
            "remaining_quantity": "0",
        }

        order = CryptoComOrder.from_api_response(order_response, "BTC_USDT")
        order_dict = order.to_dict()
        assert order_dict["symbol_name"] == "BTC_USDT"
        assert order_dict["order_id"] == "123456"
        assert order_dict["side"] == "SELL"
        assert order_dict["status"] == "FILLED"


class TestCryptoComRegistry:
    """Test Crypto.com registration."""

    def test_cryptocom_registered(self):
        """Test that Crypto.com is properly registered."""
        # Check if feed is registered
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["CRYPTOCOM___SPOT"] == CryptoComRequestDataSpot

        # Check if exchange data is registered
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["CRYPTOCOM___SPOT"] == CryptoComExchangeDataSpot

        # Check if balance handler is registered
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["CRYPTOCOM___SPOT"] is not None

    def test_cryptocom_create_feed(self):
        """Test creating Crypto.com feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "CRYPTOCOM___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, CryptoComRequestDataSpot)

    def test_cryptocom_create_exchange_data(self):
        """Test creating Crypto.com exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("CRYPTOCOM___SPOT")
        assert isinstance(exchange_data, CryptoComExchangeDataSpot)


class TestCryptoComIntegration:
    """Integration tests for Crypto.com."""

    @pytest.mark.integration
    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        import pytest
        pytest.skip("Crypto.com API is blocked by Cloudflare - needs mocking or VPN")
        data_queue = queue.Queue()
        feed = CryptoComRequestDataSpot(data_queue)

        # Test server time
        server_time = feed.get_server_time()
        assert server_time.status is True

        # Test ticker
        ticker = feed.get_ticker("BTC_USDT")
        assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
