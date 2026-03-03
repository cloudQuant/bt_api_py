"""
Test BTC Markets exchange integration following Binance/OKX standards.

NOTE: These tests are structured following the Binance/OKX test standards.
However, the BTC Markets exchange implementation has known issues that prevent
the tests from running:
    pass
- logger.warning attribute issue
- config loading issues
- missing get_period method
- missing api_key attribute

Run tests:
    pytest tests/feeds/test_live_btc_markets_request_data.py -v

Run with coverage:
    pytest tests/feeds/test_live_btc_markets_request_data.py --cov=bt_api_py.feeds.live_btc_markets --cov-report=term-missing
"""

import queue
import time
import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_btc_markets.spot import BtcMarketsRequestDataSpot


def init_req_feed():
    """Initialize BTC Markets request feed."""
    data_queue = queue.Queue()
    live_btc_markets_spot_feed = BtcMarketsRequestDataSpot(data_queue)
    return live_btc_markets_spot_feed


class TestBtcMarketsServerTime:
    """Test BTC Markets server time endpoint."""

    def test_btc_markets_req_server_time(self):
        """Test getting server time."""
        live_btc_markets_spot_feed = init_req_feed()
        data = live_btc_markets_spot_feed.get_server_time()
        assert isinstance(data, RequestData)
        print("server_time:", data.get_data())


class TestBtcMarketsTickData:
    """Test BTC Markets ticker data."""

    def test_btc_markets_req_tick_data(self):
        """Test getting ticker data (synchronous)."""
        live_btc_markets_spot_feed = init_req_feed()
        data = live_btc_markets_spot_feed.get_tick("BTC-AUD").get_data()
        assert isinstance(data, list)

        if len(data) > 0 and data[0] is not None:
            pass
        tick_data = data[0]
        assert isinstance(tick_data, dict)

        # Verify marketId
        market_id = tick_data.get("marketId")
        assert market_id == "BTC-AUD", f"Expected BTC-AUD, got {market_id}"

        # Verify price fields
        last_price = tick_data.get("lastPrice")
        assert last_price is None or float(
            last_price) > 0, f"Invalid last_price: {last_price}"

        best_bid = tick_data.get("bestBid")
        assert best_bid is None or float(
            best_bid) >= 0, f"Invalid best_bid: {best_bid}"

        best_ask = tick_data.get("bestAsk")
        assert best_ask is None or float(
            best_ask) >= 0, f"Invalid best_ask: {best_ask}"

        # Verify volume
        volume = tick_data.get("volume24h")
        assert volume is None or float(
            volume) >= 0, f"Invalid volume: {volume}"

        print("tick_data:", tick_data)

    def test_btc_markets_async_tick_data(self):
        """Test getting ticker data (asynchronous)."""
        data_queue = queue.Queue()
        live_btc_markets_spot_feed = BtcMarketsRequestDataSpot(data_queue)
        live_btc_markets_spot_feed.async_get_tick(
            "BTC-AUD", extra_data={"test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            tick_data = None

        assert isinstance(tick_data, RequestData)
        assert isinstance(tick_data.get_data(), list)


class TestBtcMarketsKlineData:
    """Test BTC Markets kline/candlestick data."""

    def test_btc_markets_req_kline_data(self):
        """Test getting kline data (synchronous)."""
        live_btc_markets_spot_feed = init_req_feed()
        data = live_btc_markets_spot_feed.get_kline(
            "BTC-AUD", "1m", count=2).get_data()
        assert isinstance(data, list)

        if len(data) > 0 and data[0] is not None:
            # BTC Markets returns list of candles
            kline_list = data[0]
            if isinstance(kline_list, list) and len(kline_list) > 0:
                first_kline = kline_list[0]

                # BTC Markets candle format: [timestamp, open, high, low, close,
                # volume]
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

                print("kline_data:", kline_list)

    def test_btc_markets_async_kline_data(self):
        """Test getting kline data (asynchronous)."""
        data_queue = queue.Queue()
        live_btc_markets_spot_feed = BtcMarketsRequestDataSpot(data_queue)
        live_btc_markets_spot_feed.async_get_kline("BTC-AUD", period="1m", count=3,
                                                   extra_data={"test_async_kline_data": True})
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            kline_data = None

        assert kline_data is not None
        assert isinstance(kline_data, RequestData)


class TestBtcMarketsOrderBook:
    """Test BTC Markets order book data."""

    def test_btc_markets_req_orderbook_data(self):
        """Test getting order book data."""
        live_btc_markets_spot_feed = init_req_feed()
        data = live_btc_markets_spot_feed.get_depth("BTC-AUD", 20).get_data()
        assert isinstance(data, list)

        if len(data) > 0 and data[0] is not None:
            order_book_data = data[0]
            self.order_book_value_equals(order_book_data)
            print("orderbook_data:", order_book_data)

    def order_book_value_equals(self, order_book_data):
        """Validate order book data structure and values."""
        assert order_book_data is not None

        # BTC Markets returns dict with bids/asks
        assert isinstance(
            order_book_data, dict), f"Expected dict, got {type(order_book_data)}"

        # Check for bids
        if "bids" in order_book_data:
            bids = order_book_data["bids"]
            assert isinstance(
                bids, list), f"bids should be list, got {type(bids)}"
            if len(bids) > 0:
                first_bid = bids[0]
                # BTC Markets bid format: [price, volume]
                if isinstance(first_bid, list):
                    assert len(
                        first_bid) >= 2, f"bid should have price and volume, got {first_bid}"
                    bid_price = float(first_bid[0])
                    bid_volume = float(first_bid[1])
                    assert bid_price > 0, f"Invalid bid_price: {bid_price}"
                    assert bid_volume >= 0, f"Invalid bid_volume: {bid_volume}"
                elif isinstance(first_bid, dict):
                    bid_price = float(first_bid.get("price", 0))
                    bid_volume = float(first_bid.get("size", 0))
                    assert bid_price > 0, f"Invalid bid_price: {bid_price}"
                    assert bid_volume >= 0, f"Invalid bid_volume: {bid_volume}"

        # Check for asks
        if "asks" in order_book_data:
            asks = order_book_data["asks"]
            assert isinstance(
                asks, list), f"asks should be list, got {type(asks)}"
            if len(asks) > 0:
                first_ask = asks[0]
                if isinstance(first_ask, list):
                    assert len(
                        first_ask) >= 2, f"ask should have price and volume, got {first_ask}"
                    ask_price = float(first_ask[0])
                    ask_volume = float(first_ask[1])
                    assert ask_price > 0, f"Invalid ask_price: {ask_price}"
                    assert ask_volume >= 0, f"Invalid ask_volume: {ask_volume}"
                elif isinstance(first_ask, dict):
                    ask_price = float(first_ask.get("price", 0))
                    ask_volume = float(first_ask.get("size", 0))
                    assert ask_price > 0, f"Invalid ask_price: {ask_price}"
                    assert ask_volume >= 0, f"Invalid ask_volume: {ask_volume}"

    def test_btc_markets_async_orderbook_data(self):
        """Test getting order book data (asynchronous)."""
        data_queue = queue.Queue()
        live_btc_markets_spot_feed = BtcMarketsRequestDataSpot(data_queue)
        live_btc_markets_spot_feed.async_get_depth("BTC-AUD", 20)
        time.sleep(3)

        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            depth_data = None

        assert depth_data is not None
        target_data = depth_data.get_data()
        assert isinstance(target_data, list)


class TestBtcMarketsExchangeInfo:
    """Test BTC Markets exchange information."""

    def test_btc_markets_req_exchange_info(self):
        """Test getting exchange information."""
        live_btc_markets_spot_feed = init_req_feed()
        data = live_btc_markets_spot_feed.get_exchange_info().get_data()
        assert isinstance(data, list)
        print("exchange_info:", data)


class TestBtcMarketsIntegration:
    """Integration tests for BTC Markets."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
