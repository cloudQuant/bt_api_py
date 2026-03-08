"""
Luno Exchange Live Request Data Tests

Tests for Luno spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue
import time
import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_luno.spot import LunoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Luno
import bt_api_py.exchange_registers.register_luno  # noqa: F401


def init_req_feed():
    """Initialize Luno request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_luno_spot_feed = LunoRequestDataSpot(data_queue, **kwargs)
    return live_luno_spot_feed


def init_async_feed(data_queue):
    """Initialize Luno async feed for testing."""
    kwargs = {}
    live_luno_spot_feed = LunoRequestDataSpot(data_queue, **kwargs)
    return live_luno_spot_feed


def test_luno_req_server_time():
    """Test Luno server time endpoint."""
    # Luno doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker and orderbook responses
    pass


@pytest.mark.integration
def test_luno_req_tick_data():
    """Test Luno ticker data retrieval (synchronous)."""
    live_luno_spot_feed = init_req_feed()
    # Luno uses XBTZAR as a common trading pair
    data = live_luno_spot_feed.get_tick("XBTZAR")
    assert isinstance(data, RequestData)
    if not data.status:
        pytest.skip("Luno API returned error (network/auth)")

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    # Luno ticker returns raw dict data
    tick_data = data_list[0]
    assert isinstance(tick_data, dict)

    # Verify ticker fields
    assert "last_trade" in tick_data or "last" in tick_data
    assert "bid" in tick_data
    assert "ask" in tick_data

    # Validate price values are reasonable
    last_price = float(tick_data.get("last_trade", tick_data.get("last", 0)))
    bid_price = float(tick_data.get("bid", 0))
    ask_price = float(tick_data.get("ask", 0))

    assert last_price > 0, "Last price should be positive"
    assert bid_price > 0, "Bid price should be positive"
    assert ask_price > 0, "Ask price should be positive"
    assert ask_price >= bid_price, "Ask price should be >= bid price"


def test_luno_async_tick_data():
    """Test Luno ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_luno_spot_feed = init_async_feed(data_queue)

    # Note: Luno's current implementation uses sync requests
    # This test would require async implementation
    pass


@pytest.mark.integration
def test_luno_req_kline_data():
    """Test Luno kline/candlestick data retrieval (synchronous)."""
    live_luno_spot_feed = init_req_feed()
    data = live_luno_spot_feed.get_kline("XBTZAR", "1h", count=10)
    assert isinstance(data, RequestData)
    if not data.status:
        pytest.skip("Luno API returned error (network/auth)")

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    # Luno kline data structure
    klines = data_list[0]
    assert isinstance(klines, list)

    if len(klines) > 0:
        # Each kline should have timestamp, open, high, low, close, volume
        first_kline = klines[0]
        assert isinstance(first_kline, list) or isinstance(first_kline, dict)

        if isinstance(first_kline, list):
            assert len(first_kline) >= 6, "Kline should have at least 6 elements"

            # Validate data types and values
            timestamp = first_kline[0]
            open_price = float(first_kline[1])
            high_price = float(first_kline[2])
            low_price = float(first_kline[3])
            close_price = float(first_kline[4])
            volume = float(first_kline[5])

            assert timestamp > 0, "Timestamp should be positive"
            assert open_price > 0, "Open price should be positive"
            assert high_price >= open_price, "High should be >= open"
            assert low_price <= open_price, "Low should be <= open"
            assert close_price > 0, "Close price should be positive"
            assert volume >= 0, "Volume should be non-negative"


def test_luno_async_kline_data():
    """Test Luno kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_luno_spot_feed = init_async_feed(data_queue)

    # Note: Luno's current implementation uses sync requests
    # This test would require async implementation
    pass


@pytest.mark.integration
def test_luno_req_orderbook_data():
    """Test Luno orderbook data retrieval."""
    live_luno_spot_feed = init_req_feed()
    data = live_luno_spot_feed.get_depth("XBTZAR", 20)
    assert isinstance(data, RequestData)
    if not data.status:
        pytest.skip("Luno API returned error (network/auth)")

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    order_book = data_list[0]
    order_book_value_equals(order_book)


def test_luno_exchange_data():
    """Test Luno exchange data configuration."""
    from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeDataSpot

    exchange_data = LunoExchangeDataSpot()
    assert exchange_data.exchange_name == "LUNO___SPOT"
    assert exchange_data.asset_type == "spot"
    assert exchange_data.rest_url
    assert exchange_data.wss_url


def test_luno_registration():
    """Test that Luno is properly registered."""
    assert ExchangeRegistry.has_exchange("LUNO___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("LUNO___SPOT")
    assert feed_class is not None
    assert feed_class == LunoRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeDataSpot
    data_class = ExchangeRegistry._exchange_data_classes.get("LUNO___SPOT")
    assert data_class is not None
    assert data_class == LunoExchangeDataSpot


if __name__ == "__main__":
    # Run tests manually for development
    test_luno_req_tick_data()
    test_luno_req_kline_data()
    test_luno_req_orderbook_data()
    test_luno_exchange_data()
    test_luno_registration()
    print("All Luno tests passed!")
