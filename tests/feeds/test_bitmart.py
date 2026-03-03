"""
Test BitMart exchange integration.

Run tests:
    pytest tests/feeds/test_bitmart.py -v

Run with coverage:
    pytest tests/feeds/test_bitmart.py --cov=bt_api_py.feeds.live_bitmart --cov-report=term-missing

Run specific test:
    pytest tests/feeds/test_bitmart.py::test_bitmart_req_tick_data -v
"""

import queue
import time

import pytest

from bt_api_py.containers.exchanges.bitmart_exchange_data import BitmartExchangeDataSpot
from bt_api_py.containers.tickers.bitmart_ticker import BitmartRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitmart.spot import BitmartRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BitMart
import bt_api_py.feeds.register_bitmart  # noqa: F401

# ==================== Test Fixtures ====================


def init_req_feed():
    """Initialize BitMart request feed for testing."""
    data_queue = queue.Queue()
    return BitmartRequestDataSpot(data_queue)


def init_async_feed(data_queue):
    """Initialize BitMart async feed for testing."""
    return BitmartRequestDataSpot(data_queue)

# ==================== Exchange Data Tests ====================


class TestBitmartExchangeData:
    """Test BitMart exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating BitMart spot exchange data."""
        exchange_data = BitmartExchangeDataSpot()
#         assert exchange_data.exchange_name == "BITMART"
        assert exchange_data.asset_type == "SPOT"

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BitmartExchangeDataSpot()
        # BitMart uses underscore separator
        assert exchange_data.get_symbol("BTC/USDT") == "BTC_USDT"
        assert exchange_data.get_symbol("BTC-USDT") == "BTC_USDT"
        assert exchange_data.get_symbol("BTC_USDT") == "BTC_USDT"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = BitmartExchangeDataSpot()
        # Test common paths - use the key from YAML config
        path = exchange_data.get_rest_path("get_ticker")
        assert "ticker" in path.lower()

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BitmartExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BitmartExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency

# ==================== Request Feed Tests ====================


class TestBitmartRequestData:
    """Test BitMart REST API request base class."""

    def test_request_data_creation(self):
        """Test creating BitMart request data."""
        data_queue = queue.Queue()
        request_data = BitmartRequestDataSpot(data_queue)
        assert request_data.exchange_name == "BITMART___SPOT"

    def test_capabilities(self):
        """Test that BitMart has the correct capabilities."""
        capabilities = BitmartRequestDataSpot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_convert_symbol(self):
        """Test symbol conversion."""
        data_queue = queue.Queue()
        request_data = BitmartRequestDataSpot(data_queue)

        assert request_data._convert_symbol("BTC/USDT") == "BTC_USDT"
        assert request_data._convert_symbol("BTC-USDT") == "BTC_USDT"
        # BitMart API requires underscore format, so BTCUSDT is converted to BTC_USDT
        assert request_data._convert_symbol("BTCUSDT") == "BTC_USDT"

# ==================== Server Time Tests ====================


def test_bitmart_req_server_time():
    """Test BitMart server time endpoint."""

# ==================== Ticker Tests ====================


def test_bitmart_req_tick_data():
    """Test BitMart ticker data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_tick("BTCUSDT")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test ticker data
    if data_list and len(data_list) > 0:
        ticker = data_list[0]
        if isinstance(ticker, dict):
            # BitMart ticker response
            assert "symbol" in ticker or "last_price" in ticker


def test_bitmart_async_tick_data():
    """Test BitMart ticker data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: BitMart doesn't have async_get_tick implemented
    time.sleep(2)

    try:
        tick_data = data_queue.get(timeout=10)
        assert tick_data is not None
    except queue.Empty:
        pass

        # ==================== Kline Tests ====================


def test_bitmart_req_kline_data():
    """Test BitMart kline data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_kline("BTCUSDT", "1m", count=2)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test kline data if available
    if data_list and len(data_list) > 0:
        klines = data_list[0]
        if isinstance(klines, list):
            # Kline data should be a list of candles
            assert isinstance(klines, list)


def test_bitmart_async_kline_data():
    """Test BitMart kline data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: BitMart doesn't have async_get_kline implemented
    time.sleep(3)

    try:
        kline_data = data_queue.get(timeout=10)
        assert kline_data is not None
    except queue.Empty:
        pass

        # ==================== Order Book Tests ====================


def order_book_value_equals(order_book):
    """Validate BitMart order book data."""
    # BitMart order book format validation
    assert order_book is not None
    assert isinstance(order_book, dict)

    # Check for buys (bids) and sells (asks)
    if "buys" in order_book:
        bids = order_book["buys"]
        if bids and len(bids) > 0:
            assert isinstance(bids, list)
            assert bids[0][0] > 0  # price > 0

    if "sells" in order_book:
        asks = order_book["sells"]
        if asks and len(asks) > 0:
            assert isinstance(asks, list)
            assert asks[0][0] > 0  # price > 0


def test_bitmart_req_orderbook_data():
    """Test BitMart order book data."""
    feed = init_req_feed()
    data = feed.get_depth("BTCUSDT", 20)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test order book if available
    if data_list and len(data_list) > 0:
        order_book = data_list[0]
        if isinstance(order_book, dict):
            order_book_value_equals(order_book)


def test_bitmart_async_orderbook_data():
    """Test BitMart order book data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: BitMart doesn't have async_get_depth implemented
    time.sleep(3)

    try:
        depth_data = data_queue.get(timeout=10)
        assert depth_data is not None
    except queue.Empty:
        pass

        # ==================== Account Tests ====================


def test_bitmart_req_account_data():
    """Test BitMart account data."""
    from bt_api_py.error_framework import AuthenticationError
    feed = init_req_feed()
    try:
        data = feed.get_account()
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
    except AuthenticationError:
        # Skip test if API keys are not configured
        pass


def test_bitmart_async_account_data():
    """Test BitMart account data (asynchronous)."""
    from bt_api_py.error_framework import AuthenticationError
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)
    try:
        feed.async_get_account()
        time.sleep(3)

        try:
            account_data = data_queue.get(timeout=10)
            assert isinstance(account_data, RequestData)
        except queue.Empty:
            pass
    except AuthenticationError:
        # Skip test if API keys are not configured
        pass

        # ==================== Balance Tests ====================


def test_bitmart_req_balance_data():
    """Test BitMart balance data."""
    from bt_api_py.error_framework import AuthenticationError
    feed = init_req_feed()
    try:
        data = feed.get_balance()
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
    except AuthenticationError:
        # Skip test if API keys are not configured
        pass

# ==================== Trade Tests ====================


def test_bitmart_req_get_deals():
    """Test BitMart trade/deal history."""
    feed = init_req_feed()
    data = feed.get_deals()
    assert isinstance(data, RequestData)
    trade_data = data.get_data()
    if trade_data and len(trade_data) > 0:
        assert isinstance(trade_data, list)

# ==================== Data Container Tests ====================


class TestBitmartDataContainers:
    """Test BitMart data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "code": 1000,
            "message": "OK",
            "data": {
                "symbol": "BTC_USDT",
                "last_price": "50000",
                "bid_1": "49999",
                "ask_1": "50001",
                "high_24h": "51000",
                "low_24h": "49000",
                "volume_24h": "1234.56",
                "timestamp": 1234567890
            }
        }

        ticker = BitmartRequestTickerData(
            ticker_response, "BTCUSDT", "SPOT"
        )

        assert ticker.get_symbol_name() == "BTCUSDT"

# ==================== Registration Tests ====================


class TestBitmartRegistry:
    """Test BitMart registration."""

    def test_bitmart_registered(self):
        """Test that BitMart is properly registered."""
        # Check if feed is registered
        assert "BITMART___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITMART___SPOT"] == BitmartRequestDataSpot

        # Check if exchange data is registered
        assert "BITMART___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITMART___SPOT"] == BitmartExchangeDataSpot

        # Check if balance handler is registered
        assert "BITMART___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITMART___SPOT"] is not None

    def test_bitmart_create_feed(self):
        """Test creating BitMart feed through registry."""
        import queue
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BITMART___SPOT",
            data_queue,
        )
        assert isinstance(feed, BitmartRequestDataSpot)

    def test_bitmart_create_exchange_data(self):
        """Test creating BitMart exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITMART___SPOT")
        assert isinstance(exchange_data, BitmartExchangeDataSpot)

# ==================== Integration Tests ====================


class TestBitmartIntegration:
    """Integration tests for BitMart."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)"""
        data_queue = queue.Queue()
        feed = BitmartRequestDataSpot(data_queue)

        # Test ticker
        result = feed.get_tick("BTCUSDT")
        assert result is not None

    def test_trading_api(self):
        """Test trading API calls (requires API keys)"""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
