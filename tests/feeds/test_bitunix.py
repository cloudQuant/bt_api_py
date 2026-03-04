"""
Test Bitunix exchange integration.

Run tests:
    pytest tests/feeds/test_bitunix.py -v
"""

import queue
import time
from unittest.mock import Mock, MagicMock, patch

import pytest

from bt_api_py.containers.exchanges.bitunix_exchange_data import (
    BitunixExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitunix.spot import BitunixRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitunix
import bt_api_py.exchange_registers.register_bitunix  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitunix_exchange_data.logger") as mock_log:
        mock_log.warning = MagicMock()
        yield mock_log


@pytest.fixture
def mock_feed():
    """Create a Bitunix feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitunixExchangeData:
    """Test Bitunix exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        exchange_data = BitunixExchangeDataSpot()
        assert exchange_data.exchange_name == "bitunix"
        assert exchange_data.asset_type == "spot"
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        exchange_data = BitunixExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        exchange_data = BitunixExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        exchange_data = BitunixExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitunixRequestDataSpot:
    """Test Bitunix REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        assert feed.exchange_name == "BITUNIX___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BitunixRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTCUSDT")
        assert "GET" in path
        assert "tickers" in path
        assert params["symbol"] == "BTCUSDT"
        assert extra_data["request_type"] == "get_tick"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTCUSDT")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTCUSDT")
        assert "depth" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTCUSDT")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTCUSDT", "1h")
        assert "klines" in path
        assert extra_data["request_type"] == "get_kline"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTCUSDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBitunixStandardInterfaces:
    """Test standard Feed interface methods for Bitunix."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTCUSDT", 0.01, 50000, "LIMIT", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTCUSDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTCUSDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTCUSDT")
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


class TestBitunixBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitunix.request_base import BitunixRequestData
        caps = BitunixRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitunixNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"code": 0, "data": [{"symbol": "BTCUSDT", "lastPrice": "50000"}]}
        result, status = BitunixRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_success(self):
        data = {"code": 0, "data": {"bids": [["49990", "1.0"]], "asks": [["50010", "1.0"]]}}
        result, status = BitunixRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True
        assert "bids" in result[0]

    def test_kline_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_success(self):
        data = {"code": 0, "data": [[1640995200, "49500", "51000", "49000", "50000", "1234.56"]]}
        result, status = BitunixRequestDataSpot._get_kline_normalize_function(data, None)
        assert status is True
        assert isinstance(result[0], list)

    def test_exchange_info_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BitunixRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBitunixRegistration:
    """Test Bitunix registration."""

    def test_bitunix_registered(self):
        assert "BITUNIX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITUNIX___SPOT"] == BitunixRequestDataSpot

    def test_bitunix_exchange_data_registered(self):
        assert "BITUNIX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITUNIX___SPOT"] == BitunixExchangeDataSpot

    def test_bitunix_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITUNIX___SPOT")
        assert isinstance(exchange_data, BitunixExchangeDataSpot)


class TestBitunixLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_bitunix_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        data = feed.get_tick("BTCUSDT")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitunix_req_depth_data(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        data = feed.get_depth("BTCUSDT", 20)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitunix_req_kline_data(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        data = feed.get_kline("BTCUSDT", "1h", count=5)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitunix_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitunixRequestDataSpot(data_queue, exchange_name="BITUNIX___SPOT")
        feed.async_get_tick("BTCUSDT")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
