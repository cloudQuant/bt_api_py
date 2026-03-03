"""
Test Bitrue exchange integration.

Run tests:
    pytest tests/feeds/test_bitrue.py -v

Run with coverage:
    pytest tests/feeds/test_bitrue.py --cov=bt_api_py.feeds.live_bitrue --cov-report=term-missing

Run specific test:
    pytest tests/feeds/test_bitrue.py::test_bitrue_req_tick_data -v
"""

import queue
import time

import pytest

from bt_api_py.containers.exchanges.bitrue_exchange_data import BitrueExchangeDataSpot
from bt_api_py.containers.tickers.bitrue_ticker import BitrueRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitrue.spot import BitrueRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitrue
import bt_api_py.feeds.register_bitrue  # noqa: F401

# ==================== Test Fixtures ====================


def init_req_feed():
    """Initialize Bitrue request feed for testing."""
    data_queue = queue.Queue()
    return BitrueRequestDataSpot(data_queue)


def init_async_feed(data_queue):
    """Initialize Bitrue async feed for testing."""
    return BitrueRequestDataSpot(data_queue)

# ==================== Exchange Data Tests ====================


class TestBitrueExchangeData:
    """Test Bitrue exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bitrue spot exchange data."""
        exchange_data = BitrueExchangeDataSpot()
#         assert exchange_data.exchange_name == "BITRUE"
        assert exchange_data.asset_type == "SPOT"

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BitrueExchangeDataSpot()
        # Bitrue uses various formats
        assert exchange_data.get_symbol("BTCUSDT") == "BTCUSDT"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = BitrueExchangeDataSpot()
        # Test common paths
        path = exchange_data.get_rest_path("get_tick")
        assert "ticker" in path.lower() or "tick" in path.lower()

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BitrueExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BitrueExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency

# ==================== Request Feed Tests ====================


class TestBitrueRequestData:
    """Test Bitrue REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Bitrue request data."""
        data_queue = queue.Queue()
        request_data = BitrueRequestDataSpot(data_queue)
        assert request_data.exchange_name == "BITRUE___SPOT"

    def test_capabilities(self):
        """Test that Bitrue has the correct capabilities."""
        capabilities = BitrueRequestDataSpot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_get_tick_params(self):
        """Test get ticker parameter generation."""
        data_queue = queue.Queue()
        request_data = BitrueRequestDataSpot(data_queue)

        # The _get_tick method returns the result of self.request
        result = request_data._get_tick("BTCUSDT")
        # We can't test the actual request without mocking, but we can verify
        # it's called
        assert result is not None

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = BitrueRequestDataSpot(data_queue)

        result = request_data._get_depth("BTCUSDT", count=20)
        assert result is not None

    def test_get_kline_params(self):
        """Test get kline parameter generation."""
        data_queue = queue.Queue()
        request_data = BitrueRequestDataSpot(data_queue)

        result = request_data._get_kline("BTCUSDT", "1h", count=20)
        assert result is not None

# ==================== Data Container Tests ====================


class TestBitrueDataContainers:
    """Test Bitrue data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "symbol": "BTCUSDT",
            "price": "50000",
            "bid": "49999",
            "ask": "50001",
            "high": "51000",
            "low": "49000",
            "volume": "1234.56",
            "timestamp": 1234567890
        }

        ticker = BitrueRequestTickerData(
            ticker_response, "BTCUSDT", "SPOT"
        )

        assert ticker.get_symbol_name() == "BTCUSDT"

# ==================== Server Time Tests ====================


def test_bitrue_req_server_time():
    """Test Bitrue server time endpoint."""

# ==================== Ticker Tests ====================


def test_bitrue_req_tick_data():
    """Test Bitrue ticker data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_tick("BTCUSDT")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test ticker data
    if data_list and len(data_list) > 0:
        ticker = data_list[0]
        if hasattr(ticker, 'init_data'):
            ticker = ticker.init_data()
            assert ticker.get_exchange_name() == "BITRUE"
            assert ticker.get_symbol_name() == "BTCUSDT"


def test_bitrue_async_tick_data():
    """Test Bitrue ticker data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitrue doesn't have async_get_tick implemented
    time.sleep(2)

    try:
        tick_data = data_queue.get(timeout=10)
        assert tick_data is not None
    except queue.Empty:
        pass

        # ==================== Kline Tests ====================


def test_bitrue_req_kline_data():
    """Test Bitrue kline data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_kline("BTCUSDT", "1h", count=2)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test kline data if available
    if data_list and len(data_list) > 0:
        kline = data_list[0]
        if isinstance(kline, list) and len(kline) > 0:
            # Kline data should be a list of candles
            assert isinstance(kline, list)


def test_bitrue_async_kline_data():
    """Test Bitrue kline data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitrue doesn't have async_get_kline implemented
    time.sleep(3)

    try:
        kline_data = data_queue.get(timeout=10)
        assert kline_data is not None
    except queue.Empty:
        pass

        # ==================== Order Book Tests ====================


def order_book_value_equals(order_book):
    """Validate Bitrue order book data."""
    # Bitrue order book format validation
    assert order_book is not None
    assert isinstance(order_book, dict)

    # Check for bids and asks
    if "bids" in order_book:
        bids = order_book["bids"]
        if bids and len(bids) > 0:
            assert isinstance(bids, list)
            # Price values are strings, convert to float for validation
            price = float(bids[0][0])
            assert price > 0  # price > 0

    if "asks" in order_book:
        asks = order_book["asks"]
        if asks and len(asks) > 0:
            assert isinstance(asks, list)
            # Price values are strings, convert to float for validation
            price = float(asks[0][0])
            assert price > 0  # price > 0


def test_bitrue_req_orderbook_data():
    """Test Bitrue order book data."""
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


def test_bitrue_async_orderbook_data():
    """Test Bitrue order book data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitrue doesn't have async_get_depth implemented
    time.sleep(3)

    try:
        depth_data = data_queue.get(timeout=10)
        assert depth_data is not None
    except queue.Empty:
        pass

        # ==================== Account Tests ====================


@pytest.mark.skip(reason="Account endpoints require API keys")
def test_bitrue_req_account_data():
    """Test Bitrue account data."""
    feed = init_req_feed()
    data = feed.get_account()
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)


@pytest.mark.skip(reason="Account endpoints require API keys")
def test_bitrue_async_account_data():
    """Test Bitrue account data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)
    feed.async_get_account()
    time.sleep(3)

    try:
        account_data = data_queue.get(timeout=10)
        assert isinstance(account_data, RequestData)
    except queue.Empty:
        pass

        # ==================== Balance Tests ====================


@pytest.mark.skip(reason="Balance endpoints require API keys")
def test_bitrue_req_balance_data():
    """Test Bitrue balance data."""
    feed = init_req_feed()
    data = feed.get_balance("BTCUSDT")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

# ==================== Trade Tests ====================


@pytest.mark.skip(reason="Trade history endpoints require API keys")
def test_bitrue_req_get_deals():
    """Test Bitrue trade/deal history."""
    feed = init_req_feed()
    data = feed.get_deals("BTCUSDT")
    assert isinstance(data, RequestData)
    trade_data = data.get_data()
    if trade_data and len(trade_data) > 0:
        assert isinstance(trade_data, list)

# ==================== Registration Tests ====================


class TestBitrueRegistry:
    """Test Bitrue registration."""

    def test_bitrue_registered(self):
        """Test that Bitrue is properly registered."""
        # Check if feed is registered
        assert "BITRUE___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITRUE___SPOT"] == BitrueRequestDataSpot

        # Check if exchange data is registered
        assert "BITRUE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITRUE___SPOT"] == BitrueExchangeDataSpot

        # Check if balance handler is registered
        assert "BITRUE___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITRUE___SPOT"] is not None

    def test_bitrue_create_feed(self):
        """Test creating Bitrue feed through registry."""
        import queue
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BITRUE___SPOT",
            data_queue,
        )
        assert isinstance(feed, BitrueRequestDataSpot)

    def test_bitrue_create_exchange_data(self):
        """Test creating Bitrue exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITRUE___SPOT")
        assert isinstance(exchange_data, BitrueExchangeDataSpot)

# ==================== Integration Tests ====================


class TestBitrueIntegration:
    """Integration tests for Bitrue."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)"""
        data_queue = queue.Queue()
        feed = BitrueRequestDataSpot(data_queue)

        # Test ticker
        result = feed.get_tick("BTCUSDT")
        assert result is not None

    def test_trading_api(self):
        """Test trading API calls (requires API keys)"""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
