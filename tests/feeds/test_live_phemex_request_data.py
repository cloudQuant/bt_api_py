"""
Phemex Exchange Live Request Data Tests

Tests for Phemex spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue
import time
import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_phemex.spot import PhemexRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Phemex
import bt_api_py.feeds.register_phemex  # noqa: F401


def init_req_feed():
    """Initialize Phemex request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_phemex_spot_feed = PhemexRequestDataSpot(data_queue, **kwargs)
    return live_phemex_spot_feed


def init_async_feed(data_queue):
    """Initialize Phemex async feed for testing."""
    kwargs = {}
    live_phemex_spot_feed = PhemexRequestDataSpot(data_queue, **kwargs)
    return live_phemex_spot_feed


def test_phemex_req_server_time():
    """Test Phemex server time endpoint."""
    # Phemex doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker and orderbook responses
    pass


def test_phemex_req_tick_data():
    """Test Phemex ticker data retrieval (synchronous)."""
    live_phemex_spot_feed = init_req_feed()
    # Phemex uses BTC/USDT format for symbols
    path, params, extra_data = live_phemex_spot_feed._get_tick("BTC/USDT")

    assert "ticker" in path.lower() or "md" in path.lower()
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["symbol_name"] == "BTC/USDT"


def test_phemex_tick_normalize_function():
    """Test Phemex ticker normalize function."""
    # Phemex API response format
    input_data = {
        "code": 0,
        "data": {
            "lastEp": 50000000000,  # Scaled price (multiply by 1e8)
            "markEp": 50000000000,
            "indexEp": 50000000000,
        }
    }

    result, status = PhemexRequestDataSpot._get_tick_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    ticker = result[0]
    assert isinstance(ticker, dict)
    assert "lastEp" in ticker


def test_phemex_async_tick_data():
    """Test Phemex ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_phemex_spot_feed = init_async_feed(data_queue)

    # Note: Phemex's current implementation uses sync requests
    # This test would require async implementation
    pass


def test_phemex_req_kline_data():
    """Test Phemex kline/candlestick data retrieval (synchronous)."""
    live_phemex_spot_feed = init_req_feed()
    path, params, extra_data = live_phemex_spot_feed._get_kline("BTC/USDT", "1h", 100)

    assert "kline" in path.lower() or "candle" in path.lower()
    assert extra_data["request_type"] == "get_kline"
    assert extra_data["symbol_name"] == "BTC/USDT"
    assert extra_data["period"] == "1h"
    assert params["limit"] == 100


def test_phemex_kline_normalize_function():
    """Test Phemex kline normalize function."""
    # Phemex API response format for klines
    input_data = {
        "code": 0,
        "data": {
            "rows": [
                [1678901234000, 49000, 51000, 48000, 50500, 100],
                [1678904834000, 50500, 52000, 50000, 51500, 150],
            ]
        }
    }

    result, status = PhemexRequestDataSpot._get_kline_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "period": "1h", "asset_type": "SPOT"}
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


def test_phemex_async_kline_data():
    """Test Phemex kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_phemex_spot_feed = init_async_feed(data_queue)

    # Note: Phemex's current implementation uses sync requests
    # This test would require async implementation
    pass


def order_book_value_equals(order_book):
    """Validate Phemex orderbook data structure and values."""
    assert isinstance(order_book, dict), "Orderbook should be a dict"

    # Phemex orderbook format: {"bids": [[price, size], ...], "asks": [[price, size], ...]}
    assert "bids" in order_book, "Orderbook should have bids"
    assert "asks" in order_book, "Orderbook should have asks"

    if len(order_book["bids"]) > 0:
        first_bid = order_book["bids"][0]
        assert isinstance(first_bid, list), "Bid should be a list [price, size]"
        assert len(first_bid) >= 2, "Bid should have price and size"
        bid_price = float(first_bid[0]) / 1e8  # Phemex uses scaled prices
        bid_volume = float(first_bid[1]) / 1e8  # Phemex uses scaled amounts
        assert bid_price > 0, "Bid price should be positive"
        assert bid_volume >= 0, "Bid volume should be non-negative"

    if len(order_book["asks"]) > 0:
        first_ask = order_book["asks"][0]
        assert isinstance(first_ask, list), "Ask should be a list [price, size]"
        assert len(first_ask) >= 2, "Ask should have price and size"
        ask_price = float(first_ask[0]) / 1e8  # Phemex uses scaled prices
        ask_volume = float(first_ask[1]) / 1e8  # Phemex uses scaled amounts
        assert ask_price > 0, "Ask price should be positive"
        assert ask_volume >= 0, "Ask volume should be non-negative"


def test_phemex_req_orderbook_data():
    """Test Phemex orderbook data retrieval."""
    live_phemex_spot_feed = init_req_feed()
    path, params, extra_data = live_phemex_spot_feed._get_depth("BTC/USDT", 20)

    assert "orderbook" in path.lower() or "depth" in path.lower() or "md" in path.lower()
    assert extra_data["request_type"] == "get_depth"
    assert extra_data["symbol_name"] == "BTC/USDT"
    assert params["level"] == 20


def test_phemex_depth_normalize_function():
    """Test Phemex depth normalize function."""
    # Phemex API response format for orderbook
    input_data = {
        "code": 0,
        "data": {
            "book": {
                "bids": [
                    [4999900000000, 100000000],  # Scaled: price, size
                    [4999800000000, 200000000],
                ],
                "asks": [
                    [5000100000000, 150000000],
                    [5000200000000, 250000000],
                ]
            }
        }
    }

    result, status = PhemexRequestDataSpot._get_depth_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    order_book = result[0]
    order_book_value_equals(order_book)


def test_phemex_exchange_data():
    """Test Phemex exchange data configuration."""
    from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot

    exchange_data = PhemexExchangeDataSpot()
    assert exchange_data.exchange_name in ["phemex", "phemex_spot"]
    assert exchange_data.asset_type == "SPOT"

    # Test symbol conversion
    # Phemex spot symbols are prefixed with 's'
    symbol = exchange_data.get_symbol("BTC/USDT")
    assert "BTC" in symbol.upper()
    assert "USDT" in symbol.upper()


def test_phemex_scale_unscale_price():
    """Test Phemex price scaling/unscaling."""
    from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot

    exchange_data = PhemexExchangeDataSpot()
    price = 50000.0

    scaled = exchange_data.scale_price(price)
    assert scaled == int(price * 1e8)

    unscaled = exchange_data.unscale_price(scaled)
    assert unscaled == price


def test_phemex_registration():
    """Test that Phemex is properly registered."""
    assert ExchangeRegistry.has_exchange("PHEMEX___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("PHEMEX___SPOT")
    assert feed_class is not None
    assert feed_class == PhemexRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot
    data_class = ExchangeRegistry._exchange_data_classes.get("PHEMEX___SPOT")
    assert data_class is not None
    assert data_class == PhemexExchangeDataSpot


def test_phemex_get_period_conversion():
    """Test Phemex period conversion."""
    from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot

    exchange_data = PhemexExchangeDataSpot()
    assert exchange_data.get_period("1m") == "60"
    assert exchange_data.get_period("1h") == "3600"
    assert exchange_data.get_period("1d") == "86400"


if __name__ == "__main__":
    # Run tests manually for development
    test_phemex_req_tick_data()
    test_phemex_tick_normalize_function()
    test_phemex_req_kline_data()
    test_phemex_kline_normalize_function()
    test_phemex_req_orderbook_data()
    test_phemex_depth_normalize_function()
    test_phemex_exchange_data()
    test_phemex_scale_unscale_price()
    test_phemex_registration()
    test_phemex_get_period_conversion()
    print("All Phemex tests passed!")
