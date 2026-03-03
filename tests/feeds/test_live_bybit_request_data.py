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

from bt_api_py.containers.tickers.bybit_ticker import BybitSpotTickerData
from bt_api_py.containers.orderbooks.bybit_orderbook import BybitSpotOrderBookData
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

    def test_bybit_req_tick_data(self):
        """Test getting ticker data (synchronous)."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_ticker("BTCUSDT")
        assert data is not None

        # Bybit returns dict with retCode, retMsg, result
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "retCode" in data
        assert data["retCode"] == 0, f"API returned error: {data.get('retMsg')}"

        result = data.get("result", {})
        list_data = result.get("list", [])

        if len(list_data) > 0:
            pass
        ticker = list_data[0]
        assert isinstance(ticker, dict)

        # Verify symbol
        symbol = ticker.get("symbol")
        assert symbol == "BTCUSDT", f"Expected BTCUSDT, got {symbol}"

        # Verify price fields
        last_price = ticker.get("lastPrice")
        assert last_price is None or float(
            last_price) > 0, f"Invalid last_price: {last_price}"

        bid1_price = ticker.get("bid1Price")
        assert bid1_price is None or float(
            bid1_price) >= 0, f"Invalid bid1Price: {bid1_price}"

        ask1_price = ticker.get("ask1Price")
        assert ask1_price is None or float(
            ask1_price) >= 0, f"Invalid ask1Price: {ask1_price}"

        print("tick_data:", ticker)

    def test_bybit_async_tick_data(self):
        """Test getting ticker data (asynchronous)."""
        data_queue = queue.Queue()
        live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
        live_bybit_spot_feed.async_get_tick(
            "BTCUSDT", extra_data={
                "test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            tick_data = None

        assert tick_data is not None


class TestBybitKlineData:
    """Test Bybit kline/candlestick data."""

    def test_bybit_req_kline_data(self):
        """Test getting kline data (synchronous)."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_klines("BTCUSDT", "1", limit=2)
        assert data is not None

        # Bybit returns dict with retCode, retMsg, result
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "retCode" in data
        assert data["retCode"] == 0, f"API returned error: {data.get('retMsg')}"

        result = data.get("result", {})
        list_data = result.get("list", [])

        if len(list_data) > 0:
            pass
        first_kline = list_data[0]

        # Bybit candle format: [timestamp, open, high, low, close, volume,
        # turnover]
        assert isinstance(
            first_kline, list), f"Expected list, got {type(first_kline)}"
        assert len(
            first_kline) >= 6, f"Expected at least 6 fields, got {len(first_kline)}"

        timestamp = int(first_kline[0])
        open_price = float(first_kline[1])
        high_price = float(first_kline[2])
        low_price = float(first_kline[3])
        close_price = float(first_kline[4])
        volume = float(first_kline[5])

        assert timestamp > 0, f"Invalid timestamp: {timestamp}"
        assert open_price > 0, f"Invalid open_price: {open_price}"
        assert high_price >= low_price, f"high ({high_price}) should be >= low ({low_price})"
        assert close_price > 0, f"Invalid close_price: {close_price}"
        assert volume >= 0, f"Invalid volume: {volume}"

        print("kline_data:", list_data)

    def test_bybit_async_kline_data(self):
        """Test getting kline data (asynchronous)."""
        data_queue = queue.Queue()
        live_bybit_spot_feed = BybitRequestDataSpot(data_queue)
        live_bybit_spot_feed.async_get_kline("BTCUSDT", period="1", count=3,
                                             extra_data={"test_async_kline_data": True})
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            kline_data = None

        assert kline_data is not None


class TestBybitOrderBook:
    """Test Bybit order book data."""

    def test_bybit_req_orderbook_data(self):
        """Test getting order book data."""
        live_bybit_spot_feed = init_req_feed()
        data = live_bybit_spot_feed.get_orderbook("BTCUSDT", limit=20)
        assert data is not None

        # Bybit returns dict with retCode, retMsg, result
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "retCode" in data
        assert data["retCode"] == 0, f"API returned error: {data.get('retMsg')}"

        result = data.get("result", {})
        self.order_book_value_equals(result)
        print("orderbook_data:", result)

    def order_book_value_equals(self, order_book_data):
        """Validate order book data structure and values."""
        assert order_book_data is not None

        # Bybit returns dict with b (bids) and a (asks)
        assert isinstance(
            order_book_data, dict), f"Expected dict, got {type(order_book_data)}"

        # Check for bids (b)
        if "b" in order_book_data:
            bids = order_book_data["b"]
            assert isinstance(
                bids, list), f"bids should be list, got {type(bids)}"
            if len(bids) > 0:
                first_bid = bids[0]
                # Bybit bid format: [price, volume]
                assert isinstance(
                    first_bid, list), f"bid should be list, got {type(first_bid)}"
                assert len(
                    first_bid) >= 2, f"bid should have price and volume, got {first_bid}"
                bid_price = float(first_bid[0])
                bid_volume = float(first_bid[1])
                assert bid_price > 0, f"Invalid bid_price: {bid_price}"
                assert bid_volume >= 0, f"Invalid bid_volume: {bid_volume}"

        # Check for asks (a)
        if "a" in order_book_data:
            asks = order_book_data["a"]
            assert isinstance(
                asks, list), f"asks should be list, got {type(asks)}"
            if len(asks) > 0:
                first_ask = asks[0]
                assert isinstance(
                    first_ask, list), f"ask should be list, got {type(first_ask)}"
                assert len(
                    first_ask) >= 2, f"ask should have price and volume, got {first_ask}"
                ask_price = float(first_ask[0])
                ask_volume = float(first_ask[1])
                assert ask_price > 0, f"Invalid ask_price: {ask_price}"
                assert ask_volume >= 0, f"Invalid ask_volume: {ask_volume}"

        # Verify b[0][0] <= a[0][0] (best bid <= best ask)
        if "b" in order_book_data and "a" in order_book_data:
            if len(order_book_data["b"]) > 0 and len(order_book_data["a"]) > 0:
                best_bid = float(order_book_data["b"][0][0])
                best_ask = float(order_book_data["a"][0][0])
                assert best_bid <= best_ask, f"best_bid ({best_bid}) should be <= best_ask ({best_ask})"

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
        data = live_bybit_spot_feed.get_exchange_info(
            category="spot", symbol="BTCUSDT")
        assert data is not None

        # Bybit returns dict with retCode, retMsg, result
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "retCode" in data
        assert data["retCode"] == 0, f"API returned error: {data.get('retMsg')}"

        result = data.get("result", {})
        symbols = result.get("list", [])
        assert isinstance(symbols, list)
        print("exchange_info:", data)


class TestBybitIntegration:
    """Integration tests for Bybit."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
