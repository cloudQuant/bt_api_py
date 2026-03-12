"""
Test Bybit exchange integration following Binance/OKX standards.

NOTE: These tests are structured following the Binance/OKX test standards.
However, the Bybit exchange implementation has some issues that prevent
the tests from running:
    pass
- RateLimiter enum issues

Run tests:
    pytest tests/feeds/test_live_bybit_request_data.py -v

Run with coverage:
    pytest tests/feeds/test_live_bybit_request_data.py --cov=bt_api_py.feeds.live_bybit --cov-report=term-missing
"""

import queue
import time

import pytest

from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot


def init_req_feed():
    """Initialize Bybit request feed."""
    data_queue = queue.Queue()
    live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
    return live_bybit_spot_feed


class TestBybitServerTime:
    """Test Bybit server time endpoint."""

    def test_bybit_req_server_time(self):
        """Test getting server time."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_server_time()
        assert data is not None
        print("server_time:", data)


class TestBybitTickData:
    """Test Bybit ticker data."""

    @pytest.mark.ticker
    def test_bybit_req_tick_data(self):
        """Test getting ticker data (synchronous)."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_ticker("BTCUSDT")
        assert data is not None
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, RequestData))

    @pytest.mark.ticker
    def test_bybit_async_tick_data(self):
        """Test getting ticker data (asynchronous)."""
        data_queue = queue.Queue()
        live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
        live_bybit_spot_feed.async_get_tick("BTCUSDT", extra_data={"test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            tick_data = None

        assert tick_data is not None


class TestBybitKlineData:
    """Test Bybit kline/candlestick data."""

    @pytest.mark.kline
    def test_bybit_req_kline_data(self):
        """Test getting kline data (synchronous)."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_kline("BTCUSDT", period="1", limit=2)
        assert data is not None
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    @pytest.mark.kline
    def test_bybit_async_kline_data(self):
        """Test getting kline data (asynchronous)."""
        data_queue = queue.Queue()
        live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
        live_bybit_spot_feed.async_get_kline(
            "BTCUSDT", period="1", count=3, extra_data={"test_async_kline_data": True}
        )
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            kline_data = None

        assert kline_data is not None


class TestBybitOrderBook:
    """Test Bybit order book data."""

    @pytest.mark.orderbook
    def test_bybit_req_orderbook_data(self):
        """Test getting order book data."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_depth("BTCUSDT", limit=20)
        assert data is not None
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    def order_book_value_equals(self, order_book_data):
        """Validate order book data structure and values."""
        assert order_book_data is not None

        # Bybit returns dict with b (bids) and a (asks)
        assert isinstance(order_book_data, dict), f"Expected dict, got {type(order_book_data)}"

        # Check for bids (b)
        if "b" in order_book_data:
            bids = order_book_data["b"]
            assert isinstance(bids, list), f"bids should be list, got {type(bids)}"
            if len(bids) > 0:
                first_bid = bids[0]
                # Bybit bid format: [price, volume]
                assert isinstance(first_bid, list), f"bid should be list, got {type(first_bid)}"
                assert len(first_bid) >= 2, f"bid should have price and volume, got {first_bid}"
                bid_price = float(first_bid[0])
                bid_volume = float(first_bid[1])
                assert bid_price > 0, f"Invalid bid_price: {bid_price}"
                assert bid_volume >= 0, f"Invalid bid_volume: {bid_volume}"

        # Check for asks (a)
        if "a" in order_book_data:
            asks = order_book_data["a"]
            assert isinstance(asks, list), f"asks should be list, got {type(asks)}"
            if len(asks) > 0:
                first_ask = asks[0]
                assert isinstance(first_ask, list), f"ask should be list, got {type(first_ask)}"
                assert len(first_ask) >= 2, f"ask should have price and volume, got {first_ask}"
                ask_price = float(first_ask[0])
                ask_volume = float(first_ask[1])
                assert ask_price > 0, f"Invalid ask_price: {ask_price}"
                assert ask_volume >= 0, f"Invalid ask_volume: {ask_volume}"

        # Verify b[0][0] <= a[0][0] (best bid <= best ask)
        if (
            "b" in order_book_data
            and "a" in order_book_data
            and len(order_book_data["b"]) > 0
            and len(order_book_data["a"]) > 0
        ):
            best_bid = float(order_book_data["b"][0][0])
            best_ask = float(order_book_data["a"][0][0])
            assert best_bid <= best_ask, f"best_bid ({best_bid}) should be <= best_ask ({best_ask})"

    @pytest.mark.orderbook
    def test_bybit_async_orderbook_data(self):
        """Test getting order book data (asynchronous)."""
        data_queue = queue.Queue()
        live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
        live_bybit_spot_feed.async_get_depth("BTCUSDT", 20)
        time.sleep(3)

        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            depth_data = None

        assert depth_data is not None


class TestBybitExchangeInfo:
    """Test Bybit exchange information."""

    def test_bybit_req_exchange_info(self):
        """Test getting exchange information."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_exchange_info(symbol="BTCUSDT")
        assert data is not None
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))


class TestBybitIntegration:
    """Integration tests for Bybit."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
