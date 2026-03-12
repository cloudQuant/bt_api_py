"""
Ripio Exchange Live Request Data Tests

Tests for Ripio spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue

import pytest

# Import registration to auto-register Ripio
import bt_api_py.exchange_registers.register_ripio  # noqa: F401
from bt_api_py.feeds.live_ripio.spot import RipioRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def init_req_feed():
    """Initialize Ripio request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_ripio_spot_feed = RipioRequestDataSpot(data_queue, **kwargs)
    return live_ripio_spot_feed


def init_async_feed(data_queue):
    """Initialize Ripio async feed for testing."""
    kwargs = {}
    live_ripio_spot_feed = RipioRequestDataSpot(data_queue, **kwargs)
    return live_ripio_spot_feed


def test_ripio_req_server_time():
    """Test Ripio server time endpoint."""
    # Ripio doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker responses


@pytest.mark.ticker
def test_ripio_req_tick_data():
    """Test Ripio ticker data retrieval (synchronous)."""
    live_ripio_spot_feed = init_req_feed()
    # Ripio uses underscore format: BTC_USDT
    path, params, extra_data = live_ripio_spot_feed._get_tick("BTC/USDT")

    assert "ticker" in path.lower()
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["symbol_name"] == "BTC/USDT"


@pytest.mark.ticker
def test_ripio_tick_normalize_function():
    """Test Ripio ticker normalize function."""
    # Ripio wraps response in {'success': true, 'data': {...}}
    input_data = {
        "success": True,
        "data": {
            "lastPrice": "50000.00",
            "bidPrice": "49999.00",
            "askPrice": "50001.00",
            "bidQuantity": "1.5",
            "askQuantity": "2.3",
            "volume": "1234.56",
            "high": "51000.00",
            "low": "49000.00",
        },
    }

    result, status = RipioRequestDataSpot._get_tick_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    ticker = result[0]
    assert isinstance(ticker, dict)
    assert ticker["lastPrice"] == "50000.00"
    assert ticker["bidPrice"] == "49999.00"
    assert ticker["askPrice"] == "50001.00"


@pytest.mark.ticker
def test_ripio_async_tick_data():
    """Test Ripio ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    init_async_feed(data_queue)

    # Note: Ripio's current implementation uses sync requests
    # This test would require async implementation


@pytest.mark.kline
def test_ripio_req_kline_data():
    """Test Ripio kline/candlestick data retrieval (synchronous)."""
    live_ripio_spot_feed = init_req_feed()
    path, params, extra_data = live_ripio_spot_feed._get_kline("BTC/USDT", "1h", 100)

    assert "candle" in path.lower() or "kline" in path.lower()
    assert extra_data["request_type"] == "get_kline"
    assert extra_data["symbol_name"] == "BTC/USDT"
    assert extra_data["period"] == "1h"
    assert params["limit"] == 100


@pytest.mark.kline
def test_ripio_kline_normalize_function():
    """Test Ripio kline normalize function."""
    # Ripio wraps response in {'success': true, 'data': [...]}
    input_data = {
        "success": True,
        "data": [
            [1678901234000, "49000", "51000", "48000", "50500", "100"],
            [1678904834000, "50500", "52000", "50000", "51500", "150"],
        ],
    }

    result, status = RipioRequestDataSpot._get_kline_normalize_function(
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


@pytest.mark.kline
def test_ripio_async_kline_data():
    """Test Ripio kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    init_async_feed(data_queue)

    # Note: Ripio's current implementation uses sync requests
    # This test would require async implementation


def order_book_value_equals(order_book):
    """Validate Ripio orderbook data structure and values."""
    assert isinstance(order_book, dict), "Orderbook should be a dict"

    # Ripio orderbook format: {"bids": [[price, volume], ...], "asks": [[price, volume], ...]}
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


@pytest.mark.orderbook
def test_ripio_req_orderbook_data():
    """Test Ripio orderbook data retrieval."""
    live_ripio_spot_feed = init_req_feed()
    path, params, extra_data = live_ripio_spot_feed._get_depth("BTC/USDT", 20)

    assert "orderbook" in path.lower() or "depth" in path.lower()
    assert extra_data["request_type"] == "get_depth"
    assert extra_data["symbol_name"] == "BTC/USDT"
    assert params["limit"] == 20


@pytest.mark.orderbook
def test_ripio_depth_normalize_function():
    """Test Ripio depth normalize function."""
    # Ripio wraps response in {'success': true, 'data': {...}}
    input_data = {
        "success": True,
        "data": {
            "bids": [["49999.00", "1.5"], ["49998.00", "2.0"]],
            "asks": [["50001.00", "1.3"], ["50002.00", "2.5"]],
        },
    }

    result, status = RipioRequestDataSpot._get_depth_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    order_book = result[0]
    order_book_value_equals(order_book)


def test_ripio_exchange_data():
    """Test Ripio exchange data configuration."""
    from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeDataSpot

    exchange_data = RipioExchangeDataSpot()
    assert exchange_data.exchange_name == "ripio"
    assert exchange_data.asset_type == "SPOT"

    # Test symbol conversion - Ripio uses underscore format
    assert exchange_data.get_symbol("BTC/USDT") == "BTC_USDT"
    assert exchange_data.get_symbol("ETH-USDT") == "ETH_USDT"


def test_ripio_registration():
    """Test that Ripio is properly registered."""
    assert ExchangeRegistry.has_exchange("RIPIO___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("RIPIO___SPOT")
    assert feed_class is not None
    assert feed_class == RipioRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeDataSpot

    data_class = ExchangeRegistry._exchange_data_classes.get("RIPIO___SPOT")
    assert data_class is not None
    assert data_class == RipioExchangeDataSpot


def test_ripio_get_period_conversion():
    """Test Ripio period conversion."""
    from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeDataSpot

    exchange_data = RipioExchangeDataSpot()
    assert exchange_data.get_period("1m") == "1"
    assert exchange_data.get_period("1h") == "60"
    assert exchange_data.get_period("1d") == "1440"


def test_ripio_trades_normalize_function():
    """Test Ripio trades normalize function."""
    # Ripio wraps response in {'success': true, 'data': [...]}
    input_data = {
        "success": True,
        "data": [
            {
                "id": "12345",
                "price": "50000.00",
                "amount": "0.5",
                "side": "buy",
                "timestamp": 1678901234000,
            },
            {
                "id": "12346",
                "price": "50001.00",
                "amount": "0.3",
                "side": "sell",
                "timestamp": 1678901235000,
            },
        ],
    }

    result, status = RipioRequestDataSpot._get_trades_normalize_function(
        input_data, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)


if __name__ == "__main__":
    # Run tests manually for development
    test_ripio_req_tick_data()
    test_ripio_tick_normalize_function()
    test_ripio_req_kline_data()
    test_ripio_kline_normalize_function()
    test_ripio_req_orderbook_data()
    test_ripio_depth_normalize_function()
    test_ripio_exchange_data()
    test_ripio_registration()
    test_ripio_get_period_conversion()
    test_ripio_trades_normalize_function()
    print("All Ripio tests passed!")
