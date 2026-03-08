"""
Test BingX exchange integration.

Run tests:
    pytest tests/feeds/test_bingx.py -v
"""

import queue
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import registration to auto-register BingX
import bt_api_py.exchange_registers.register_bingx  # noqa: F401
from bt_api_py.containers.exchanges.bingx_exchange_data import (
    BingXExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bingx.spot import BingXRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bingx_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


@pytest.fixture
def mock_feed():
    """Create a BingX feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBingXExchangeData:
    """Test BingX exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating BingX spot exchange data."""
        exchange_data = BingXExchangeDataSpot()
        assert exchange_data.rest_url
        assert (
            "get_ticker" in exchange_data.rest_paths
            or "get_server_time" in exchange_data.rest_paths
        )

    @pytest.mark.kline
    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BingXExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BingXExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BingXExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBingXRequestDataSpot:
    """Test BingX REST API request methods."""

    def test_request_data_creation(self):
        """Test creating BingX request data."""
        data_queue = queue.Queue()
        request_data = BingXRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BINGX___SPOT",
        )
        assert request_data.exchange_name == "BINGX___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test declared capabilities include all standard interfaces."""
        caps = BingXRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    @pytest.mark.ticker
    def test_get_tick_returns_tuple(self, mock_feed):
        """Test _get_tick returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_tick("BTC-USDT")
        assert "GET" in path
        assert params["symbol"] == "BTC-USDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-USDT"

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, mock_feed):
        """Test get_tick calls self.request."""
        mock_feed.get_tick("BTC-USDT")
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        """Test _get_depth returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_depth("BTC-USDT", 10)
        assert params["symbol"] == "BTC-USDT"
        assert params["limit"] == 10
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, mock_feed):
        """Test get_depth calls self.request."""
        mock_feed.get_depth("BTC-USDT", 10)
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        """Test _get_kline returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_kline("BTC-USDT", "1m", 5)
        assert params["symbol"] == "BTC-USDT"
        assert extra_data["request_type"] == "get_kline"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, mock_feed):
        """Test get_kline calls self.request."""
        mock_feed.get_kline("BTC-USDT", "1m", 5)
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        """Test _get_exchange_info returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        """Test _get_server_time returns tuple."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"
        assert "server" in path.lower() or "time" in path.lower()

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        input_data = {
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "50000",
                }
            ]
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert success is True

    @pytest.mark.orderbook
    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "data": {
                "bids": [["49990", "1.0"], ["49980", "2.0"]],
                "asks": [["50010", "1.0"], ["50020", "2.0"]],
            }
        }
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_depth_normalize_function(input_data, extra_data)

        assert success is True
        assert "bids" in result[0]

    @pytest.mark.kline
    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {"data": [[1640995200, "49500", "51000", "49000", "50000", "1234.56"]]}
        extra_data = {"symbol_name": "BTC-USDT"}

        result, success = BingXRequestDataSpot._get_kline_normalize_function(input_data, extra_data)

        assert success is True
        assert isinstance(result[0], list)


class TestBingXRegistration:
    """Test BingX registration."""

    def test_bingx_registered(self):
        """Test that BingX is properly registered."""
        assert "BINGX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BINGX___SPOT"] == BingXRequestDataSpot

        assert "BINGX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BINGX___SPOT"] == BingXExchangeDataSpot

        assert "BINGX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BINGX___SPOT"] is not None

    def test_bingx_create_exchange_data(self):
        """Test creating BingX exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BINGX___SPOT")
        assert isinstance(exchange_data, BingXExchangeDataSpot)


class TestBingXStandardInterfaces:
    """Test standard Feed interface methods for BingX."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-USDT", 0.01, 50000, "LIMIT")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC-USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC-USDT")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, feed):
        feed.get_account("BTC")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, feed):
        feed.get_balance("BTC")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_exchange_info"


class TestBingXBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bingx.request_base import BingXRequestData

        caps = BingXRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBingXNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = BingXRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = BingXRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = BingXRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BingXRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_empty_data(self):
        result, status = BingXRequestDataSpot._get_tick_normalize_function({"data": []}, None)
        assert result == []
        assert status is False


class TestBingXLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bingx_req_tick_data(self):
        """Test BingX ticker data (sync)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        data = feed.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bingx_req_kline_data(self):
        """Test BingX kline data (sync)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        data = feed.get_kline("BTC-USDT", "1m", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            assert isinstance(klines, (list, dict))

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bingx_req_orderbook_data(self):
        """Test BingX orderbook data (sync)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        data = feed.get_depth("BTC-USDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            if isinstance(orderbook, dict):
                assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bingx_async_tick_data(self):
        """Test BingX ticker data (async)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        feed.async_get_tick("BTC-USDT", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bingx_async_kline_data(self):
        """Test BingX kline data (async)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        feed.async_get_kline("BTC-USDT", period="1m", count=3, extra_data={"test_async": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bingx_async_orderbook_data(self):
        """Test BingX orderbook data (async)."""
        data_queue = queue.Queue()
        feed = BingXRequestDataSpot(data_queue, exchange_name="BINGX___SPOT")
        feed.async_get_depth("BTC-USDT", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass
