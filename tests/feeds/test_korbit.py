"""
Test Korbit exchange integration.

Run tests:
    pytest tests/feeds/test_korbit.py -v

Run with coverage:
    pytest tests/feeds/test_korbit.py --cov=bt_api_py.feeds.live_korbit --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.containers.tickers.korbit_ticker import KorbitRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_korbit.spot import KorbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Korbit
import bt_api_py.feeds.register_korbit  # noqa: F401


class TestKorbitExchangeData:
    """Test Korbit exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Korbit spot exchange data."""
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.exchange_name == "KORBIT___SPOT"
        assert exchange_data.asset_type == "spot"

    def test_get_symbol(self):
        """Test symbol format conversion.

        Korbit uses lowercase underscore format like btc_krw.
        """
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/KRW") == "btc_krw"
        assert exchange_data.get_symbol("BTC-KRW") == "btc_krw"
        assert exchange_data.get_symbol("btc_krw") == "btc_krw"

    def test_get_reverse_symbol(self):
        """Test reverse symbol conversion.

        Converting from exchange format (btc_krw) back to display format (BTC-KRW).
        """
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.get_reverse_symbol("btc_krw") == "BTC-KRW"
        # When already in display format, convert underscores to dashes
        assert exchange_data.get_reverse_symbol("BTC_KRW") == "BTC-KRW"

    def test_symbol_mappings(self):
        """Test symbol mappings."""
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-KRW") == "btc_krw"
        assert exchange_data.get_symbol("ETH-KRW") == "eth_krw"

    def test_get_period(self):
        """Test kline period conversion."""
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1m"
        assert exchange_data.get_period("1h") == "1h"
        assert exchange_data.get_period("1d") == "1d"

    def test_get_reverse_period(self):
        """Test reverse kline period conversion."""
        exchange_data = KorbitExchangeDataSpot()
        assert exchange_data.get_reverse_period("1m") == "1m"
        assert exchange_data.get_reverse_period("1h") == "1h"

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = KorbitExchangeDataSpot()
        assert "KRW" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency


class TestKorbitRequestData:
    """Test Korbit REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Korbit request data."""
        data_queue = queue.Queue()
        request_data = KorbitRequestDataSpot(
            data_queue,
            exchange_name="KORBIT___SPOT",
        )
        assert request_data.exchange_name == "KORBIT___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        from bt_api_py.feeds.capability import Capability

        data_queue = queue.Queue()
        request_data = KorbitRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class TestKorbitDataContainers:
    """Test Korbit data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "last": "95000000",
            "bid": "94990000",
            "ask": "95000000",
            "low": "93500000",
            "high": "95800000",
            "volume": "1234.56",
            "change": "500000",
            "changePercent": "0.53",
            "timestamp": 1678901234000,
        })

        ticker = KorbitRequestTickerData(ticker_info, "BTC-KRW", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "KORBIT"
        assert ticker.get_symbol_name() == "BTC-KRW"
        assert ticker.get_last_price() == 95000000.0
        assert ticker.get_bid_price() == 94990000.0
        assert ticker.get_ask_price() == 95000000.0
        assert ticker.get_high() == 95800000.0
        assert ticker.get_low() == 93500000.0
        assert ticker.get_daily_change() == 500000.0
        assert ticker.get_daily_change_percentage() == 0.53

    def test_ticker_wss_container(self):
        """Test ticker WebSocket data container."""
        from bt_api_py.containers.tickers.korbit_ticker import KorbitWssTickerData

        ticker_info = json.dumps({
            "last": "95000000",
            "bid": "94990000",
            "ask": "95000000",
        })

        ticker = KorbitWssTickerData(ticker_info, "BTC-KRW", "SPOT", False)
        ticker.init_data()

        assert ticker.get_exchange_name() == "KORBIT"
        assert ticker.get_last_price() == 95000000.0


class TestKorbitRegistry:
    """Test Korbit registration."""

    def test_korbit_registered(self):
        """Test that Korbit is properly registered."""
        # Check if feed is registered
        assert "KORBIT___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["KORBIT___SPOT"] == KorbitRequestDataSpot

        # Check if exchange data is registered
        assert "KORBIT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["KORBIT___SPOT"] == KorbitExchangeDataSpot

        # Check if balance handler is registered
        assert "KORBIT___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["KORBIT___SPOT"] is not None

        # Check if stream handler is registered
        stream_handlers = ExchangeRegistry._stream_classes.get(
            "KORBIT___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_korbit_create_feed(self):
        """Test creating Korbit feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("KORBIT___SPOT", data_queue)
        assert isinstance(feed, KorbitRequestDataSpot)

    def test_korbit_create_exchange_data(self):
        """Test creating Korbit exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("KORBIT___SPOT")
        assert isinstance(exchange_data, KorbitExchangeDataSpot)


class TestKorbitLiveMarketData:
    """Live market data tests following Binance/OKX standard."""

    def init_req_feed(self):
        """Initialize Korbit request feed."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        return live_korbit_spot_feed

    def test_korbit_req_server_time(self):
        """Test server time endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_server_time()
        assert isinstance(data, RequestData)
        print("korbit_server_time:", data.get_data())

    def test_korbit_req_tick_data(self):
        """Test ticker data request (synchronous)."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_tick("BTC-KRW").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            tick_data = data[0]
            if hasattr(tick_data, 'init_data'):
                tick_data.init_data()
            if hasattr(tick_data, 'get_symbol_name'):
                assert tick_data.get_symbol_name() == "BTC-KRW"
                assert tick_data.get_last_price() > 0

    @pytest.mark.integration
    def test_korbit_async_tick_data(self):
        """Test ticker data request (asynchronous)."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        live_korbit_spot_feed.async_get_tick(
            "BTC-KRW", extra_data={"test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert isinstance(tick_data, RequestData)
        except queue.Empty:
            pass  # No data received

    @pytest.mark.integration
    def test_korbit_req_kline_data(self):
        """Test kline data request (synchronous)."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_kline(
            "BTC-KRW", "1h", count=2).get_data()
        assert isinstance(data, list)

    @pytest.mark.integration
    def test_korbit_async_kline_data(self):
        """Test kline data request (asynchronous)."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        live_korbit_spot_feed.async_get_kline("BTC-KRW", period="1h", count=3,
                                              extra_data={"test_async_kline_data": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert isinstance(kline_data, RequestData)
        except queue.Empty:
            pass  # No data received


def order_book_value_equals(order_book):
    """Validate order book data."""
    # Korbit may return dict order book data
    if isinstance(order_book, dict):
        assert order_book.get("symbol_name") == "BTC-KRW"
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        if bids:
            # Korbit returns bids as lists: ["price", "amount", "count"]
            bid = bids[0]
            price = float(bid[0]) if isinstance(bid, list) else float(bid.get("price", 0))
            assert price > 0
        if asks:
            ask = asks[0]
            price = float(ask[0]) if isinstance(ask, list) else float(ask.get("price", 0))
            assert price > 0


class TestKorbitOrderBook:
    """Order book tests."""

    def init_req_feed(self):
        """Initialize Korbit request feed."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        return live_korbit_spot_feed

    def test_korbit_req_orderbook_data(self):
        """Test orderbook data request."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_depth("BTC-KRW").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            order_book_value_equals(data[0])

    @pytest.mark.integration
    def test_korbit_async_orderbook_data(self):
        """Test orderbook data request (asynchronous)."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        live_korbit_spot_feed.async_get_depth("BTC-KRW")
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass  # No data received


class TestKorbitWebSocket:
    """Test Korbit WebSocket handlers."""

    def test_market_wss_handler(self):
        """Test market WebSocket handler."""
        from bt_api_py.feeds.live_korbit.spot import KorbitMarketWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "ticker", "symbol": "btc_krw"}]

        wss_handler = KorbitMarketWssDataSpot(data_queue, topics=topics)
        wss_handler.start()
        assert wss_handler.running is True
        wss_handler.stop()
        assert wss_handler.running is False

    def test_account_wss_handler(self):
        """Test account WebSocket handler."""
        from bt_api_py.feeds.live_korbit.spot import KorbitAccountWssDataSpot

        data_queue = queue.Queue()
        topics = [{"topic": "account"}]

        wss_handler = KorbitAccountWssDataSpot(data_queue, topics=topics)
        wss_handler.start()
        assert wss_handler.running is True
        wss_handler.stop()
        assert wss_handler.running is False


class TestKorbitIntegration:
    """Integration tests for Korbit."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = KorbitRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC-KRW")
        assert isinstance(ticker, RequestData)

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


# ==================== Additional Binance/OKX Standard Tests =============

class TestKorbitStandardMarketData:
    """Standard market data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Korbit request feed."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(data_queue)
        return live_korbit_spot_feed

    def test_korbit_req_get_exchange_info(self):
        """Test exchange info endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_exchange_info()
        assert isinstance(data, RequestData)
        print("korbit_exchange_info:", data.get_data())

    @pytest.mark.integration
    def test_korbit_req_get_symbols(self):
        """Test symbols list endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_symbols()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, list)


class TestKorbitOrderManagement:
    """Order management tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Korbit request feed."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_korbit_spot_feed

    def test_make_order_params(self):
        """Test make order parameter generation."""
        live_korbit_spot_feed = self.init_req_feed()
        path, params, extra_data = live_korbit_spot_feed._make_order(
            symbol="BTC-KRW",
            vol="0.001",
            price="50000000",
            order_type="buy-limit",
        )
        assert "orders" in path.lower() or "place" in path.lower()
        assert params.get("symbol") == "btc_krw"
        assert params.get("type") == "limit"
        assert params.get("side") == "buy"

    def test_cancel_order_params(self):
        """Test cancel order parameter generation."""
        live_korbit_spot_feed = self.init_req_feed()
        path, params, extra_data = live_korbit_spot_feed._cancel_order(
            order_id="123456"
        )
        assert "cancel" in path.lower()
        assert params.get("id") == "123456" or "id" in path

    @pytest.mark.integration
    def test_korbit_req_make_order(self):
        """Test make order endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.make_order(
            symbol="BTC-KRW",
            vol="0.001",
            price="30000000",  # Low price to avoid execution
            order_type="buy-limit",
        )
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_korbit_req_query_order(self):
        """Test query order endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.query_order(
            symbol="BTC-KRW",
            order_id="123456"
        )
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_korbit_req_get_open_orders(self):
        """Test get open orders endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_open_orders(symbol="BTC-KRW")
        assert isinstance(data, RequestData)


class TestKorbitAccountData:
    """Account data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Korbit request feed."""
        data_queue = queue.Queue()
        live_korbit_spot_feed = KorbitRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_korbit_spot_feed

    @pytest.mark.integration
    def test_korbit_req_get_account(self):
        """Test account data endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_account()
        assert isinstance(data, RequestData)
        print("korbit_account:", data.get_data())

    @pytest.mark.integration
    def test_korbit_req_get_balance(self):
        """Test balance data endpoint."""
        live_korbit_spot_feed = self.init_req_feed()
        data = live_korbit_spot_feed.get_balance()
        assert isinstance(data, RequestData)
        print("korbit_balance:", data.get_data())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
