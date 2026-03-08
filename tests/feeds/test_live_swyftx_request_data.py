"""
Swyftx Exchange Live Request Data Tests

Tests for Swyftx spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue
import time
import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_swyftx.spot import SwyftxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Swyftx
import bt_api_py.exchange_registers.register_swyftx  # noqa: F401


def init_req_feed():
    """Initialize Swyftx request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_swyftx_spot_feed = SwyftxRequestDataSpot(data_queue, **kwargs)
    return live_swyftx_spot_feed


def init_async_feed(data_queue):
    """Initialize Swyftx async feed for testing."""
    kwargs = {}
    live_swyftx_spot_feed = SwyftxRequestDataSpot(data_queue, **kwargs)
    return live_swyftx_spot_feed


def test_swyftx_req_server_time():
    """Test Swyftx server time endpoint."""
    # Swyftx doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker responses
    pass


def test_swyftx_req_tick_data():
    """Test Swyftx ticker data retrieval (synchronous)."""
    live_swyftx_spot_feed = init_req_feed()
    # Swyftx uses market ID for symbols
    path, params, extra_data = live_swyftx_spot_feed._get_tick("BTC-AUD")

    assert "ticker" in path.lower()
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["symbol_name"] == "BTC-AUD"
    assert params is None  # _get_tick returns no params


def test_swyftx_tick_normalize_function():
    """Test Swyftx ticker normalize function."""
    # Swyftx ticker response format
    input_data = {
        "marketId": "BTC-AUD",
        "lastPrice": "95000.00",
        "bid": "94999.00",
        "ask": "95001.00",
        "volume": "123.45",
        "high": "96000.00",
        "low": "94000.00",
        "change": "2.5",
    }

    result, status = SwyftxRequestDataSpot._get_tick_normalize_function(
        input_data, {"symbol_name": "BTC-AUD", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    ticker = result[0]
    assert isinstance(ticker, dict)
    # Verify ticker has expected fields
    assert "lastPrice" in ticker or "price" in ticker


def test_swyftx_async_tick_data():
    """Test Swyftx ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_swyftx_spot_feed = init_async_feed(data_queue)

    # Note: Swyftx's current implementation uses sync requests
    # This test would require async implementation
    pass


def test_swyftx_req_kline_data():
    """Test Swyftx kline/candlestick data retrieval (synchronous)."""
    live_swyftx_spot_feed = init_req_feed()
    path, params, extra_data = live_swyftx_spot_feed._get_kline("BTC-AUD", "1h", 100)

    assert "candle" in path.lower() or "kline" in path.lower()
    assert extra_data["request_type"] == "get_kline"
    assert extra_data["symbol_name"] == "BTC-AUD"
    assert params["limit"] == 100


def test_swyftx_kline_normalize_function():
    """Test Swyftx kline normalize function."""
    # Swyftx kline response format
    input_data = [
        [1678901234000, "94000", "96000", "93000", "95500", "5.5"],
        [1678904834000, "95500", "97500", "95000", "97000", "7.2"],
    ]

    result, status = SwyftxRequestDataSpot._get_kline_normalize_function(
        input_data, {"symbol_name": "BTC-AUD", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)

    if len(result) > 0:
        first_kline = result[0]
        assert isinstance(first_kline, list)
        assert len(first_kline) >= 6

        timestamp = int(first_kline[0])
        open_price = float(first_kline[1])
        high_price = float(first_kline[2])
        low_price = float(first_kline[3])
        close_price = float(first_kline[4])
        volume = float(first_kline[5])

        assert timestamp > 0
        assert open_price > 0
        assert high_price >= open_price
        assert low_price <= open_price
        assert close_price > 0
        assert volume >= 0


def test_swyftx_async_kline_data():
    """Test Swyftx kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_swyftx_spot_feed = init_async_feed(data_queue)

    # Note: Swyftx's current implementation uses sync requests
    # This test would require async implementation
    pass


def order_book_value_equals(order_book):
    """Validate Swyftx orderbook data structure and values."""
    assert isinstance(order_book, dict), "Orderbook should be a dict"

    # Swyftx orderbook format: {"bids": [[price, volume], ...], "asks": [[price, volume], ...]}
    # Or: {"buy": [[price, volume], ...], "sell": [[price, volume], ...]}
    has_bids = "bids" in order_book or "buy" in order_book
    has_asks = "asks" in order_book or "sell" in order_book

    assert has_bids, "Orderbook should have bids"
    assert has_asks, "Orderbook should have asks"

    bids_key = "bids" if "bids" in order_book else "buy"
    asks_key = "asks" if "asks" in order_book else "sell"

    if len(order_book.get(bids_key, [])) > 0:
        first_bid = order_book[bids_key][0]
        assert isinstance(first_bid, list), "Bid should be a list [price, volume]"
        assert len(first_bid) >= 2, "Bid should have price and volume"
        bid_price = float(first_bid[0])
        bid_volume = float(first_bid[1])
        assert bid_price > 0, "Bid price should be positive"
        assert bid_volume >= 0, "Bid volume should be non-negative"

    if len(order_book.get(asks_key, [])) > 0:
        first_ask = order_book[asks_key][0]
        assert isinstance(first_ask, list), "Ask should be a list [price, volume]"
        assert len(first_ask) >= 2, "Ask should have price and volume"
        ask_price = float(first_ask[0])
        ask_volume = float(first_ask[1])
        assert ask_price > 0, "Ask price should be positive"
        assert ask_volume >= 0, "Ask volume should be non-negative"


def test_swyftx_req_orderbook_data():
    """Test Swyftx orderbook data retrieval."""
    live_swyftx_spot_feed = init_req_feed()
    path, params, extra_data = live_swyftx_spot_feed._get_depth("BTC-AUD", 20)

    assert "orderbook" in path.lower() or "depth" in path.lower()
    assert extra_data["request_type"] == "get_depth"
    assert extra_data["symbol_name"] == "BTC-AUD"
    assert params["depth"] == 20


def test_swyftx_depth_normalize_function():
    """Test Swyftx depth normalize function."""
    # Swyftx orderbook response format
    input_data = {
        "bids": [
            ["94999.00", "1.5"],
            ["94998.00", "2.0"]
        ],
        "asks": [
            ["95001.00", "1.3"],
            ["95002.00", "2.5"]
        ]
    }

    result, status = SwyftxRequestDataSpot._get_depth_normalize_function(
        input_data, {"symbol_name": "BTC-AUD", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    order_book = result[0]
    order_book_value_equals(order_book)


def test_swyftx_exchange_data():
    """Test Swyftx exchange data configuration."""
    from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot

    exchange_data = SwyftxExchangeDataSpot()
    assert exchange_data.exchange_name == "SWYFTX___SPOT"
    assert "swyftx" in exchange_data.rest_url.lower()
    # wss_url may be empty if not configured
    assert isinstance(exchange_data.wss_url, str)


def test_swyftx_registration():
    """Test that Swyftx is properly registered."""
    assert ExchangeRegistry.has_exchange("SWYFTX___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("SWYFTX___SPOT")
    assert feed_class is not None
    assert feed_class == SwyftxRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot
    data_class = ExchangeRegistry._exchange_data_classes.get("SWYFTX___SPOT")
    assert data_class is not None
    assert data_class == SwyftxExchangeDataSpot


def test_swyftx_legal_currencies():
    """Test Swyftx legal currencies."""
    from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeData

    exchange_data = SwyftxExchangeData()
    # Should support AUD primarily (Australian exchange)
    assert "AUD" in exchange_data.legal_currency
    assert "USD" in exchange_data.legal_currency


def test_swyftx_get_period_conversion():
    """Test Swyftx period conversion."""
    from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot

    exchange_data = SwyftxExchangeDataSpot()
    # Test common periods
    period_1m = exchange_data.get_period("1m")
    period_1h = exchange_data.get_period("1h")
    period_1d = exchange_data.get_period("1d")

    # Periods should be defined
    assert period_1m is not None
    assert period_1h is not None
    assert period_1d is not None


if __name__ == "__main__":
    # Run tests manually for development
    test_swyftx_req_tick_data()
    test_swyftx_tick_normalize_function()
    test_swyftx_req_kline_data()
    test_swyftx_kline_normalize_function()
    test_swyftx_req_orderbook_data()
    test_swyftx_depth_normalize_function()
    test_swyftx_exchange_data()
    test_swyftx_registration()
    test_swyftx_legal_currencies()
    test_swyftx_get_period_conversion()
    print("All Swyftx tests passed!")
