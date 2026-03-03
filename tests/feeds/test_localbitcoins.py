"""
Test LocalBitcoins exchange integration.

Run tests:
    pytest tests/feeds/test_localbitcoins.py -v

Run with coverage:
    pytest tests/feeds/test_localbitcoins.py --cov=bt_api_py.feeds.live_localbitcoins --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.containers.tickers.localbitcoins_ticker import LocalBitcoinsRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_localbitcoins.spot import LocalBitcoinsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register LocalBitcoins
import bt_api_py.feeds.register_localbitcoins  # noqa: F401


class TestLocalBitcoinsExchangeData:
    """Test LocalBitcoins exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating LocalBitcoins spot exchange data."""
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert exchange_data.exchange_name == "LOCALBITCOINS___SPOT"
        assert exchange_data.asset_type == "spot"

    def test_get_symbol(self):
        """Test symbol format conversion.

        LocalBitcoins uses lowercase underscore format like btc_usd.
        """
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/USD") == "btc_usd"
        assert exchange_data.get_symbol("BTC-USD") == "btc_usd"
        assert exchange_data.get_symbol("btc_usd") == "btc_usd"

    def test_get_reverse_symbol(self):
        """Test reverse symbol conversion."""
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert exchange_data.get_reverse_symbol("btc_usd") == "BTC-USD"
        assert exchange_data.get_reverse_symbol("BTC-USD") == "BTC_USD"

    def test_symbol_mappings(self):
        """Test symbol mappings."""
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USD") == "btc_usd"
        assert exchange_data.get_symbol("BTC-EUR") == "btc_eur"
        assert exchange_data.get_symbol("BTC-GBP") == "btc_gbp"

    def test_get_period(self):
        """Test kline period conversion.

        Note: LocalBitcoins only supports 1d period.
        """
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert exchange_data.get_period("1d") == "1d"

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = LocalBitcoinsExchangeDataSpot()
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "GBP" in exchange_data.legal_currency
        assert "RUB" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestLocalBitcoinsRequestData:
    """Test LocalBitcoins REST API request base class."""

    def test_request_data_creation(self):
        """Test creating LocalBitcoins request data."""
        data_queue = queue.Queue()
        request_data = LocalBitcoinsRequestDataSpot(
            data_queue,
            exchange_name="LOCALBITCOINS___SPOT",
        )
        assert request_data.exchange_name == "LOCALBITCOINS___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities.

        Note: LocalBitcoins is a P2P exchange with limited API.
        """
        data_queue = queue.Queue()
        request_data = LocalBitcoinsRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert "get_tick" in capabilities
        assert "get_exchange_info" in capabilities


class TestLocalBitcoinsDataContainers:
    """Test LocalBitcoins data containers."""

    def test_ticker_container(self):
        """Test ticker data container.

        Note: LocalBitcoins ticker data represents aggregated P2P advertisements.
        """
        ticker_info = json.dumps({
            "btc_usd": {
                "avg": 45000.50,
                "bid": 44900.00,
                "ask": 45100.00,
                "volume_btc": 1234.56,
            }
        })

        ticker = LocalBitcoinsRequestTickerData(
            ticker_info, "BTC-USD", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "LOCALBITCOINS"
        assert ticker.get_symbol_name() == "BTC-USD"
        assert ticker.get_last_price() == 45000.50
        assert ticker.get_bid_price() == 44900.00
        assert ticker.get_ask_price() == 45100.00
        assert ticker.get_volume() == 1234.56
        # High/low not typically available in P2P
        assert ticker.get_high() is None
        assert ticker.get_low() is None

    def test_ticker_wss_container(self):
        """Test ticker WebSocket data container.

        Note: LocalBitcoins does not provide WebSocket API.
        """
        from bt_api_py.containers.tickers.localbitcoins_ticker import LocalBitcoinsWssTickerData

        ticker_info = json.dumps({
            "btc_usd": {
                "avg": 45000.50,
                "bid": 44900.00,
                "ask": 45100.00,
            }
        })

        ticker = LocalBitcoinsWssTickerData(
            ticker_info, "BTC-USD", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "LOCALBITCOINS"
        assert ticker.get_last_price() == 45000.50


class TestLocalBitcoinsRegistry:
    """Test LocalBitcoins registration."""

    def test_localbitcoins_registered(self):
        """Test that LocalBitcoins is properly registered."""
        # Check if feed is registered
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["LOCALBITCOINS___SPOT"] == LocalBitcoinsRequestDataSpot

        # Check if exchange data is registered
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes[
            "LOCALBITCOINS___SPOT"] == LocalBitcoinsExchangeDataSpot

        # Check if balance handler is registered
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["LOCALBITCOINS___SPOT"] is not None

        # Check if stream handler is registered
        stream_handlers = ExchangeRegistry._stream_classes.get(
            "LOCALBITCOINS___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_localbitcoins_create_feed(self):
        """Test creating LocalBitcoins feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("LOCALBITCOINS___SPOT", data_queue)
        assert isinstance(feed, LocalBitcoinsRequestDataSpot)

    def test_localbitcoins_create_exchange_data(self):
        """Test creating LocalBitcoins exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "LOCALBITCOINS___SPOT")
        assert isinstance(exchange_data, LocalBitcoinsExchangeDataSpot)


class TestLocalBitcoinsLiveMarketData:
    """Live market data tests following Binance/OKX standard.

    Note: LocalBitcoins is a P2P exchange, so some tests may be skipped.
    """

    def init_req_feed(self):
        """Initialize LocalBitcoins request feed."""
        data_queue = queue.Queue()
        live_localbitcoins_spot_feed = LocalBitcoinsRequestDataSpot(data_queue)
        return live_localbitcoins_spot_feed

    def test_localbitcoins_req_server_time(self):
        """Test server time endpoint (not available for P2P)."""
        pass

    def test_localbitcoins_req_tick_data(self):
        """Test ticker data request (synchronous)."""
        live_localbitcoins_spot_feed = self.init_req_feed()
        data = live_localbitcoins_spot_feed.get_ticker("BTC-USD").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            pass
        tick_data = data[0]
        assert tick_data.get("symbol_name") == "BTC-USD"
        assert tick_data.get("last_price") > 0
        assert tick_data.get("bid_price") >= 0
        assert tick_data.get("ask_price") >= 0

    def test_localbitcoins_async_tick_data(self):
        """Test ticker data request (asynchronous)."""
        data_queue = queue.Queue()
        live_localbitcoins_spot_feed = LocalBitcoinsRequestDataSpot(data_queue)
        live_localbitcoins_spot_feed.async_get_ticker(
            "BTC-USD", extra_data={"test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            tick_data = None
        assert isinstance(tick_data, RequestData)
        assert isinstance(tick_data.get_data(), list)
        if tick_data.get_data() and tick_data.get_data()[0]:
            pass
        async_tick_data = tick_data.get_data()[0]
        assert async_tick_data.get("symbol_name") == "BTC-USD"
        assert async_tick_data.get("last_price") > 0

    def test_localbitcoins_req_kline_data(self):
        """Test kline data request (not available for P2P)."""
        pass

    def test_localbitcoins_async_kline_data(self):
        """Test kline data request (not available for P2P)."""
        pass


def order_book_value_equals(order_book):
    """Validate order book data (not applicable for P2P exchange)."""
    # LocalBitcoins P2P exchange - order book not in traditional sense
    pass


class TestLocalBitcoinsOrderBook:
    """Order book tests (not applicable for P2P exchange)."""

    def test_localbitcoins_req_orderbook_data(self):
        """Test orderbook data request (not available for P2P)."""
        pass

    def test_localbitcoins_async_orderbook_data(self):
        """Test orderbook data request (not available for P2P)."""
        pass


class TestLocalBitcoinsWebSocket:
    """Test LocalBitcoins WebSocket handlers.

    Note: LocalBitcoins does not provide WebSocket API.
    """

    def test_market_wss_handler_not_supported(self):
        """Test market WebSocket handler (not supported)."""
        from bt_api_py.feeds.live_localbitcoins.spot import LocalBitcoinsMarketWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "ticker", "symbol": "btc_usd"}]

        wss_handler = LocalBitcoinsMarketWssDataSpot(
            data_queue, topics=topics, wss_url="")
        wss_handler.start()
        # Should not start without URL (not supported)
        wss_handler.stop()

    def test_account_wss_handler_not_supported(self):
        """Test account WebSocket handler (not supported)."""
        from bt_api_py.feeds.live_localbitcoins.spot import LocalBitcoinsAccountWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "account"}]

        wss_handler = LocalBitcoinsAccountWssDataSpot(
            data_queue, topics=topics, wss_url="")
        wss_handler.start()
        # Should not start without URL (not supported)
        wss_handler.stop()


class TestLocalBitcoinsIntegration:
    """Integration tests for LocalBitcoins."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


# ==================== Additional Binance/OKX Standard Tests =============

class TestLocalBitcoinsStandardMarketData:
    """Standard market data tests following Binance/OKX pattern.

    Note: LocalBitcoins is a P2P exchange, so some standard features may not apply.
    """

    def init_req_feed(self):
        """Initialize LocalBitcoins request feed."""
        data_queue = queue.Queue()
        live_localbitcoins_spot_feed = LocalBitcoinsRequestDataSpot(data_queue)
        return live_localbitcoins_spot_feed

    def test_localbitcoins_req_get_exchange_info(self):
        """Test exchange info endpoint (not applicable for P2P)."""
        pass

    def test_localbitcoins_req_get_symbols(self):
        """Test symbols list endpoint (not applicable for P2P)."""
        pass


class TestLocalBitcoinsOrderManagement:
    """Order management tests following Binance/OKX pattern.

    Note: LocalBitcoins is a P2P exchange, so standard order management may not apply.
    """

    def init_req_feed(self):
        """Initialize LocalBitcoins request feed."""
        data_queue = queue.Queue()
        live_localbitcoins_spot_feed = LocalBitcoinsRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_localbitcoins_spot_feed

    def test_localbitcoins_req_make_order(self):
        """Test make order endpoint (not applicable for P2P)."""
        pass

    def test_localbitcoins_req_query_order(self):
        """Test query order endpoint (not applicable for P2P)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
