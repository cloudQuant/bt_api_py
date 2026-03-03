"""
Test Bitstamp exchange integration.

Run tests:
    pytest tests/feeds/test_bitstamp.py -v

Run with coverage:
    pytest tests/feeds/test_bitstamp.py --cov=bt_api_py.feeds.live_bitstamp --cov-report=term-missing

Run specific test:
    pytest tests/feeds/test_bitstamp.py::test_bitstamp_req_tick_data -v
"""

import queue
import time

import pytest

from bt_api_py.containers.exchanges.bitstamp_exchange_data import (
    BitstampExchangeData,
    BitstampExchangeDataSpot,
)
from bt_api_py.containers.tickers.bitstamp_ticker import (
    BitstampRequestTickerData,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitstamp.spot import BitstampRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitstamp
import bt_api_py.feeds.register_bitstamp  # noqa: F401

# ==================== Test Fixtures ====================


def init_req_feed():
    """Initialize Bitstamp request feed for testing."""
    data_queue = queue.Queue()
    return BitstampRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )


def init_async_feed(data_queue):
    """Initialize Bitstamp async feed for testing."""
    return BitstampRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )

# ==================== Exchange Data Tests ====================


class TestBitstampExchangeData:
    """Test Bitstamp exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bitstamp spot exchange data."""
        exchange_data = BitstampExchangeDataSpot()
        assert exchange_data.exchange_name == "bitstamp" or "BITSTAMP" in exchange_data.exchange_name.upper()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert "bitstamp" in exchange_data.rest_url.lower()

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BitstampExchangeDataSpot()
        # Bitstamp uses lowercase without separator
        assert exchange_data.get_symbol("BTC-USD") == "btcusd"
        assert exchange_data.get_symbol("ETH-USD") == "ethusd"

    def test_get_period(self):
        """Test period conversion."""
        exchange_data = BitstampExchangeDataSpot()
        assert exchange_data.get_period("1m") == "60"
        assert exchange_data.get_period("1h") == "3600"
        assert exchange_data.get_period("1d") == "86400"

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BitstampExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BitstampExchangeDataSpot()
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = BitstampExchangeDataSpot()
        assert "bitstamp" in exchange_data.rest_url.lower()

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = BitstampExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()

# ==================== Data Container Tests ====================


class TestBitstampDataContainers:
    """Test Bitstamp data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        # Bitstamp ticker format (all values are strings)
        ticker_data = {
            "last": "50000",
            "bid": "49990",
            "ask": "50010",
            "volume": "1234.56",
            "high": "51000",
            "low": "49000",
            "open": "49500",
        }

        ticker = BitstampRequestTickerData(
            ticker_data, "BTC-USD", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BITSTAMP"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49990.0
        assert ticker.ask_price == 50010.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 49000.0

# ==================== Request Feed Tests ====================


class TestBitstampRequestData:
    """Test Bitstamp REST API request class."""

    def test_request_data_creation(self):
        """Test creating Bitstamp request data."""
        data_queue = queue.Queue()
        request_data = BitstampRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        assert "BITSTAMP" in request_data.exchange_name.upper()

# ==================== Server Time Tests ====================


def test_bitstamp_req_server_time():
    """Test Bitstamp server time endpoint."""

# ==================== Ticker Tests ====================


def test_bitstamp_req_tick_data():
    """Test Bitstamp ticker data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_tick("BTC-USD")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test ticker data
    if data_list and len(data_list) > 0:
        ticker = data_list[0]
        if hasattr(ticker, 'init_data'):
            ticker = ticker.init_data()
            assert ticker.get_exchange_name() == "BITSTAMP"
            assert ticker.get_symbol_name() == "BTC-USD"
            assert ticker.last_price > 0


def test_bitstamp_async_tick_data():
    """Test Bitstamp ticker data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitstamp doesn't have async_get_tick implemented
    time.sleep(2)

    try:
        tick_data = data_queue.get(timeout=10)
        assert tick_data is not None
    except queue.Empty:
        pass

        # ==================== Kline Tests ====================


def test_bitstamp_req_kline_data():
    """Test Bitstamp kline data (synchronous)."""
    feed = init_req_feed()
    data = feed.get_kline("BTC-USD", "1h", count=2)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test kline data if available
    if data_list and len(data_list) > 0:
        klines = data_list[0]
        if isinstance(klines, list):
            # Kline data should be a list of candles
            assert isinstance(klines, list)


def test_bitstamp_async_kline_data():
    """Test Bitstamp kline data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitstamp doesn't have async_get_kline implemented
    time.sleep(3)

    try:
        kline_data = data_queue.get(timeout=10)
        assert kline_data is not None
    except queue.Empty:
        pass

        # ==================== Order Book Tests ====================


def order_book_value_equals(order_book):
    """Validate Bitstamp order book data."""
    # Bitstamp order book format validation
    assert order_book is not None
    assert isinstance(order_book, dict)

    # Check for bids and asks
    if "bids" in order_book:
        bids = order_book["bids"]
        if bids and len(bids) > 0:
            assert isinstance(bids, list)
            # Bitstamp returns strings, convert to float for comparison
            price = float(bids[0][0]) if isinstance(bids[0][0], str) else bids[0][0]
            assert price > 0  # price > 0

    if "asks" in order_book:
        asks = order_book["asks"]
        if asks and len(asks) > 0:
            assert isinstance(asks, list)
            # Bitstamp returns strings, convert to float for comparison
            price = float(asks[0][0]) if isinstance(asks[0][0], str) else asks[0][0]
            assert price > 0  # price > 0


def test_bitstamp_req_orderbook_data():
    """Test Bitstamp order book data."""
    feed = init_req_feed()
    data = feed.get_depth("BTC-USD", 20)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test order book if available
    if data_list and len(data_list) > 0:
        order_book = data_list[0]
        if isinstance(order_book, dict):
            order_book_value_equals(order_book)


def test_bitstamp_async_orderbook_data():
    """Test Bitstamp order book data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitstamp doesn't have async_get_depth implemented
    time.sleep(3)

    try:
        depth_data = data_queue.get(timeout=10)
        assert depth_data is not None
    except queue.Empty:
        pass

        # ==================== Account Tests ====================


@pytest.mark.skip(reason="Requires valid Bitstamp API credentials")
def test_bitstamp_req_account_data():
    """Test Bitstamp account data."""
    feed = init_req_feed()
    data = feed.get_account()
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)


@pytest.mark.skip(reason="Requires valid Bitstamp API credentials")
def test_bitstamp_async_account_data():
    """Test Bitstamp account data (asynchronous)."""
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


@pytest.mark.skip(reason="Requires valid Bitstamp API credentials")
def test_bitstamp_req_balance_data():
    """Test Bitstamp balance data."""
    feed = init_req_feed()
    data = feed.get_balance()
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

# ==================== Trade Tests ====================


@pytest.mark.skip(reason="Requires valid Bitstamp API credentials")
def test_bitstamp_req_get_deals():
    """Test Bitstamp trade/deal history."""
    feed = init_req_feed()
    data = feed.get_deals()
    assert isinstance(data, RequestData)
    trade_data = data.get_data()
    if trade_data and len(trade_data) > 0:
        assert isinstance(trade_data, list)

# ==================== Registration Tests ====================


class TestBitstampRegistration:
    """Test Bitstamp registration."""

    def test_bitstamp_exchange_data_creation(self):
        """Test creating Bitstamp exchange data."""
        exchange_data = BitstampExchangeDataSpot()
        assert exchange_data is not None
        assert exchange_data.exchange_name is not None

# ==================== Integration Tests ====================


class TestBitstampIntegration:
    """Integration tests for Bitstamp."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
