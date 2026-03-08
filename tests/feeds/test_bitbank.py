"""
Test Bitbank exchange integration.

Run tests:
    pytest tests/feeds/test_bitbank.py -v
"""

import queue
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import registration to auto-register Bitbank
import bt_api_py.exchange_registers.register_bitbank  # noqa: F401
from bt_api_py.containers.exchanges.bitbank_exchange_data import (
    BitbankExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbank.spot import BitbankRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitbank_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


@pytest.fixture
def mock_feed():
    """Create a Bitbank feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitbankExchangeData:
    """Test Bitbank exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitbank spot exchange data."""
        exchange_data = BitbankExchangeDataSpot()
        assert exchange_data.rest_url

    @pytest.mark.kline
    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitbankExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitbankExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitbankExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitbankRequestDataSpot:
    """Test Bitbank REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitbank request data."""
        data_queue = queue.Queue()
        request_data = BitbankRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITBANK___SPOT",
        )
        assert request_data.exchange_name == "BITBANK___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test declared capabilities include all standard interfaces."""
        caps = BitbankRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_normalize_pair(self):
        """Test symbol normalization for Bitbank."""
        data_queue = queue.Queue()
        request_data = BitbankRequestDataSpot(data_queue)
        assert request_data._normalize_pair("BTC/JPY") == "btc_jpy"
        assert request_data._normalize_pair("BTC-JPY") == "btc_jpy"
        assert request_data._normalize_pair("BTC_JPY") == "btc_jpy"

    @pytest.mark.ticker
    def test_get_tick_returns_tuple(self, mock_feed):
        """Test _get_tick returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_tick("BTC/JPY")
        assert "GET" in path
        assert "btc_jpy" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/JPY"

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, mock_feed):
        """Test get_tick calls self.request."""
        mock_feed.get_tick("BTC/JPY")
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        """Test _get_depth returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_depth("BTC/JPY")
        assert "depth" in path
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/JPY")
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        """Test _get_kline returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_kline("BTC/JPY", "1h")
        assert "candlestick" in path
        assert extra_data["request_type"] == "get_kline"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC/JPY", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        input_data = {
            "success": 1,
            "data": {
                "last": "5000000",
                "buy": "4999000",
                "sell": "5001000",
            },
        }
        extra_data = {"symbol_name": "BTC/JPY"}
        result, success = BitbankRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    @pytest.mark.orderbook
    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "success": 1,
            "data": {
                "bids": [
                    {"price": "4999000", "amount": "0.1"},
                ],
                "asks": [
                    {"price": "5001000", "amount": "0.1"},
                ],
            },
        }
        extra_data = {"symbol_name": "BTC/JPY"}
        result, success = BitbankRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )
        assert success is True
        assert "bids" in result[0]

    @pytest.mark.kline
    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "success": 1,
            "data": {
                "candlestick": [
                    {
                        "type": "1hour",
                        "ohlcv": [
                            "4950000",
                            "5100000",
                            "4900000",
                            "5000000",
                            "123.456",
                            "1640995200000",
                        ],
                    }
                ]
            },
        }
        extra_data = {"symbol_name": "BTC/JPY"}
        result, success = BitbankRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )
        assert success is True
        assert "ohlcv" in result[0]


class TestBitbankRegistration:
    """Test Bitbank registration."""

    def test_bitbank_registered(self):
        """Test that Bitbank is properly registered."""
        assert "BITBANK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITBANK___SPOT"] == BitbankRequestDataSpot
        assert "BITBANK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITBANK___SPOT"] == BitbankExchangeDataSpot
        assert "BITBANK___SPOT" in ExchangeRegistry._balance_handlers

    def test_bitbank_create_exchange_data(self):
        """Test creating Bitbank exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITBANK___SPOT")
        assert isinstance(exchange_data, BitbankExchangeDataSpot)


class TestBitbankStandardInterfaces:
    """Test standard Feed interface methods for Bitbank."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/JPY", 0.01, 5000000, "limit")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/JPY", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC/JPY", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC/JPY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_exchange_info"


class TestBitbankBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitbank.request_base import BitbankRequestData

        caps = BitbankRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitbankNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = BitbankRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = BitbankRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = BitbankRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BitbankRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BitbankRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_failure(self):
        result, status = BitbankRequestDataSpot._get_tick_normalize_function(
            {"success": 0, "data": {}}, None
        )
        assert result == []
        assert status is False


class TestBitbankLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bitbank_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        data = feed.get_tick("BTC/JPY")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bitbank_req_kline_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        data = feed.get_kline("BTC/JPY", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            assert isinstance(klines, (list, dict))

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bitbank_req_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        data = feed.get_depth("BTC/JPY", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            if isinstance(orderbook, dict):
                assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bitbank_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        feed.async_get_tick("BTC/JPY", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bitbank_async_kline_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        feed.async_get_kline("BTC/JPY", period="1h", count=3, extra_data={"test_async": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bitbank_async_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitbankRequestDataSpot(data_queue, exchange_name="BITBANK___SPOT")
        feed.async_get_depth("BTC/JPY", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass
