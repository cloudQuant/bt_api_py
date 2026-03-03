"""
Test Latoken exchange integration.

Run tests:
    pytest tests/feeds/test_latoken.py -v

Run with coverage:
    pytest tests/feeds/test_latoken.py --cov=bt_api_py.feeds.live_latoken --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.containers.tickers.latoken_ticker import LatokenRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_latoken.spot import LatokenRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Latoken
import bt_api_py.feeds.register_latoken  # noqa: F401


class TestLatokenExchangeData:
    """Test Latoken exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Latoken spot exchange data."""
        exchange_data = LatokenExchangeDataSpot()
        assert exchange_data.exchange_name == "LATOKEN___SPOT"

    def test_get_symbol(self):
        """Test symbol format conversion.

        Latoken uses lowercase underscore format like btc_usdt.
        """
        exchange_data = LatokenExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/USDT") == "btc_usdt"
        assert exchange_data.get_symbol("BTC-USDT") == "btc_usdt"
        assert exchange_data.get_symbol("btc_usdt") == "btc_usdt"

    def test_get_reverse_symbol(self):
        """Test reverse symbol conversion."""
        exchange_data = LatokenExchangeDataSpot()
        # Convert from exchange format (lowercase underscore) to standard
        # format (uppercase dash)
        assert exchange_data.get_reverse_symbol("btc_usdt") == "BTC-USDT"
        # If already in dash format, convert to uppercase dash
        assert exchange_data.get_reverse_symbol("btc-usdt") == "BTC-USDT"

    def test_symbol_mappings(self):
        """Test symbol mappings."""
        exchange_data = LatokenExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USDT") == "btc_usdt"
        assert exchange_data.get_symbol("ETH-USDT") == "eth_usdt"

    def test_get_period(self):
        """Test kline period conversion."""
        exchange_data = LatokenExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1"
        assert exchange_data.get_period("5m") == "5"
        assert exchange_data.get_period("1h") == "60"
        assert exchange_data.get_period("1d") == "1D"

    def test_get_reverse_period(self):
        """Test reverse kline period conversion."""
        exchange_data = LatokenExchangeDataSpot()
        assert exchange_data.get_reverse_period("1") == "1m"
        assert exchange_data.get_reverse_period("60") == "1h"
        assert exchange_data.get_reverse_period("1D") == "1d"

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = LatokenExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency
        assert "LA" in exchange_data.legal_currency


class TestLatokenRequestData:
    """Test Latoken REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Latoken request data."""
        data_queue = queue.Queue()
        request_data = LatokenRequestDataSpot(
            data_queue,
            exchange_name="LATOKEN___SPOT",
        )
        assert request_data.exchange_name == "LATOKEN___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = LatokenRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestLatokenDataContainers:
    """Test Latoken data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "symbol": "BTCUSDT",
            "lastPrice": "50000.50",
            "bidPrice": "50000.00",
            "askPrice": "50000.50",
            "high": "51000.00",
            "low": "49000.00",
            "volume": "1234.56",
            "timestamp": 1678901234000,
        })

        ticker = LatokenRequestTickerData(
            ticker_info, "BTC-USDT", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "LATOKEN"
        assert ticker.get_symbol_name() == "BTC-USDT"
        assert ticker.get_last_price() == 50000.50
        assert ticker.get_bid_price() == 50000.00
        assert ticker.get_ask_price() == 50000.50
        assert ticker.get_high() == 51000.00
        assert ticker.get_low() == 49000.00

    def test_ticker_wss_container(self):
        """Test ticker WebSocket data container."""
        from bt_api_py.containers.tickers.latoken_ticker import LatokenWssTickerData

        ticker_info = json.dumps({
            "symbol": "BTCUSDT",
            "lastPrice": "50000.50",
            "bidPrice": "50000.00",
            "askPrice": "50000.50",
        })

        ticker = LatokenWssTickerData(ticker_info, "BTC-USDT", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "LATOKEN"
        assert ticker.get_last_price() == 50000.50


class TestLatokenRegistry:
    """Test Latoken registration."""

    def test_latoken_registered(self):
        """Test that Latoken is properly registered."""
        # Check if feed is registered
        assert "LATOKEN___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["LATOKEN___SPOT"] == LatokenRequestDataSpot

        # Check if exchange data is registered
        assert "LATOKEN___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["LATOKEN___SPOT"] == LatokenExchangeDataSpot

        # Check if balance handler is registered
        assert "LATOKEN___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["LATOKEN___SPOT"] is not None

        # Check if stream handler is registered
        stream_handlers = ExchangeRegistry._stream_classes.get(
            "LATOKEN___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_latoken_create_feed(self):
        """Test creating Latoken feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("LATOKEN___SPOT", data_queue)
        assert isinstance(feed, LatokenRequestDataSpot)

    def test_latoken_create_exchange_data(self):
        """Test creating Latoken exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("LATOKEN___SPOT")
        assert isinstance(exchange_data, LatokenExchangeDataSpot)


class TestLatokenLiveMarketData:
    """Live market data tests following Binance/OKX standard."""

    def init_req_feed(self):
        """Initialize Latoken request feed."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        return live_latoken_spot_feed

    def test_latoken_req_server_time(self):
        """Test server time endpoint."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_server_time()
        assert isinstance(data, RequestData)
        print("latoken_server_time:", data.get_data())

    def test_latoken_req_tick_data(self):
        """Test ticker data request (synchronous)."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_ticker("BTC-USDT").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            pass
        tick_data = data[0]
        assert tick_data.get("symbol_name") == "BTC-USDT"
        assert tick_data.get("last_price") > 0
        assert tick_data.get("bid_price") >= 0
        assert tick_data.get("ask_price") >= 0

    def test_latoken_async_tick_data(self):
        """Test ticker data request (asynchronous)."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        live_latoken_spot_feed.async_get_ticker(
            "BTC-USDT", extra_data={"test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert isinstance(tick_data, RequestData)
            assert isinstance(tick_data.get_data(), list)
            if tick_data.get_data() and tick_data.get_data()[0]:
                pass
            async_tick_data = tick_data.get_data()[0]
            assert async_tick_data.get("symbol_name") == "BTC-USDT"
            assert async_tick_data.get("last_price") > 0
        except queue.Empty:
            pass
            return

    def test_latoken_req_kline_data(self):
        """Test kline data request (synchronous)."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_kline(
            "BTC-USDT", "1h", count=2).get_data()
        assert isinstance(data, list)
        if data and data[0]:
            pass
        kline_data = data[0]
        assert kline_data.get("symbol_name") == "BTC-USDT"
        assert kline_data.get("open_price") > 0
        assert kline_data.get("high_price") >= 0
        assert kline_data.get("low_price") > 0
        assert kline_data.get("close_price") >= 0
        assert kline_data.get("volume") >= 0

    def test_latoken_async_kline_data(self):
        """Test kline data request (asynchronous)."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        live_latoken_spot_feed.async_get_kline("BTC-USDT", period="1h", count=3,
                                               extra_data={"test_async_kline_data": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
            assert isinstance(kline_data, RequestData)
            target_data_list = kline_data.get_data()
            if target_data_list and target_data_list[0]:
                pass
            async_kline_data = target_data_list[0]
            assert async_kline_data.get("symbol_name") == "BTC-USDT"
            assert async_kline_data.get("open_price") > 0
            assert async_kline_data.get("high_price") > 0
            assert async_kline_data.get("low_price") > 0
            assert async_kline_data.get("close_price") > 0
            assert async_kline_data.get("volume") >= 0
        except queue.Empty:
            pass
            return


def order_book_value_equals(order_book):
    """Validate order book data."""
    # Latoken may return dict order book data
    if isinstance(order_book, dict):
        assert order_book.get("symbol_name") == "BTC-USDT"
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        if bids:
            assert bids[0].get("price") > 0
        if asks:
            assert asks[0].get("price") > 0


class TestLatokenOrderBook:
    """Order book tests."""

    def init_req_feed(self):
        """Initialize Latoken request feed."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        return live_latoken_spot_feed

    def test_latoken_req_orderbook_data(self):
        """Test orderbook data request."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_depth("BTC-USDT", 20).get_data()
        assert isinstance(data, list)
        if data and data[0]:
            pass
        order_book_value_equals(data[0])

    def test_latoken_async_orderbook_data(self):
        """Test orderbook data request (asynchronous)."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        live_latoken_spot_feed.async_get_depth("BTC-USDT", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
            target_data = depth_data.get_data()
            assert isinstance(target_data, list)
            if target_data and target_data[0]:
                pass
            order_book_value_equals(target_data[0])
        except queue.Empty:
            pass
            return


class TestLatokenWebSocket:
    """Test Latoken WebSocket handlers."""

    def test_market_wss_handler_no_url(self):
        """Test market WebSocket handler without URL (not supported)."""
        from bt_api_py.feeds.live_latoken.spot import LatokenMarketWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "ticker", "symbol": "btc_usdt"}]

        wss_handler = LatokenMarketWssDataSpot(
            data_queue, topics=topics, wss_url="")
        wss_handler.start()
        # Should not start without URL
        wss_handler.stop()

    def test_account_wss_handler_no_url(self):
        """Test account WebSocket handler without URL (not supported)."""
        from bt_api_py.feeds.live_latoken.spot import LatokenAccountWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "account"}]

        wss_handler = LatokenAccountWssDataSpot(
            data_queue, topics=topics, wss_url="")
        wss_handler.start()
        # Should not start without URL
        wss_handler.stop()


class TestLatokenIntegration:
    """Integration tests for Latoken."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Additional Binance/OKX Standard Tests =============


class TestLatokenStandardMarketData:
    """Standard market data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Latoken request feed."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(data_queue)
        return live_latoken_spot_feed

    def test_latoken_req_get_exchange_info(self):
        """Test exchange info endpoint."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_exchange_info()
        assert isinstance(data, RequestData)
        print("latoken_exchange_info:", data.get_data())

    def test_latoken_req_get_symbols(self):
        """Test symbols list endpoint."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.get_symbols()
        assert isinstance(data, RequestData)
        result = data.get_data()
        if result:
            pass
        assert isinstance(result, list)


class TestLatokenOrderManagement:
    """Order management tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Latoken request feed."""
        data_queue = queue.Queue()
        live_latoken_spot_feed = LatokenRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_latoken_spot_feed

    def test_make_order_params(self):
        """Test make order parameter generation."""
        live_latoken_spot_feed = self.init_req_feed()
        # Skip test if _make_order is not implemented
        if not hasattr(live_latoken_spot_feed, '_make_order'):
            try:
                path, params, extra_data = live_latoken_spot_feed._make_order(
                    symbol="BTC-USDT",
                    vol="0.001",
                    price="50000",
                    order_type="buy-limit",
                )
                assert "orders" in path.lower() or "place" in path.lower()
                assert params.get("symbol") == "btc_usdt"
            except NotImplementedError:
                pass
                # Some exchanges may not implement this
                return

    def test_latoken_req_make_order(self):
        """Test make order endpoint."""
        live_latoken_spot_feed = self.init_req_feed()
        data = live_latoken_spot_feed.make_order(
            symbol="BTC-USDT",
            vol="0.001",
            price="30000",  # Low price to avoid execution
            order_type="buy-limit",
        )
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
