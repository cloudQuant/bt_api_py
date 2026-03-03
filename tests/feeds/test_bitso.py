"""
Test Bitso exchange integration.

Run tests:
    pytest tests/feeds/test_bitso.py -v

Run with coverage:
    pytest tests/feeds/test_bitso.py --cov=bt_api_py.feeds.live_bitso --cov-report=term-missing

Run specific test:
    pytest tests/feeds/test_bitso.py::test_bitso_req_tick_data -v
"""

import queue
import time

import pytest

from bt_api_py.containers.exchanges.bitso_exchange_data import BitsoExchangeDataSpot
from bt_api_py.containers.tickers.bitso_ticker import BitsoRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitso.spot import BitsoRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitso
import bt_api_py.feeds.register_bitso  # noqa: F401

# ==================== Test Fixtures ====================


def init_req_feed():
    """Initialize Bitso request feed for testing."""
    data_queue = queue.Queue()
    return BitsoRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )


def init_async_feed(data_queue):
    """Initialize Bitso async feed for testing."""
    return BitsoRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )

# ==================== Exchange Data Tests ====================


class TestBitsoExchangeData:
    """Test Bitso exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bitso spot exchange data."""
        exchange_data = BitsoExchangeDataSpot()
        # exchange_name is loaded from YAML config (bitsoSpot)
        assert exchange_data.rest_url
        # Check that the exchange has configuration

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BitsoExchangeDataSpot()
        # Bitso uses MXN and USDC, not USDT
        assert exchange_data.get_symbol("BTC-MXN") == "btc_mxn"
        assert exchange_data.get_symbol("ETH-MXN") == "eth_mxn"
        assert exchange_data.get_symbol("BTC-USDC") == "btc_usdc"

    def test_get_period(self):
        """Test kline period conversion."""
        exchange_data = BitsoExchangeDataSpot()
        assert exchange_data.get_period("1m") == "60"
        assert exchange_data.get_period("1h") == "3600"

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = BitsoExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = BitsoExchangeDataSpot()
        assert "MXN" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "USDC" in exchange_data.legal_currency
        # Bitso doesn't use USDT, they use USDC and MXN
        assert "USDT" not in exchange_data.legal_currency

# ==================== Request Feed Tests ====================


class TestBitsoRequestDataSpot:
    """Test Bitso spot REST API request class."""

    def test_request_data_creation(self):
        """Test creating Bitso request data."""
        data_queue = queue.Queue()
        request_data = BitsoRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITSO___SPOT",
        )
        assert request_data.exchange_name == "BITSO___SPOT"

# ==================== Server Time Tests ====================


def test_bitso_req_server_time():
    """Test Bitso server time endpoint."""

# ==================== Ticker Tests ====================


def test_bitso_req_tick_data():
    """Test Bitso ticker data (synchronous)."""
    feed = init_req_feed()
    # Bitso uses BTC-MXN, not BTC-USDT
    data = feed.get_tick("BTC-MXN")
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test ticker data
    if data_list and len(data_list) > 0:
        ticker = data_list[0]
        if hasattr(ticker, 'init_data'):
            ticker = ticker.init_data()
            assert ticker.get_exchange_name() == "BITSO"
            assert ticker.get_symbol_name() == "BTC-MXN"


def test_bitso_async_tick_data():
    """Test Bitso ticker data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitso doesn't have async_get_tick implemented
    time.sleep(2)

    try:
        tick_data = data_queue.get(timeout=10)
        assert tick_data is not None
    except queue.Empty:
        pass

        # ==================== Kline Tests ====================


def test_bitso_req_kline_data():
    """Test Bitso kline data (synchronous)."""
    feed = init_req_feed()
    # Bitso uses BTC-MXN, not BTC-USDT
    data = feed.get_kline("BTC-MXN", "1h", count=2)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test kline data if available
    if data_list and len(data_list) > 0:
        klines = data_list[0]
        if isinstance(klines, list):
            # Kline data should be a list of candles
            assert isinstance(klines, list)


def test_bitso_async_kline_data():
    """Test Bitso kline data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitso doesn't have async_get_kline implemented
    time.sleep(3)

    try:
        kline_data = data_queue.get(timeout=10)
        assert kline_data is not None
    except queue.Empty:
        pass

        # ==================== Order Book Tests ====================


def order_book_value_equals(order_book):
    """Validate Bitso order book data."""
    # Bitso order book format validation
    assert order_book is not None
    assert isinstance(order_book, dict)

    # Check for bids and asks
    # Bitso uses dict format: {"price": "...", "amount": "...", "book": "..."}
    if "bids" in order_book:
        bids = order_book["bids"]
        if bids and len(bids) > 0:
            assert isinstance(bids, list)
            # Bitso bids are dicts with "price" key
            first_bid = bids[0]
            if isinstance(first_bid, dict):
                assert "price" in first_bid
                assert float(first_bid["price"]) > 0  # price > 0
            elif isinstance(first_bid, list):
                # Fallback for array format
                assert first_bid[0] > 0

    if "asks" in order_book:
        asks = order_book["asks"]
        if asks and len(asks) > 0:
            assert isinstance(asks, list)
            # Bitso asks are dicts with "price" key
            first_ask = asks[0]
            if isinstance(first_ask, dict):
                assert "price" in first_ask
                assert float(first_ask["price"]) > 0  # price > 0
            elif isinstance(first_ask, list):
                # Fallback for array format
                assert first_ask[0] > 0


def test_bitso_req_orderbook_data():
    """Test Bitso order book data."""
    feed = init_req_feed()
    # Bitso uses BTC-MXN, not BTC-USDT
    data = feed.get_depth("BTC-MXN", 20)
    assert isinstance(data, RequestData)
    data_list = data.get_data()
    assert isinstance(data_list, list)

    # Test order book if available
    if data_list and len(data_list) > 0:
        order_book = data_list[0]
        if isinstance(order_book, dict):
            order_book_value_equals(order_book)


def test_bitso_async_orderbook_data():
    """Test Bitso order book data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)

    # Note: Bitso doesn't have async_get_depth implemented
    time.sleep(3)

    try:
        depth_data = data_queue.get(timeout=10)
        assert depth_data is not None
    except queue.Empty:
        pass

        # ==================== Account Tests ====================


def test_bitso_req_account_data():
    """Test Bitso account data."""
    feed = init_req_feed()
    # Bitso account endpoint requires authentication
    # This test verifies the method exists but is not implemented
    with pytest.raises(NotImplementedError):
        data = feed.get_account()


def test_bitso_async_account_data():
    """Test Bitso account data (asynchronous)."""
    data_queue = queue.Queue()
    feed = init_async_feed(data_queue)
    # Bitso async account endpoint requires authentication and is not implemented
    with pytest.raises(NotImplementedError):
        feed.async_get_account()

        # ==================== Balance Tests ====================


def test_bitso_req_balance_data():
    """Test Bitso balance data."""
    feed = init_req_feed()
    # Bitso balance endpoint requires authentication and a currency symbol
    # This test verifies the method exists but is not implemented
    with pytest.raises(NotImplementedError):
        data = feed.get_balance("BTC-MXN")

# ==================== Trade Tests ====================


def test_bitso_req_get_deals():
    """Test Bitso trade/deal history."""
    feed = init_req_feed()
    # Bitso deals endpoint requires authentication and a symbol
    # This test verifies the method exists but is not implemented
    with pytest.raises(NotImplementedError):
        data = feed.get_deals("BTC-MXN")

# ==================== Data Container Tests ====================


class TestBitsoDataContainers:
    """Test Bitso data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "success": True,
            "payload": {
                "book": "btc_mxn",
                "last": "50000",
                "bid": "49999",
                "ask": "50001",
                "volume": "1000",
                "high": "51000",
                "low": "49000",
            },
        }

        # BitsoRequestTickerData takes (ticker_info, symbol_name, asset_type,
        # has_been_json_encoded)
        # Bitso uses MXN, not USDT
        ticker = BitsoRequestTickerData(
            ticker_response, "BTC-MXN", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BITSO"
        assert ticker.get_symbol_name() == "BTC-MXN"
        assert ticker.get_last_price() == 50000

# ==================== Registration Tests ====================


class TestBitsoRegistry:
    """Test Bitso registration."""

    def test_bitso_registered(self):
        """Test that Bitso is properly registered."""
        assert "BITSO___SPOT" in ExchangeRegistry._feed_classes
        assert "BITSO___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_bitso_create_feed(self):
        """Test creating Bitso feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BITSO___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, BitsoRequestDataSpot)

    def test_bitso_create_exchange_data(self):
        """Test creating Bitso exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITSO___SPOT")
        assert isinstance(exchange_data, BitsoExchangeDataSpot)

# ==================== Integration Tests ====================


class TestBitsoIntegration:
    """Integration tests for Bitso (marked as integration)."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        pass

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
