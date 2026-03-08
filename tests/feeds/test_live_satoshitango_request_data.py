import pytest

"""
SatoshiTango Exchange Live Request Data Tests

Tests for SatoshiTango spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue

# Import registration to auto-register SatoshiTango
import bt_api_py.exchange_registers.register_satoshitango  # noqa: F401
from bt_api_py.feeds.live_satoshitango.spot import SatoshiTangoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def init_req_feed():
    """Initialize SatoshiTango request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_satoshitango_spot_feed = SatoshiTangoRequestDataSpot(data_queue, **kwargs)
    return live_satoshitango_spot_feed


def init_async_feed(data_queue):
    """Initialize SatoshiTango async feed for testing."""
    kwargs = {}
    live_satoshitango_spot_feed = SatoshiTangoRequestDataSpot(data_queue, **kwargs)
    return live_satoshitango_spot_feed


def test_satoshitango_req_server_time():
    """Test SatoshiTango server time endpoint."""
    # SatoshiTango doesn't have a dedicated server time endpoint
    # Timestamps are included in ticker responses
    pass


@pytest.mark.ticker
def test_satoshitango_req_tick_data():
    """Test SatoshiTango ticker data retrieval (synchronous)."""
    live_satoshitango_spot_feed = init_req_feed()
    # SatoshiTango uses symbol format like "btcars"
    path, params, extra_data = live_satoshitango_spot_feed._get_tick("btcars")

    assert "ticker" in path.lower()
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["symbol_name"] == "btcars"
    assert params["symbol"] == "btcars"


@pytest.mark.ticker
def test_satoshitango_tick_normalize_function():
    """Test SatoshiTango ticker normalize function."""
    # SatoshiTango ticker response format
    input_data = {
        "symbol": "btcars",
        "last": "9500000.00",
        "bid": "9400000.00",
        "ask": "9600000.00",
        "volume": "10.5",
        "high": "9700000.00",
        "low": "9300000.00",
        "timestamp": 1678901234000,
    }

    result, status = SatoshiTangoRequestDataSpot._get_tick_normalize_function(
        input_data, {"symbol_name": "btcars", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    ticker = result[0]
    assert isinstance(ticker, dict)
    assert ticker["last"] == "9500000.00"
    assert ticker["bid"] == "9400000.00"
    assert ticker["ask"] == "9600000.00"


@pytest.mark.ticker
def test_satoshitango_async_tick_data():
    """Test SatoshiTango ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_satoshitango_spot_feed = init_async_feed(data_queue)

    # Note: SatoshiTango's current implementation uses sync requests
    # This test would require async implementation
    pass


@pytest.mark.kline
def test_satoshitango_req_kline_data():
    """Test SatoshiTango kline/candlestick data retrieval (synchronous)."""
    live_satoshitango_spot_feed = init_req_feed()
    path, params, extra_data = live_satoshitango_spot_feed._get_kline("btcars", "1h", 100)

    assert "kline" in path.lower() or "candle" in path.lower()
    assert extra_data["request_type"] == "get_kline"
    assert extra_data["symbol_name"] == "btcars"
    assert params["symbol"] == "btcars"
    assert params["limit"] == 100


@pytest.mark.kline
def test_satoshitango_kline_normalize_function():
    """Test SatoshiTango kline normalize function."""
    # SatoshiTango kline response format
    input_data = [
        [1678901234000, "9400000", "9700000", "9300000", "9600000", "5.5"],
        [1678904834000, "9600000", "9800000", "9500000", "9750000", "7.2"],
    ]

    result, status = SatoshiTangoRequestDataSpot._get_kline_normalize_function(
        input_data, {"symbol_name": "btcars", "asset_type": "SPOT"}
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
def test_satoshitango_async_kline_data():
    """Test SatoshiTango kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    live_satoshitango_spot_feed = init_async_feed(data_queue)

    # Note: SatoshiTango's current implementation uses sync requests
    # This test would require async implementation
    pass


def order_book_value_equals(order_book):
    """Validate SatoshiTango orderbook data structure and values."""
    assert isinstance(order_book, dict), "Orderbook should be a dict"

    # SatoshiTango orderbook format: {"bids": [[price, volume], ...], "asks": [[price, volume], ...]}
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
def test_satoshitango_req_orderbook_data():
    """Test SatoshiTango orderbook data retrieval."""
    live_satoshitango_spot_feed = init_req_feed()
    path, params, extra_data = live_satoshitango_spot_feed._get_depth("btcars", 20)

    assert "orderbook" in path.lower() or "depth" in path.lower()
    assert extra_data["request_type"] == "get_depth"
    assert extra_data["symbol_name"] == "btcars"
    assert params["symbol"] == "btcars"
    assert params["depth"] == 20


@pytest.mark.orderbook
def test_satoshitango_depth_normalize_function():
    """Test SatoshiTango depth normalize function."""
    # SatoshiTango orderbook response format
    input_data = {
        "bids": [["9400000.00", "1.5"], ["9300000.00", "2.0"]],
        "asks": [["9600000.00", "1.3"], ["9700000.00", "2.5"]],
    }

    result, status = SatoshiTangoRequestDataSpot._get_depth_normalize_function(
        input_data, {"symbol_name": "btcars", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    order_book = result[0]
    order_book_value_equals(order_book)


def test_satoshitango_exchange_data():
    """Test SatoshiTango exchange data configuration."""
    from bt_api_py.containers.exchanges.satoshitango_exchange_data import (
        SatoshiTangoExchangeDataSpot,
    )

    exchange_data = SatoshiTangoExchangeDataSpot()
    assert exchange_data.exchange_name == "satoshitango"
    assert exchange_data.rest_url == "https://api.satoshitango.com"


def test_satoshitango_registration():
    """Test that SatoshiTango is properly registered."""
    assert ExchangeRegistry.has_exchange("SATOSHITANGO___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("SATOSHITANGO___SPOT")
    assert feed_class is not None
    assert feed_class == SatoshiTangoRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.satoshitango_exchange_data import (
        SatoshiTangoExchangeDataSpot,
    )

    data_class = ExchangeRegistry._exchange_data_classes.get("SATOSHITANGO___SPOT")
    assert data_class is not None
    assert data_class == SatoshiTangoExchangeDataSpot


def test_satoshitango_legal_currencies():
    """Test SatoshiTango legal currencies."""
    from bt_api_py.containers.exchanges.satoshitango_exchange_data import SatoshiTangoExchangeData

    exchange_data = SatoshiTangoExchangeData()
    # Should support Argentine Peso and USD
    assert "ARS" in exchange_data.legal_currency
    assert "USD" in exchange_data.legal_currency


if __name__ == "__main__":
    # Run tests manually for development
    test_satoshitango_req_tick_data()
    test_satoshitango_tick_normalize_function()
    test_satoshitango_req_kline_data()
    test_satoshitango_kline_normalize_function()
    test_satoshitango_req_orderbook_data()
    test_satoshitango_depth_normalize_function()
    test_satoshitango_exchange_data()
    test_satoshitango_registration()
    test_satoshitango_legal_currencies()
    print("All SatoshiTango tests passed!")
