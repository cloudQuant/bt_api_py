"""
Mercado Bitcoin Exchange Live Request Data Tests

Tests for Mercado Bitcoin spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue
import time
import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_mercado_bitcoin.spot import MercadoBitcoinRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Mercado Bitcoin
import bt_api_py.feeds.register_mercado_bitcoin  # noqa: F401


def init_req_feed():
    """Initialize Mercado Bitcoin request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_mercado_bitcoin_spot_feed = MercadoBitcoinRequestDataSpot(data_queue, **kwargs)
    return live_mercado_bitcoin_spot_feed


def init_async_feed(data_queue):
    """Initialize Mercado Bitcoin async feed for testing."""
    kwargs = {}
    live_mercado_bitcoin_spot_feed = MercadoBitcoinRequestDataSpot(data_queue, **kwargs)
    return live_mercado_bitcoin_spot_feed


def test_mercado_bitcoin_req_server_time():
    """Test Mercado Bitcoin server time endpoint."""
    # Mercado Bitcoin doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker responses
    pass


@pytest.mark.integration
def test_mercado_bitcoin_req_tick_data():
    """Test Mercado Bitcoin ticker data retrieval (synchronous)."""
    live_mercado_bitcoin_spot_feed = init_req_feed()
    # Mercado Bitcoin uses BTC-BRL as a common trading pair
    data = live_mercado_bitcoin_spot_feed.get_tick("BTC-BRL")
    assert isinstance(data, RequestData)
    assert data.status is True

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    # Mercado Bitcoin ticker returns dict with "ticker" key
    tick_data = data_list[0]
    assert isinstance(tick_data, dict)

    # Verify ticker fields
    assert "last" in tick_data
    assert "buy" in tick_data or "bid" in tick_data
    assert "sell" in tick_data or "ask" in tick_data

    # Validate price values are reasonable
    last_price = float(tick_data.get("last", 0))
    buy_price = float(tick_data.get("buy", tick_data.get("bid", 0)))
    sell_price = float(tick_data.get("sell", tick_data.get("ask", 0)))

    assert last_price > 0, "Last price should be positive"
    assert buy_price > 0, "Buy price should be positive"
    assert sell_price > 0, "Sell price should be positive"


def test_mercado_bitcoin_async_tick_data():
    """Test Mercado Bitcoin ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_mercado_bitcoin_spot_feed = init_async_feed(data_queue)

    # Note: Mercado Bitcoin's current implementation uses sync requests
    # This test would require async implementation
    pass


@pytest.mark.integration
def test_mercado_bitcoin_req_kline_data():
    """Test Mercado Bitcoin kline/candlestick data retrieval (synchronous)."""
    live_mercado_bitcoin_spot_feed = init_req_feed()
    data = live_mercado_bitcoin_spot_feed.get_kline("BTC-BRL", "1h", count=10)
    assert isinstance(data, RequestData)
    assert data.status is True

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    # Mercado Bitcoin kline data structure
    klines = data_list[0]
    assert isinstance(klines, list)

    if len(klines) > 0:
        # Each kline: [timestamp, open, high, low, close, volume]
        first_kline = klines[0]
        assert isinstance(first_kline, list)
        assert len(first_kline) >= 6, "Kline should have at least 6 elements"

        # Validate data types and values
        timestamp = int(first_kline[0])
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


def test_mercado_bitcoin_async_kline_data():
    """Test Mercado Bitcoin kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_mercado_bitcoin_spot_feed = init_async_feed(data_queue)

    # Note: Mercado Bitcoin's current implementation uses sync requests
    # This test would require async implementation
    pass


def order_book_value_equals(order_book):
    """Validate Mercado Bitcoin orderbook data structure and values."""
    assert isinstance(order_book, dict), "Orderbook should be a dict"

    # Mercado Bitcoin orderbook format: {"bids": [[price, volume], ...], "asks": [[price, volume], ...]}
    assert "bids" in order_book, "Orderbook should have bids"
    assert "asks" in order_book, "Orderbook should have asks"

    if len(order_book["bids"]) > 0:
        first_bid = order_book["bids"][0]
        assert isinstance(first_bid, list), "Bid should be a list [price, volume]"
        assert len(first_bid) >= 2, "Bid should have price and volume"
        bid_price = float(first_bid[0])
        bid_volume = float(first_bid[1])
        assert bid_price > 0, "Bid price should be positive"
        assert bid_volume >= 0, "Bid volume should be non-negative"

    if len(order_book["asks"]) > 0:
        first_ask = order_book["asks"][0]
        assert isinstance(first_ask, list), "Ask should be a list [price, volume]"
        assert len(first_ask) >= 2, "Ask should have price and volume"
        ask_price = float(first_ask[0])
        ask_volume = float(first_ask[1])
        assert ask_price > 0, "Ask price should be positive"
        assert ask_volume >= 0, "Ask volume should be non-negative"


@pytest.mark.integration
def test_mercado_bitcoin_req_orderbook_data():
    """Test Mercado Bitcoin orderbook data retrieval."""
    live_mercado_bitcoin_spot_feed = init_req_feed()
    data = live_mercado_bitcoin_spot_feed.get_depth("BTC-BRL", 20)
    assert isinstance(data, RequestData)
    assert data.status is True

    data_list = data.get_data()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    order_book = data_list[0]
    order_book_value_equals(order_book)


@pytest.mark.integration
def test_mercado_bitcoin_exchange_data():
    """Test Mercado Bitcoin exchange data configuration."""
    from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeDataSpot

    exchange_data = MercadoBitcoinExchangeDataSpot()
    assert exchange_data.exchange_name == "MERCADO_BITCOIN___SPOT"
    assert exchange_data.asset_type == "spot"
    assert exchange_data.rest_url
    assert exchange_data.wss_url


def test_mercado_bitcoin_registration():
    """Test that Mercado Bitcoin is properly registered."""
    assert ExchangeRegistry.has_exchange("MERCADO_BITCOIN___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("MERCADO_BITCOIN___SPOT")
    assert feed_class is not None
    assert feed_class == MercadoBitcoinRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeDataSpot
    data_class = ExchangeRegistry._exchange_data_classes.get("MERCADO_BITCOIN___SPOT")
    assert data_class is not None
    assert data_class == MercadoBitcoinExchangeDataSpot


if __name__ == "__main__":
    # Run tests manually for development
    test_mercado_bitcoin_req_tick_data()
    test_mercado_bitcoin_req_kline_data()
    test_mercado_bitcoin_req_orderbook_data()
    test_mercado_bitcoin_exchange_data()
    test_mercado_bitcoin_registration()
    print("All Mercado Bitcoin tests passed!")
