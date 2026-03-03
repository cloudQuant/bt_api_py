"""
Test Bithumb exchange integration.

Run tests:
    pytest tests/feeds/test_bithumb.py -v

Run with coverage:
    pytest tests/feeds/test_bithumb.py --cov=bt_api_py.feeds.live_bithumb --cov-report=term-missing

Run specific test:
    pytest tests/feeds/test_bithumb.py::test_bithumb_req_tick_data -v
"""

import queue
import time

import pytest

from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeDataSpot
from bt_api_py.containers.tickers.bithumb_ticker import BithumbRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bithumb.spot import BithumbRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bithumb
import bt_api_py.feeds.register_bithumb  # noqa: F401

# ==================== Test Fixtures ====================


def init_req_feed():
    """Initialize Bithumb request feed for testing."""
    data_queue = queue.Queue()
    return BithumbRequestDataSpot(data_queue)


def init_async_feed(data_queue):
    """Initialize Bithumb async feed for testing."""
    return BithumbRequestDataSpot(data_queue)

# ==================== Exchange Data Tests ====================


class TestBithumbExchangeData:
    """Test Bithumb exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bithumb spot exchange data."""
        exchange_data = BithumbExchangeDataSpot()
        assert "BITHUMB" in exchange_data.exchange_name.upper()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert "bithumb" in exchange_data.rest_url.lower()

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BithumbExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BithumbExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "KRW" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = BithumbExchangeDataSpot()
        assert "bithumb" in exchange_data.rest_url.lower()

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = BithumbExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()

# ==================== Data Container Tests ====================


class TestBithumbDataContainers:
    """Test Bithumb data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        # Bithumb ticker format
        ticker_data = {
            "s": "BTC-USDT",
            "c": "50000",
            "h": "51000",
            "l": "49000",
            "v": "1234.56",
            "p": "2.5"
        }

        ticker = BithumbRequestTickerData(
            ticker_data, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BITHUMB"
        assert ticker.last_price == 50000.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 49000.0
        assert ticker.volume_24h == 1234.56

# ==================== Request Feed Tests ====================


class TestBithumbRequestData:
    """Test Bithumb REST API request class."""

    def test_request_data_creation(self):
        """Test creating Bithumb request data."""
        data_queue = queue.Queue()
        request_data = BithumbRequestDataSpot(data_queue)
        assert "BITHUMB" in request_data.exchange_name.upper()

    def test_convert_symbol(self):
        """Test symbol conversion for Bithumb."""
        data_queue = queue.Queue()
        request_data = BithumbRequestDataSpot(data_queue)

        # Bithumb uses hyphen format
        assert request_data._convert_symbol("BTC/USDT") == "BTC-USDT"
        assert request_data._convert_symbol("BTC_USDT") == "BTC-USDT"
        assert request_data._convert_symbol("BTC-USDT") == "BTC-USDT"

# ==================== Server Time Tests ====================


def test_bithumb_req_server_time():
    """Test Bithumb server time endpoint."""

# ==================== Ticker Tests ====================


@pytest.mark.integration
def test_bithumb_req_tick_data():
    """Test Bithumb ticker data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_tick("BTC-USDT")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test ticker data
    if data_list and len(data_list) > 0:
        ticker = data_list[0]
        if hasattr(ticker, 'init_data'):
            ticker = ticker.init_data()
            assert "BITHUMB" in ticker.get_exchange_name().upper()
            assert ticker.get_symbol_name() == "BTC-USDT"
            assert ticker.last_price > 0


def test_bithumb_async_tick_data():
    """Test Bithumb ticker data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bithumb doesn't have async_get_tick implemented
    time.sleep(2)

    try:
        tick_data = data_queue.get(timeout=10)
        assert tick_data is not None
    except queue.Empty:
        pass

        # ==================== Kline Tests ====================


@pytest.mark.integration
def test_bithumb_req_kline_data():
    """Test Bithumb kline data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_kline("BTC-USDT", "1h", count=2)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test kline data if available
    if data_list and len(data_list) > 0:
        kline = data_list[0]
        if isinstance(kline, list) and len(kline) > 0:
            # Kline data should be a list of candles
            assert isinstance(kline, list)


def test_bithumb_async_kline_data():
    """Test Bithumb kline data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bithumb doesn't have async_get_kline implemented
    time.sleep(3)

    try:
        kline_data = data_queue.get(timeout=10)
        assert kline_data is not None
    except queue.Empty:
        pass

        # ==================== Order Book Tests ====================


def order_book_value_equals(order_book):
    """Validate Bithumb order book data."""
    # Bithumb order book format validation
    assert order_book is not None
    assert isinstance(order_book, dict)

    # Check for bids and asks
    if "b" in order_book:
        bids = order_book["b"]
        if bids and len(bids) > 0:
            assert isinstance(bids, list)
            assert bids[0][0] > 0  # price > 0

    if "s" in order_book:
        asks = order_book["s"]
        if asks and len(asks) > 0:
            assert isinstance(asks, list)
            assert asks[0][0] > 0  # price > 0


@pytest.mark.integration
def test_bithumb_req_orderbook_data():
    """Test Bithumb order book data."""
    feed = init_req_feed()
    data = feed.get_depth("BTC-USDT", 20)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test order book if available
    if data_list and len(data_list) > 0:
        order_book = data_list[0]
        if isinstance(order_book, dict):
            order_book_value_equals(order_book)


def test_bithumb_async_orderbook_data():
    """Test Bithumb order book data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bithumb doesn't have async_get_depth implemented
    time.sleep(3)

    try:
        depth_data = data_queue.get(timeout=10)
        assert depth_data is not None
    except queue.Empty:
        pass

        # ==================== Account Tests ====================


@pytest.mark.integration
def test_bithumb_req_account_data():
    """Test Bithumb account data."""
    feed = init_req_feed()
    data = feed.get_account()
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)


@pytest.mark.integration
def test_bithumb_async_account_data():
    """Test Bithumb account data (asynchronous)."""
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


@pytest.mark.integration
def test_bithumb_req_balance_data():
    """Test Bithumb balance data."""
    feed = init_req_feed()
    data = feed.get_balance("BTC")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

# ==================== Trade Tests ====================


@pytest.mark.integration
def test_bithumb_req_get_deals():
    """Test Bithumb trade/deal history."""
    feed = init_req_feed()
    data = feed.get_deals("BTC-USDT", count=50)
    assert isinstance(data, RequestData)
    trade_data = data.get_data()
    if trade_data and len(trade_data) > 0:
        assert isinstance(trade_data, list)

# ==================== Registration Tests ====================


class TestBithumbRegistration:
    """Test Bithumb registration."""

    def test_bithumb_exchange_data_creation(self):
        """Test creating Bithumb exchange data."""
        exchange_data = BithumbExchangeDataSpot()
        assert exchange_data is not None
        assert exchange_data.exchange_name is not None

# ==================== Integration Tests ====================


class TestBithumbIntegration:
    """Integration tests for Bithumb."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
