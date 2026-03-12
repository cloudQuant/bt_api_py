"""
MEXC Exchange Live Request Data Tests

Tests for MEXC spot trading implementation following Binance/OKX standards:
- Server time
- Ticker data (sync and async)
- Kline data (sync and async)
- Orderbook data
"""

import queue

import pytest

# Import registration to auto-register MEXC
import bt_api_py.exchange_registers.register_mexc  # noqa: F401
from bt_api_py.containers.orderbooks.mexc_orderbook import MexcRequestOrderBookData
from bt_api_py.containers.tickers.mexc_ticker import MexcRequestTickerData
from bt_api_py.feeds.live_mexc.spot import MexcRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def init_req_feed():
    """Initialize MEXC request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {}
    live_mexc_spot_feed = MexcRequestDataSpot(data_queue, **kwargs)
    return live_mexc_spot_feed


def init_async_feed(data_queue):
    """Initialize MEXC async feed for testing."""
    kwargs = {}
    live_mexc_spot_feed = MexcRequestDataSpot(data_queue, **kwargs)
    return live_mexc_spot_feed


def test_mexc_req_server_time():
    """Test MEXC server time endpoint."""
    live_mexc_spot_feed = init_req_feed()
    path, params, extra_data = live_mexc_spot_feed._get_server_time()
    assert "time" in path.lower() or "server" in path.lower()


@pytest.mark.ticker
def test_mexc_req_tick_data():
    """Test MEXC ticker data retrieval (synchronous)."""
    live_mexc_spot_feed = init_req_feed()
    # MEXC uses BTCUSDT format
    path, params, extra_data = live_mexc_spot_feed._get_ticker("BTCUSDT")

    assert "ticker" in path.lower() or "24hr" in path.lower()
    assert params["symbol"] == "BTCUSDT"
    assert extra_data["request_type"] in ["get_24hr_ticker", "get_ticker"]


@pytest.mark.ticker
def test_mexc_async_tick_data():
    """Test MEXC ticker data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    init_async_feed(data_queue)

    # Note: MEXC's current implementation uses sync requests
    # This test would require async implementation


@pytest.mark.kline
def test_mexc_req_kline_data():
    """Test MEXC kline/candlestick data retrieval (synchronous)."""
    live_mexc_spot_feed = init_req_feed()
    path, params, extra_data = live_mexc_spot_feed._get_klines("BTCUSDT", "1h", 100)

    assert "kline" in path.lower() or "candlestick" in path.lower()
    assert params["symbol"] == "BTCUSDT"
    assert params["interval"] == "1h"
    assert params["limit"] == 100
    assert extra_data["request_type"] == "get_klines"


@pytest.mark.kline
def test_mexc_async_kline_data():
    """Test MEXC kline data retrieval (asynchronous)."""
    data_queue = queue.Queue()
    init_async_feed(data_queue)

    # Note: MEXC's current implementation uses sync requests
    # This test would require async implementation


def order_book_value_equals(order_book):
    """Validate MEXC orderbook data structure and values."""
    assert isinstance(order_book, MexcRequestOrderBookData), (
        "Orderbook should be MexcRequestOrderBookData"
    )

    # Initialize to parse data
    order_book.init_data()

    # Check basic properties
    assert order_book.get_symbol_name() is not None, "Symbol name should be set"
    assert order_book.get_asset_type() == "SPOT", "Asset type should be SPOT"

    # Validate bids
    bids = order_book.get_bids()
    assert isinstance(bids, list), "Bids should be a list"
    if len(bids) > 0:
        first_bid = bids[0]
        assert isinstance(first_bid, list), "Bid should be a list [price, volume]"
        assert len(first_bid) >= 2, "Bid should have price and volume"
        bid_price = float(first_bid[0])
        bid_volume = float(first_bid[1])
        assert bid_price > 0, "Bid price should be positive"
        assert bid_volume >= 0, "Bid volume should be non-negative"

    # Validate asks
    asks = order_book.get_asks()
    assert isinstance(asks, list), "Asks should be a list"
    if len(asks) > 0:
        first_ask = asks[0]
        assert isinstance(first_ask, list), "Ask should be a list [price, volume]"
        assert len(first_ask) >= 2, "Ask should have price and volume"
        ask_price = float(first_ask[0])
        ask_volume = float(first_ask[1])
        assert ask_price > 0, "Ask price should be positive"
        assert ask_volume >= 0, "Ask volume should be non-negative"


@pytest.mark.orderbook
def test_mexc_req_orderbook_data():
    """Test MEXC orderbook data retrieval."""
    live_mexc_spot_feed = init_req_feed()
    path, params, extra_data = live_mexc_spot_feed._get_order_book("BTCUSDT", 100)

    assert "book" in path.lower() or "depth" in path.lower()
    assert params["symbol"] == "BTCUSDT"
    assert params["limit"] == 100
    assert extra_data["request_type"] == "get_order_book"


@pytest.mark.ticker
def test_mexc_ticker_normalize_function():
    """Test MEXC ticker normalize function."""
    ticker_response = {
        "symbol": "BTCUSDT",
        "priceChange": "1000.00",
        "priceChangePercent": "2.05",
        "weightedAvgPrice": "49500.00",
        "prevClosePrice": "49000.00",
        "lastPrice": "50000.00",
        "lastQty": "0.001",
        "bidPrice": "49999.00",
        "bidQty": "1.5",
        "askPrice": "50001.00",
        "askQty": "2.3",
        "openPrice": "49500.00",
        "highPrice": "51000.00",
        "lowPrice": "49000.00",
        "volume": "1234.56",
        "quoteVolume": "61728000",
        "openTime": 1688671955000,
        "closeTime": 1688758355000,
        "firstId": 100000,
        "lastId": 110000,
        "count": 10000,
    }

    result, status = MexcRequestDataSpot._get_ticker_normalize_function(
        ticker_response, {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    ticker = result[0]
    assert isinstance(ticker, MexcRequestTickerData)
    assert ticker.symbol_name == "BTCUSDT"
    assert ticker.get_last_price() == 50000.0
    assert ticker.get_bid_price() == 49999.0
    assert ticker.get_ask_price() == 50001.0


@pytest.mark.orderbook
def test_mexc_orderbook_normalize_function():
    """Test MEXC orderbook normalize function."""
    orderbook_response = {
        "lastUpdateId": 123456,
        "bids": [["49999.00", "1.5"], ["49998.00", "2.0"]],
        "asks": [["50001.00", "1.3"], ["50002.00", "2.5"]],
    }

    result, status = MexcRequestDataSpot._get_order_book_normalize_function(
        orderbook_response, {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
    )

    assert status is True
    assert isinstance(result, list)
    assert len(result) > 0

    orderbook = result[0]
    assert isinstance(orderbook, MexcRequestOrderBookData)
    assert orderbook.symbol_name == "BTCUSDT"

    order_book_value_equals(orderbook)


def test_mexc_exchange_data():
    """Test MEXC exchange data configuration."""
    from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot

    exchange_data = MexcExchangeDataSpot()
    assert exchange_data.exchange_name == "MEXC___SPOT"
    assert exchange_data.asset_type == "SPOT"

    # Test symbol conversion
    assert exchange_data.get_symbol("BTCUSDT") == "BTCUSDT"
    assert exchange_data.get_symbol("BTC/USDT") == "BTCUSDT"


def test_mexc_registration():
    """Test that MEXC is properly registered."""
    assert ExchangeRegistry.has_exchange("MEXC___SPOT")

    # Check feed class
    feed_class = ExchangeRegistry._feed_classes.get("MEXC___SPOT")
    assert feed_class is not None
    assert feed_class == MexcRequestDataSpot

    # Check exchange data class
    from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot

    data_class = ExchangeRegistry._exchange_data_classes.get("MEXC___SPOT")
    assert data_class is not None
    assert data_class == MexcExchangeDataSpot


def test_mexc_order_params():
    """Test MEXC order parameter generation."""
    live_mexc_spot_feed = init_req_feed()

    path, params, extra_data = live_mexc_spot_feed._make_order(
        symbol="BTCUSDT",
        vol="0.001",
        price="50000",
        order_type="buy-limit",
    )

    assert "order" in path.lower()
    assert params["side"] == "BUY"
    assert params["type"] == "LIMIT"
    assert params["quantity"] == "0.001"
    assert params["price"] == "50000"


def test_mexc_cancel_order_params():
    """Test MEXC cancel order parameter generation."""
    live_mexc_spot_feed = init_req_feed()

    path, params, extra_data = live_mexc_spot_feed._cancel_order(
        symbol="BTCUSDT",
        order_id="123456",
    )

    # MEXC uses DELETE /api/v3/order for cancel
    assert "DELETE" in path.upper()
    assert "/api/v3/order" in path
    assert params["symbol"] == "BTCUSDT"
    assert params["orderId"] == "123456"


if __name__ == "__main__":
    # Run tests manually for development
    test_mexc_req_server_time()
    test_mexc_req_tick_data()
    test_mexc_req_kline_data()
    test_mexc_req_orderbook_data()
    test_mexc_ticker_normalize_function()
    test_mexc_orderbook_normalize_function()
    test_mexc_exchange_data()
    test_mexc_registration()
    test_mexc_order_params()
    test_mexc_cancel_order_params()
    print("All MEXC tests passed!")
